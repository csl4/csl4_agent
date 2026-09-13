# Contract: 安全护栏（Guardrails）

> 来源：`spec.md` FR-006/007 ｜ 设计：`research.md` R-05 ｜ 数据模型：`data-model.md` §4
> 宪法依据：第十一章 11.1 三道 Guardrail（输入/输出/结果侧），全部拦截审计留痕、敏感脱敏

## 目的

定义输入侧/输出侧 Guardrail 的拦截语义与审计契约。结果侧（工具返回值校验）已由既有 `ToolExecutor`（`path_guard`/`command_guard` + `rules.guard_kind_for`）承担，本契约不重复。

## 1. 三道 Guardrail 分工

| 位置 | 责任方 | 校验内容 | 拦截后果 |
|---|---|---|---|
| 输入侧 | `policy/input_guard.py`（新增） | 注入模式、违禁词、内容合规 | 中止本轮 LLM 调用，审计 `blocked` |
| 输出侧 | `policy/output_guard.py`（新增） | 结构化输出 schema 校验、敏感过滤 | 分层 Fallback 后放行/降级，审计 |
| 结果侧 | `ToolExecutor`（既有） | 工具返回值范围、状态码、副作用确认 | 错误不流入下一轮推理（既有） |

## 2. 输入侧契约（`input_guard.py`）

```python
class InputGuard:
    def check(self, text: str) -> GuardResult:
        """返回 allowed/reason/matched_rule；拦截时调用方中止本轮。"""
```

**规则集**（确定性优先，宪法 2.1「规则用代码实现」）：
- 注入模式：角色逃逸 / 系统指令覆盖 / 工具描述注入等常见模式（内置默认集）。
- 违禁词：内容合规黑名单（内置 + 用户 `guardrails.input.deny_patterns` 追加）。
- **不做**强制内容审查的裁决（无多租户/合规重场景，不做大模型审核依赖）。

**审计契约**（复用 `AuditLog.record`）：
```
event_type: "guardrail_input" | outcome: "blocked"
payload: { matched_rule, sanitized(可选) }   # 不含原始敏感输入全文
session_id: <request_context.session_id>
```

**验收**：注入样本拦截率 100%（SC-006）；合法输入零误拦；拦截留痕可查。

## 3. 输出侧契约（`output_guard.py`）

```python
class OutputGuard:
    def check(self, content: str, expected_schema: dict | None) -> OutputCheckResult:
        """对程序消费输出做 schema 校验；自由文本直接放行。"""
```

**触发范围**：仅 `response_format` 结构化场景（LLM 输出将被程序消费）校验 schema；自由文本输出跳过（不误伤，宪法 5.4 分层约束）。

**分层 Fallback**（宪法 5.4，不直接抛错给用户）：
1. **轻量修复**：可确定性修复（如 JSON 容错/字段补默认）→ 修复后校验。
2. **定向重试**：携带校验错误上下文重试 ≤`guardrails.output.fallback_retries`（默认 1，≤2）。
3. **降级**：以上失败 → 标记 `fallback_applied`，返回带 issue 的结果，由编排层决定降级呈现（不静默成功）。

**敏感过滤**：复用 `payload_redacted` 键名语义（token/key/password/secret/authorization/bearer），输出中含敏感键的值脱敏后才放行/记录。

**审计契约**：
```
event_type: "guardrail_output" | outcome: "ok" | "fallback" | "blocked"
payload: { issue, fallback_applied }   # 不含完整敏感输出
```

**验收**：结构化输出校验失败均触发 Fallback 且留痕；敏感键明文出现率 0（SC-006）；自由文本无额外开销。

## 4. 配置契约

```yaml
guardrails:
  input:  { enabled: true, deny_patterns: [] }
  output: { enabled: true, validate_schema: true, fallback_retries: 1 }
```

- 默认启用、可整体/分侧关闭；关闭 = 既有行为（零改动，向后兼容）。
- 环境变量：`AGENT_GUARDRAIL_INPUT` / `AGENT_GUARDRAIL_OUTPUT`（bool）。
- 新增段走 `_ENV_OVERRIDES` 声明式表，不改既有配置字段（宪法 III）。

## 5. 与既有安全层的组合

- Guardrail 与既有 `PathGuard`/`CommandGuard`/`HitlPolicy`/`AuditLog` 共存于 `policy/`，职责正交：Guardrail 面向 **LLM 输入/输出**，路径/命令守卫面向**工具执行副作用**，HITL 面向**高风险写操作确认**。
- 同一 AuditLog 通道统一留痕，事件类型前缀区分（`guardrail_*` / `approval` / `model_call` / `error`），互不混淆。
