"""OTel 初始化（方案 A：OpenLLMetry 自动埋点 + 手动业务 span 双层）。

contracts/observability.md §1：
- ``enabled=false`` → 零初始化，返回 None（主流程无感知，FR-010）。
- instrumentors try-import：未安装/导入失败 → 优雅降级为仅手动业务 span。
- ``BatchSpanProcessor`` 异步批量导出（操作文档 12，禁止 SimpleSpanProcessor）；
  ``atexit`` 注册 shutdown flush。
- 内容开关：``trace_content=false`` 时置 ``TRACELOOP_TRACE_CONTENT=false``
  （不采集 prompt/completion，操作文档 6.2），token/耗时等指标不受影响。

手动业务 span 装饰器 ``traced_node`` 在 Phase 3（US1）补充。
"""

import atexit
import functools
import logging
import os
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_SERVICE = "gsagent"
_DEFAULT_OTLP = "http://localhost:4318"
_VERSION = "0.1.0"


def setup_telemetry(config: Optional[Dict[str, Any]] = None) -> Any:
    """初始化 OTel；未启用返回 None，启用返回 ``Tracer``。

    参数:
        config: ``observability`` 配置段 dict（enabled/service_name/otlp_endpoint/
            trace_content/sample_ratio）。None 或 enabled=false → 零初始化。
    """
    cfg = config or {}
    if not cfg.get("enabled", False):
        return None

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("opentelemetry SDK 未安装，可观测降级为禁用（FR-010）。")
        return None

    service_name = cfg.get("service_name") or _DEFAULT_SERVICE
    otlp_endpoint = cfg.get("otlp_endpoint") or _DEFAULT_OTLP
    sample_ratio = float(cfg.get("sample_ratio", 1.0) or 1.0)

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": _VERSION,
            "deployment.environment": os.getenv("AGENT_ENV", "development"),
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces")
        )
    )
    trace.set_tracer_provider(provider)

    # 内容开关（contracts/observability.md §1）：默认不采集 prompt/completion
    if not cfg.get("trace_content", False):
        os.environ.setdefault("TRACELOOP_TRACE_CONTENT", "false")

    # 采样率：全量默认；高流量可降（操作文档 12）
    if sample_ratio < 1.0:
        from opentelemetry.sdk.trace.sampling import (
            ParentBasedTraceIdRatio,
            TraceIdRatioBased,
        )

        provider = TracerProvider(
            resource=resource,
            sampler=ParentBasedTraceIdRatio(TraceIdRatioBased(sample_ratio)),
        )
        trace.set_tracer_provider(provider)

    _instrument(provider)

    atexit.register(provider.shutdown)
    return trace.get_tracer(service_name)


def traced_node(
    tracer: Any, name: str
) -> Callable[[Callable[[Dict[str, Any]], Dict[str, Any]]], Callable[[Dict[str, Any]], Dict[str, Any]]]:
    """手动业务 span 装饰器工厂（操作文档 9 方案 D）。

    为 LangGraph 节点包裹一层 ``node.<name>`` 业务 span，属性含
    ``langgraph.node`` 与 ``node.output_keys``（contracts/observability.md §2）。
    ``tracer`` 为 None（未启用 OTel）时退化为透传——零开销、主流程无感知（FR-010）。

    用法（graph.py 组装时按需应用）::

        node_fn = traced_node(loop._tracer, "agent")(agent_node(loop))
    """

    if tracer is None:
        return lambda fn: fn

    from opentelemetry import trace as otel_trace

    def decorator(
        fn: Callable[[Dict[str, Any]], Dict[str, Any]]
    ) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
        @functools.wraps(fn)
        def wrapper(state: Dict[str, Any]) -> Dict[str, Any]:
            with tracer.start_as_current_span(f"node.{name}") as span:
                span.set_attribute("langgraph.node", name)
                try:
                    result = fn(state)
                    if result:
                        span.set_attribute(
                            "node.output_keys", ",".join(str(k) for k in result.keys())
                        )
                    return result
                except Exception as exc:  # noqa: BLE001 - 异常转 span 状态后重抛
                    span.record_exception(exc)
                    span.set_status(
                        otel_trace.Status(otel_trace.StatusCode.ERROR, str(exc))
                    )
                    raise

        return wrapper

    return decorator


def _instrument(provider: Any) -> None:
    """自动埋点：try-import 各 instrumentor，失败优雅降级（FR-010）。

    LangChain instrumentor 覆盖 LangGraph 图/节点执行与原生模型类
    ChatOpenAI（002-langchain-ecosystem 后 LiteLLMProvider 已移除，
    不再埋点 litellm）。
    """
    # LangGraph / LangChain 图与节点执行 + ChatOpenAI（traceloop.span.kind=workflow）
    try:
        from opentelemetry.instrumentation.langchain import LangchainInstrumentor

        LangchainInstrumentor().instrument(tracer_provider=provider)
    except ImportError:
        logger.warning(
            "opentelemetry-instrumentation-langchain 未安装，跳过 LangGraph 自动埋点。"
        )
    except Exception as exc:  # noqa: BLE001 - 可观测失败不阻塞
        logger.warning("LangGraph 自动埋点失败：%s", exc)


__all__ = ["setup_telemetry"]
