# Quickstart: 完全迁移到 langchain/langgraph 生态验证

> 阶段：Phase 1（/speckit-plan）｜ 日期：2026-09-13
> 本文件是**验证/运行指南**，不含实现代码。契约与模型详见 `contracts/` 与 `data-model.md`。

## 前置条件

- Python 3.10+（当前 conda `base_llm`）
- 依赖：新增 `langchain-openai`（ChatOpenAI），安装（base_llm 只读需管理员授权一次）：
  ```bash
  uv pip install -e ".[dev]"
  uv pip install "langchain-openai>=0.3"
  ```
- LLM Key：`AGENT_API_KEY` + `AGENT_BASE_URL`（OpenAI 兼容网关，deepseek 等）

## 验证场景

### S1. 回归：既有测试全适配（SC-001/006，最高优先）

**命令**：
```bash
python -m pytest tests -m "not llm" -q
```
**预期**：既有 105 测试全适配（dict→BaseMessage 断言）后**全绿**；新增迁移测试（工具等价/守卫/审批/事件序列）通过。

### S2. 消息层 BaseMessage（FR-001，contracts/messages.md）

**命令**（离线单测）：
```bash
python -m pytest -k "llm_adapter or messages" -v
```
**预期**：`dict_to_messages`/`messages_to_dict` 双向保真；`add_messages` 正确合并 Human/AI/Tool；`extract_usage` 从 `response_metadata["token_usage"]` 提取正确。

### S3. 工具层 @tool + 守卫/审批（FR-004/005/006，contracts/tools.md）

**命令**：
```bash
python -m pytest -k "tool_registry or guards or approval" -v
```
**预期**：
- `get_all_tools()` 工具数 = 迁移前工具数（SC-002，行为等价）。
- 破坏性命令/非法路径拦截率 100% + 审计 `blocked`（SC-003）。
- 高风险工具审批 100% 触发；批准执行/拒绝回填（SC-003）。

### S4. LLM 层 ChatOpenAI（FR-002/011，contracts/llm.md）

**命令**：
```bash
export AGENT_MODEL=deepseek/deepseek-v4-flash AGENT_BASE_URL=https://api.deepseek.com
agent run "查询北京天气"
```
**预期**：走 ChatOpenAI（base_url 指 deepseek）完成含工具任务；token/费用在事件流正确（SC-005，**V-01 验证项**）。

### S5. 外部契约不变（SC-004，FR-007/009）

**命令**：
```bash
python -m pytest -k "call_stream or stream_events" -v
```
**预期**：`StreamMessage` SSE 事件序列与迁移前一致（ANSWER_DELTA/USAGE/ANSWER_END/审批等）；CLI 命令行为不变。

### S6. 暂停恢复（FR-010）

**命令**：
```bash
python -m pytest -k "pause or resume or interrupt" -v
```
**预期**：审批/前端暂停 → `interrupt()` → 下次 `Command(resume)` 断点续跑，基于 BaseMessage 状态不丢上下文（scenario 001 已验证模式）。

### S7. 可观测（SC-005，FR-008）

**命令**：
```bash
export AGENT_OTEL_ENABLED=true
agent run "1+1=?"
```
**预期**：事件流/审计 token 费用准确；OTel 链路完整（ChatOpenAI 原生模型类被 langchain instrumentor 自动埋点，覆盖更完整）。

## 验收对照表

| 场景 | 对应 | 验证点 |
|---|---|---|
| S1 | SC-001/006 | 105 测试全适配全绿 |
| S2 | FR-001 | BaseMessage 转换保真 |
| S3 | FR-004/005/006 | 工具等价 + 守卫/审批保持 |
| S4 | FR-002/011 | ChatOpenAI + deepseek 兼容（V-01） |
| S5 | FR-007/009 | SSE 事件流/CLI 不变 |
| S6 | FR-010 | interrupt 暂停恢复 |
| S7 | FR-008 | token/费用/链路可观测 |
