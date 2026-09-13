# 可观测性（OpenTelemetry + 事件流）

本项目按宪法第三条「一切可观测，没有 Trace 不上线」接入可观测性，编排层为 LangGraph 显式图（`GSagent/core/orchestration/`），可观测采用 OpenLLMetry 自动埋点 + 手动业务 span 双层结构。

## 概览

一次任务执行对应一条 Trace：任务级 `invoke_agent` span → 节点级 `node.*` span → LLM `chat` span / 工具 `execute_tool` span，全部共享同一 trace_id。同时，运行时每步执行产生 `AgentEventEnvelope` 事件（Append-Only 事件流），经 `MetricsAggregator` 聚合为 Agent/Task 指标快照（frozen，指标唯一来源是事件流）。

## 启用

```bash
export AGENT_OTEL_ENABLED=true          # 默认关闭：false 时零 OTel 初始化，主流程无感知
export AGENT_OTEL_ENDPOINT=http://localhost:4318   # 空 = 默认
export AGENT_OTEL_SERVICE_NAME=gsagent
export AGENT_OTEL_TRACE_CONTENT=false   # 默认不采集 prompt/completion（内容与指标解耦）
```

也可在 `.GSagent/config.yaml` 配置 `observability:` 段：

```yaml
observability:
  enabled: true
  service_name: gsagent
  otlp_endpoint: http://localhost:4318
  trace_content: false
```

## 本地验证

启动 Jaeger 后端：

```bash
docker run -d --name jaeger -p 16686:16686 -p 4317:4317 -p 4318:4318 \
  -e COLLECTOR_OTLP_ENABLED=true jaegertracing/all-in-one:latest
```

运行一次任务后打开 `http://localhost:16686`，Service 选择 `gsagent` → Find Traces，应看到 `invoke_agent → chat/execute_tool` 的瀑布结构，LLM span 带 `gen_ai.usage.*` 与 `llm.cost.usd`。

## 事件流查询

事件流默认接入内存仓库（`MemoryEventStore`），`MetricsAggregator` 从事件流聚合指标。事件信封的 `trace_id`/`span_id` 取自 OTel 当前 span context；未接入 OTel 时留空，主流程照常运行（可观测不得成为可用性依赖）。

## 后端可替换

应用只向 OTLP endpoint 发送 trace，不感知后端身份（Jaeger / Tempo / LangSmith / Phoenix 任选）。切换后端仅改 `observability.otlp_endpoint`，不涉及 Agent 主流程代码。高隐私场景可在应用与后端之间加 Collector 做采样、脱敏与多路分发。
