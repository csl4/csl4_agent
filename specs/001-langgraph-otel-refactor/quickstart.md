# Quickstart: LangGraph + OTel 重构端到端验证

> 阶段：Phase 1（/speckit-plan）｜ 日期：2026-09-13
> 本文件是**验证/运行指南**，不含实现代码。实现细节见 `tasks.md`（Phase 2）。契约与模型详见 `contracts/` 与 `data-model.md`。

## 前置条件

- Python 3.10+（当前 conda `base_llm`）
- 依赖：新增 2 个 instrumentors（见 [research.md](research.md) R-06）。安装（管理员授权 site-packages 后可执行）：
  ```bash
  uv pip install -e ".[dev]"
  uv pip install opentelemetry-instrumentation-langchain opentelemetry-instrumentation-litellm
  ```
- 可观测后端（验证 SC-001/002 时可选，本地验证用 Jaeger）：
  ```bash
  docker run -d --name jaeger -p 16686:16686 -p 4317:4317 -p 4318:4318 \
    -e COLLECTOR_OTLP_ENABLED=true jaegertracing/all-in-one:latest
  ```
- 任一 LLM API Key（`AGENT_API_KEY` 或 `OPENAI_API_KEY`）

## 验证场景

### S1. 回归：外部契约不变（最高优先）

**命令**：
```bash
python -m pytest -m "not llm" -k "tool_calling or call_stream or main_agent" -v
```
**预期**：非 LLM 测试全绿（`call_stream` / `run_task` / `set_hitl_mode` 契约未破坏）；无 LLM 调用（ScriptedLLM 打桩）。

**关键对照**：对同一脚本化输入，LangGraph 版 `call_stream` 产出的 `StreamMessage` 序列与重构前一致（实现阶段内置该回归用例，见 [contracts/orchestration.md](contracts/orchestration.md) §3）。

### S2. 可观测链路完整（SC-001/002）

**命令**：
```bash
export AGENT_OTEL_ENABLED=true
agent run "查询北京天气"          # 走 LangGraph 编排 + OTel 埋点
```
**预期**：
- 打开 `http://localhost:16686` → Service `gsagent` → Find Traces，看到一条 `invoke_agent` 任务级 span，其下嵌套 `chat`（LLM）与 `execute_tool`（工具）span，全部共享同一 trace_id（[contracts/observability.md](contracts/observability.md) §2）。
- LLM span 带 `gen_ai.usage.input_tokens` / `output_tokens` 与 `llm.cost.usd`（若配置定价）。

### S3. 隐私开关（FR-007）

**命令**：
```bash
export AGENT_OTEL_ENABLED=true          # trace_content 默认 false
agent run "查询北京天气"
```
**预期**：Jaeger span 中**无** `gen_ai.prompt` / `gen_ai.completion` 内容，但 token/耗时指标完整（内容与指标解耦）。

### S4. 可观测后端不可达不阻塞（FR-010）

**命令**：
```bash
export AGENT_OTEL_ENABLED=true AGENT_OTEL_ENDPOINT=http://localhost:19999   # 不存在的端口
agent run "1+1=?"
```
**预期**：Agent 正常完成并输出答案；仅后台导出线程报连接错误日志，主流程无阻塞（SC-008）。

### S5. Guardrail 输入/输出侧（SC-006）

**命令**（离线单测优先）：
```bash
python -m pytest -m "not llm" -k "guardrail or input_guard or output_guard" -v
```
**预期**：
- 注入样本（如角色逃逸指令）被 `guard_in` 拦截，审计出现 `guardrail_input/blocked` 记录（拦截率 100%）。
- 结构化输出 schema 校验失败触发 Fallback 且留痕；合法输出零误拦。
- 审计 JSONL 中无明文敏感键。

### S6. 中断/恢复（SC-005）

**命令**（serve 或 CLI 交互）：
```bash
agent chat --hitl=always
> 帮我删除文件 a.txt        # 触发 APPROVAL_REQUIRED
/ 键入批准或拒绝
```
**预期**：审批暂停后恢复继续执行，已完成的兄弟调用结果不丢失、不重复执行（LangGraph checkpointer 状态重入，[contracts/orchestration.md](contracts/orchestration.md) §2 规则 2）。

### S7. 步数熔断（宪法 2.3）

**命令**：
```bash
python -m pytest -m "not llm" -k "max_steps" -v
```
**预期**：达到 `max_steps`（默认 20）强制终止，返回部分结果并带 `max_steps_reached=True`；死循环（连续 3 步相同调用）提前终止。

## 验收对照表

| 场景 | 对应 | 验证点 |
|---|---|---|
| S1 | FR-011/SC-007 | 契约零破坏、回归绿 |
| S2 | FR-001/002/SC-001/002 | 三层链路 + token/费用 |
| S3 | FR-007/SC-006 | 内容脱敏、指标完整 |
| S4 | FR-010/SC-008 | 可观测不可达不阻塞 |
| S5 | FR-006/SC-006 | Guardrail 拦截 + 审计 |
| S6 | FR-004/SC-005 | 中断恢复不重复 |
| S7 | FR-005/宪法 2.3 | 熔断 + 死循环检测 |
