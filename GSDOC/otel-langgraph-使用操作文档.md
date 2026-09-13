# OpenTelemetry + LangGraph 使用操作文档

> 版本 v1.0 ｜ 更新日期：2026-09-13
> 适用：Python 3.10+ / LangGraph 0.2+ / OTel SDK 1.2x+

---

## 目录

1. [概述：为什么 LangGraph 需要 OpenTelemetry](#1-概述为什么-langgraph-需要-opentelemetry)
2. [核心概念速览](#2-核心概念速览)
3. [可观测方案选型（四种接入方式对比）](#3-可观测方案选型四种接入方式对比)
4. [环境准备与依赖安装](#4-环境准备与依赖安装)
5. [快速开始：本地跑通第一条 Trace](#5-快速开始本地跑通第一条-trace)
6. [方案 A：OpenLLMetry 自动埋点（推荐）](#6-方案-aopenllmetry-自动埋点推荐)
7. [方案 B：OpenInference + Arize Phoenix](#7-方案-bopeninference--arize-phoenix)
8. [方案 C：LangSmith OTel 集成](#8-方案-clangsmith-otel-集成)
9. [方案 D：手动埋点与自定义 Span](#9-方案-d手动埋点与自定义-span)
10. [OTel Collector 中间层与 PII 脱敏](#10-otel-collector-中间层与-pii-脱敏)
11. [查看与排查：Jaeger UI 操作指南](#11-查看与排查jaeger-ui-操作指南)
12. [生产环境部署建议](#12-生产环境部署建议)
13. [附录：环境变量表与 FAQ](#13-附录环境变量表与-faq)

---

## 1. 概述：为什么 LangGraph 需要 OpenTelemetry

LangGraph 将 Agent 编排为**有状态的图**（节点 = 函数/Runnable，边 = 控制流），天然支持循环、分支、并行、Human-in-the-loop 与断点恢复。但这也带来观测难题：

- 一次用户请求可能经过 **N 个节点、多次 LLM 调用、多个工具调用**，在传统 APM 里只是"一个慢接口"；
- 无法回答关键问题：**哪个节点最慢？走了哪条条件分支？每个 LLM 调用花了多少 token、多少钱？**
- LangGraph 的 Checkpointer 可以恢复执行，但**不能回放执行过程**——这需要 Trace。

**OpenTelemetry（OTel）** 是 CNCF 的可观测性开放标准，通过统一 Trace / Metric / Log 三种信号，把 Agent 的每一步执行变成可视化、可检索、可告警的数据，并且**厂商中立**——后端可随时从 Jaeger 换到 Tempo、LangSmith 或商业 APM。

```text
LangGraph 应用 ──(OTLP)──▶ OTel Collector ──▶ Jaeger / Tempo / LangSmith / Phoenix
   │ 节点执行            可选：采样、脱敏、增强        Trace 检索与瀑布图
   │ LLM 调用(token/费用)
   │ 工具调用 / DB / HTTP
```

---

## 2. 核心概念速览

| 概念 | 说明 |
|---|---|
| **Trace / Span** | 一次完整请求 = 一个 Trace（由唯一 trace_id 标识）；每一步操作（节点执行、LLM 调用、HTTP 请求）= 一个 Span，Span 之间通过父子关系组成瀑布图。 |
| **Context 传播** | trace context 随执行流自动传递，跨节点/跨服务仍归属同一 Trace。 |
| **OTLP 协议** | OTel 官方导出协议，默认端口：gRPC `4317`，HTTP `4318`。 |
| **GenAI 语义约定** | OTel 社区为生成式 AI 定义的统一属性名，如 `gen_ai.prompt`、`gen_ai.completion`、`gen_ai.usage.input_tokens`、`gen_ai.operation.name`。 |
| **LangGraph StateGraph** | 图构建器：`add_node()` 注册节点，`add_edge()` / `add_conditional_edges()` 连接控制流，`compile()` 生成可执行图。 |
| **Checkpointer** | 每个 super-step 后持久化状态（内存 / SQLite / Postgres），用于会话记忆、断点恢复、时间旅行。注意：**Checkpoint ≠ Trace**，二者互补。 |
| **Reducer** | 状态字段的合并策略，如 `Annotated[list, add_messages]` 表示追加而非覆盖。 |

---

## 3. 可观测方案选型（四种接入方式对比）

| 方案 | 埋点库 | 特点 | 适用场景 |
|---|---|---|---|
| **【推荐】OpenLLMetry 自动埋点** | `opentelemetry-instrumentation-langchain` | 一条代码自动埋点 LangChain/LangGraph 全链路（Agent、Chain、Tool、LLM 调用），遵循 OTel GenAI 语义约定，标准 OTLP 输出，后端任选。 | 生产、需要标准 OTel 体系的团队 |
| OpenInference | `openinference-instrumentation-langchain` | LangChain 官方生态的埋点规范，与 Arize Phoenix 无缝配合，属性名前缀为 `input.value` / `output.value` 等。 | 本地调试、Agent 评估分析 |
| LangSmith OTel | LangSmith SDK 内置 | 仅设环境变量即可开启（`LANGSMITH_OTEL_ENABLED=true`），Trace 走 OTLP 发到自建 Collector 或 LangSmith 云端。 | 已订阅 LangSmith 的团队 |
| 手动埋点 | OTel SDK 原生 | 自己用 `tracer.start_as_current_span()` 包裹节点，完全控制属性与脱敏逻辑。 | 定制需求、自定义 Span / 指标 |

> ✅ **选型建议：** 默认用**方案 A（OpenLLMetry）**，零侵入、标准协议、后端可替换；本地调试想要现成的 LLM 可视化界面可用方案 B；已有 LangSmith 订阅则用方案 C；特殊脱敏/自定义指标需求叠加方案 D。

---

## 4. 环境准备与依赖安装

### 4.1 前置条件

- Python 3.10+（推荐 3.12/3.13）
- Docker（用于启动 Jaeger / Phoenix 观测后端）
- 任一 LLM API Key（如 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY` 或国内模型网关）

### 4.2 安装依赖

```bash
pip install -U langgraph langchain-core langchain-openai
pip install -U opentelemetry-api opentelemetry-sdk \
              opentelemetry-exporter-otlp-proto-http \
              opentelemetry-instrumentation-langchain
# 生产可选：
pip install -U opentelemetry-instrumentation-httpx \
              opentelemetry-instrumentation-fastapi \
              opentelemetry-instrumentation-sqlalchemy
# 持久化 checkpoint（可选）：
pip install -U langgraph-checkpoint-sqlite
```

> ℹ️ **版本要求：** `opentelemetry-instrumentation-langchain >= 0.55.0` 才支持新版 GenAI Agent Span 语义约定，请勿使用过旧版本。

---

## 5. 快速开始：本地跑通第一条 Trace

### 5.1 启动 Jaeger 后端（Docker 一行命令）

```bash
docker run -d --name jaeger \
  -p 16686:16686 \
  -p 4317:4317 \
  -p 4318:4318 \
  -e COLLECTOR_OTLP_ENABLED=true \
  jaegertracing/all-in-one:latest
```

启动后访问 `http://localhost:16686` 即为 Jaeger UI。

### 5.2 编写 LangGraph 示例应用（agent_graph.py）

下面是一个带工具调用、条件边和 checkpointer 的最小可运行 Agent：

```python
"""agent_graph.py — LangGraph ReAct 风格 Agent 示例"""
import operator
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from telemetry import setup_telemetry  # 见 5.3

# ---------- 1. 先初始化 OTel（必须在导入业务模块之前） ----------
setup_telemetry(
    service_name="my-langgraph-agent",
    otlp_endpoint="http://localhost:4318",
)

# ---------- 2. 定义工具 ----------
def get_weather(city: str) -> str:
    """查询指定城市天气（演示用，返回假数据）"""
    return f"{city}：晴，26℃，东南风 2 级"

tools = [get_weather]

# ---------- 3. 定义状态 ----------
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]   # 追加式合并

# ---------- 4. 定义节点 ----------
llm = ChatOpenAI(model="gpt-4o-mini").bind_tools(tools)

def call_model(state: AgentState) -> dict:
    response = llm.invoke(state["messages"])
    return {"messages": [response]}

def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else END

# ---------- 5. 组装图 ----------
builder = StateGraph(AgentState)
builder.add_node("agent", call_model)
builder.add_node("tools", ToolNode(tools))
builder.add_edge(START, "agent")
builder.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
builder.add_edge("tools", "agent")           # 工具结果回到 LLM，形成循环

graph = builder.compile(checkpointer=InMemorySaver())

# ---------- 6. 执行 ----------
if __name__ == "__main__":
    config = {"configurable": {"thread_id": "demo-thread-1"}}
    result = graph.invoke(
        {"messages": [("user", "北京今天天气怎么样？")]},
        config,
    )
    print(result["messages"][-1].content)
```

### 5.3 OTel 初始化模块（telemetry.py，方案 A 通用）

```python
"""telemetry.py — 统一 OTel 初始化（Traces）"""
import atexit

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.langchain import LangchainInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def setup_telemetry(service_name: str, otlp_endpoint: str) -> trace.Tracer:
    resource = Resource.create({
        "service.name": service_name,
        "service.version": "1.0.0",
        "deployment.environment": "dev",
    })
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces")
        )
    )
    trace.set_tracer_provider(provider)

    # LangChain/LangGraph 自动埋点：Agent/Chain/Tool/LLM 全覆盖
    LangchainInstrumentor().instrument(tracer_provider=provider)

    atexit.register(provider.shutdown)  # 进程退出前 flush 剩余 span
    return trace.get_tracer(service_name)
```

### 5.4 运行并验证

```bash
export OPENAI_API_KEY=sk-...
python agent_graph.py
```

打开 `http://localhost:16686`，Service 下拉框选择 `my-langgraph-agent` → Find Traces，即可看到类似这样的瀑布结构：

```text
trace: LangGraph agent run
├── span: langchain.workflow  (invoke_agent / 图执行)
│   ├── span: langchain.workflow  (node: agent → LLM)
│   │   └── span: langchain.chat  (LLM 调用, 含 prompt/token/模型名)
│   ├── span: langchain.tool     (get_weather 执行)
│   └── span: langchain.chat     (第 2 轮 LLM 调用, 生成最终回答)
```

> ✅ **验证要点：** 能看到 LLM span 上的 `gen_ai.usage.*` token 属性、tool span 的入参出参，且全部 span 归属于同一 trace_id，即接入成功。

---

## 6. 方案 A：OpenLLMetry 自动埋点（推荐）

核心就是 5.3 节的 `telemetry.py`，补充几点操作说明：

### 6.1 Span 类型识别

| Span 类型 | 识别属性 | 含义 |
|---|---|---|
| Agent / 图执行 | `traceloop.span.kind = workflow`（新版本同时有 `gen_ai.operation.name = invoke_agent`） | LangGraph 顶层运行 |
| 工具调用 | `traceloop.span.kind = tool`（`gen_ai.operation.name = execute_tool`） | @tool 函数执行 |
| LLM 推理 | `gen_ai.operation.name = chat` | 模型调用，含 token 用量 |

### 6.2 关闭提示词记录（省体积/合规）

```bash
# 全局环境变量方式：不采集 prompt/completion 内容，只保留指标
export TRACELOOP_TRACE_CONTENT=false
```

### 6.3 让 LangGraph 节点级执行更清晰

自动埋点对"节点粒度"覆盖有限，生产建议在关键节点手动叠加一层业务 span（见第 9 节），形成 **workflow（业务语义）+ 自动 span（技术细节）** 的双层结构。

---

## 7. 方案 B：OpenInference + Arize Phoenix

OpenInference 提供 LLM 专用 UI（含 token/费用统计、会话回放、评估打分），适合本地开发调试。

### 7.1 启动 Phoenix 并安装

```bash
pip install -U openinference-instrumentation-langchain arize-phoenix
python -m phoenix.server serve              # 启动本地 UI: http://localhost:6006
```

### 7.2 初始化代码

```python
from openinference.instrumentation.langchain import LangChainInstrumentor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk import trace as trace_sdk
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = trace_sdk.TracerProvider()
provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint="http://localhost:6006/v1/traces"))
)
LangChainInstrumentor().instrument(tracer_provider=provider)
```

> 📝 与方案 A 的区别主要是属性命名规范（`input.value`/`output.value` vs `gen_ai.*`）和后端 UI 倾向；接入代码几乎一样，切换成本低。

---

## 8. 方案 C：LangSmith OTel 集成

### 8.1 直接发到 LangSmith 云端（零代码）

```bash
export LANGSMITH_API_KEY=lsv2_pt_...
export LANGSMITH_TRACING=true
export LANGSMITH_OTEL_ENABLED=true
export LANGSMITH_PROJECT=my-project
python agent_graph.py     # 无需任何埋点代码
```

### 8.2 经自建 Collector 转发（可先脱敏再上云）

```bash
export LANGSMITH_TRACING=true
export LANGSMITH_OTEL_ENABLED=true
export LANGSMITH_OTEL_ONLY=true
export LANGSMITH_PROJECT=my-project
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   # 指向自建 Collector
```

Collector 将 trace 转发到 LangSmith 的 OTLP 入口 `https://api.smith.langchain.com/otel/v1/traces`（完整配置见第 10 节）。

### 8.3 用 OTel SDK 编程控制（动态项目名等）

```python
import os
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider()
provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(
        endpoint=os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] + "/v1/traces",
        headers={"Langsmith-Project": "dynamic-project-name"},
    ))
)
trace.set_tracer_provider(provider)
```

---

## 9. 方案 D：手动埋点与自定义 Span

给 LangGraph 节点包一层业务语义 span，并在条件边上记录路由决策：

```python
from opentelemetry import trace

tracer = trace.get_tracer("my-langgraph-agent")

def traced_node(name: str):
    """装饰器工厂：为节点创建业务 span"""
    def decorator(fn):
        def wrapper(state):
            with tracer.start_as_current_span(f"node.{name}") as span:
                span.set_attribute("langgraph.node", name)
                try:
                    result = fn(state)
                    span.set_attribute("node.output_keys",
                                       ",".join(result.keys()) if result else "")
                    return result
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                    raise
        return wrapper
    return decorator

@traced_node("router")
def router(state: AgentState) -> dict:
    with tracer.start_as_current_span("route_decision") as span:
        decision = "tools" if needs_tool(state) else "end"
        span.set_attribute("route.decision", decision)   # 记录分支走向
        span.set_attribute("route.reason", "tool_call_requested")
    return {"messages": []}
```

### 手动记录 LLM 费用指标（GenAI 语义约定）

```python
PRICING = {"gpt-4o-mini": (0.15 / 1e6, 0.60 / 1e6)}  # 每百万输入/输出 token 单价(USD)

def record_cost(span, model: str, input_tokens: int, output_tokens: int):
    pin, pout = PRICING.get(model, (0, 0))
    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
    span.set_attribute("llm.cost.usd", round(input_tokens * pin + output_tokens * pout, 6))
```

> ℹ️ HTTP/DB 层可用官方 instrumentor 一键接入：`HTTPXClientInstrumentor().instrument()`、`SQLAlchemyInstrumentor().instrument(engine=engine)`，与 Agent span 自动组成同一 Trace。

---

## 10. OTel Collector 中间层与 PII 脱敏

生产建议在应用与后端之间加一层 Collector（docker run / k8s daemonset），统一做采样、脱敏、多路分发。以下配置：接收 OTLP → 脱敏 prompt/completion → 同时发 Jaeger 与 LangSmith：

```yaml
# otel-collector-config.yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    timeout: 5s
  memory_limiter:
    check_interval: 1s
    limit_mib: 512
  transform/redact:
    error_mode: ignore
    trace_statements:
      - context: span
        statements:
          - replace_pattern(attributes["gen_ai.completion"], "[\\s\\S]*", "[REDACTED]")
          - replace_pattern(attributes["gen_ai.prompt"], "[\\s\\S]*", "[REDACTED]")

exporters:
  otlphttp/jaeger:
    endpoint: "http://jaeger:4318"
  otlphttp/langsmith:
    traces_endpoint: "https://api.smith.langchain.com/otel/v1/traces"
    headers:
      x-api-key: "${env:LANGSMITH_API_KEY}"
      Langsmith-Project: "${env:LANGSMITH_PROJECT}"

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, transform/redact, batch]
      exporters: [otlphttp/jaeger, otlphttp/langsmith]
```

```bash
docker run -d --name otel-collector -p 4318:4318 \
  -v $(pwd)/otel-collector-config.yaml:/etc/otelcol/config.yaml \
  otel/opentelemetry-collector-contrib:latest
```

> ✅ 如需更精细的 PII 处理（邮箱/手机号/身份证），可加 `redaction` processor 或 attributes processor 按正则遮蔽；未上 LangSmith 则删掉对应 exporter 即可。

---

## 11. 查看与排查：Jaeger UI 操作指南

| 操作 | 步骤 |
|---|---|
| 查找 Trace | 左侧 Service 选服务名 → 设时间范围 → **Find Traces**；可用 Tags 过滤，如 `error=true`、`gen_ai.operation.name=chat`。 |
| 看瀑布图 | 点开任意 Trace，每个横条为一个 span；宽度即耗时，可快速定位慢节点/慢 LLM 调用。 |
| 查看属性 | 展开 span 查看 Attributes：prompt、token 数、模型名、路由决策等。 |
| 对比分析 | 勾选多条 Trace → **Compare**，对比同一操作的正反例。 |
| 系统架构图 | 底部 **Trace Graph / System Architecture** 查看服务依赖。 |
| 性能瓶颈 | 按 Duration 排序 / 使用 **Deep Dependency Graph** 分析接口聚合耗时。 |

### 看不到 Trace 的排查清单

1. **初始化顺序**：`trace.set_tracer_provider()` 与 instrument 是否在 LLM 调用**之前**执行？
2. **端点路径**：HTTP exporter 需要完整路径 `http://host:4318/v1/traces`，不是只写 host。
3. **Batch 延迟**：BatchSpanProcessor 默认 5s 批量导出，短脚本退出前要 `provider.shutdown()`（atexit 已处理）。
4. **Provider 重复设置**：OTel 全局 provider 只能设置一次，二次设置会被忽略——检查是否被框架抢先初始化。
5. **网络/端口**：容器内访问宿主机 Jaeger 应用 `host.docker.internal` 而非 `localhost`。
6. **时间范围**：Jaeger 默认只查最近 1 小时，注意调整时间窗口与时区。

---

## 12. 生产环境部署建议

- **导出方式**：应用内用 `BatchSpanProcessor`（异步批量），不要用 SimpleSpanProcessor（同步阻塞）。
- **采样**：高流量场景配 `ParentBased(TraceIdRatioBased(0.1))`，保留 10% 采样但错误请求全量；LLM 调用建议按需全采。
- **Collector 前置**：应用只对接 Collector，后端切换/重启不影响应用；Collector 承担脱敏、限流、多路分发。
- **Checkpointer 选型**：开发用 `InMemorySaver`；单机容器用 `SqliteSaver`（注意持久卷）；多进程/多实例用 `PostgresSaver`。
- **状态瘦身**：单个 checkpoint 超 ~50KB 时把大对象挪到对象存储，state 里只留引用——过大状态拖慢序列化并挤占 LLM 上下文。
- **内容采集开关**：合规敏感场景设 `TRACELOOP_TRACE_CONTENT=false`，或在 Collector 侧统一脱敏。
- **资源属性**：统一注入 `service.name`、`deployment.environment`、`service.version`，便于按环境/版本聚合。
- **异步应用**：FastAPI 服务里用 `FastAPIInstrumentor` + `HTTPXClientInstrumentor`，让 HTTP 入口、Agent、下游调用串成一条 Trace。

---

## 13. 附录：环境变量表与 FAQ

### 13.1 常用环境变量

| 变量 | 作用 | 示例 |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP 导出地址（Collector / Jaeger） | `http://localhost:4318` |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | 导出协议 | `http/protobuf` |
| `OTEL_SERVICE_NAME` | 服务名（未在代码中设置时生效） | `my-langgraph-agent` |
| `TRACELOOP_TRACE_CONTENT` | 是否采集 prompt/completion 内容 | `false` |
| `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` | LangSmith 认证 / 项目名 | — |
| `LANGSMITH_OTEL_ENABLED` / `LANGSMITH_OTEL_ONLY` | 开启 LangSmith 的 OTel 输出 / 仅走 OTel 通道 | `true` |

### 13.2 FAQ

**Q1：Checkpoint 和 Trace 有什么区别？**
Checkpoint 是 LangGraph 的执行状态快照（用于恢复/回放执行）；Trace 是执行过程的观测记录（用于分析性能与行为）。生产两个都要：Checkpointer 保运行可靠性，OTel 保可观测性。

**Q2：自动埋点会不会记录敏感 prompt？**
会。默认 prompt/completion 会作为 span 属性上报。合规要求高的场景：设 `TRACELOOP_TRACE_CONTENT=false`，或经 Collector 的 transform processor 统一脱敏（第 10 节）。

**Q3：token/费用统计哪里来？**
自动埋点从 LLM 响应的 usage 字段提取 `gen_ai.usage.input_tokens` / `output_tokens`；费用需自备价格表在 span 上计算（第 9 节 record_cost），或在后端按属性聚合。

**Q4：能同时用 LangSmith 和 Jaeger 吗？**
可以。应用把 OTLP 发给 Collector，Collector 配两个 exporter 分发到两边（第 10 节配置即如此）。

**Q5：异步图（ainvoke/astream）支持吗？**
支持。OTel SDK 与两个埋点库均支持 async context，span 上下文在协程间正确传播；确保使用 `async with tracer.start_as_current_span(...)` 写异步手动埋点。

---

*参考：LangChain/LangSmith 官方文档（OTel 集成与 Gateway 脱敏架构）、OpenLLMetry (Traceloop) 文档、OpenInference 项目、OTel GenAI 语义约定。*
