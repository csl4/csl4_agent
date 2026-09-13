# Contract: @tool 工具注册与守卫/审批包装层

> 来源：`spec.md` FR-004/005/006/012 ｜ 设计：`research.md` R-03/R-04 ｜ 数据模型：`data-model.md` §2

## 目的

定义全部工具集迁移为 langchain `@tool` 后的注册、守卫、审批契约。**安全行为不变**（SC-003）：命令/路径守卫仍拦截、高风险工具仍审批、执行仍留痕。

## 1. 工具注册（`GSagent/core/tools/registry.py`）

```python
register_tool(fn) -> BaseTool    # @tool 装饰并登记（替代 BUILTIN_PYTHON_TOOLSETS 工厂）
get_all_tools() -> list[BaseTool]  # 全量工具（bind_tools / ToolNode 用）
get_tool(name) -> BaseTool | None
```

- 工具名/描述：`@tool` 装饰器从函数名/docstring 生成；需显式名/描述的用 `StructuredTool.from_function`。
- 工具配置（原 `bash:`/`sandbox:` 段）：注册表工厂闭包捕获注入。

## 2. 守卫包装层（`wrap_with_guards`）

```python
wrap_with_guards(tool, guards) -> BaseTool
```

- 调用前按 `rules.guard_kind_for(tool.name)` 分类：`path` → `PathGuard.check(params["path"])`；`command` → `CommandGuard.check(params["command"])`。
- 拦截 → 返回错误 `ToolMessage(content="blocked...")` + `AuditLog.record(event_type="tool_call", outcome="blocked")`。
- 行为与既有 `ToolExecutor._guard_block` 一致（SC-003）。

## 3. 审批包装层（`wrap_with_approval`）

```python
wrap_with_approval(tool, hitl, loop) -> BaseTool
```

- HITL（auto/always/never）裁决：`hitl.approve(tool_name, params)`。
- 需审批 → 返回审批信号（`ApprovalRequirement` 语义）→ 编排 tools 节点收集 → `interrupt()` 暂停。
- 恢复：`Command(resume={"tool_decisions": {...}})` → 批准的工具重新执行（`user_approved=True`）/ 拒绝的工具返回错误 ToolMessage + 审计。

## 4. 执行装配（`build_tool_node`）

```python
build_tool_node(tools, loop) -> ToolNode
```

- `ToolNode` 从 `AIMessage.tool_calls` 分发到工具、产出 `ToolMessage`（`handle_tool_errors` 兜底）。
- **V-02 验证项**：审批「同批兄弟调用全结算」语义（防 tool_call_id 孤儿）——若 `ToolNode` 不天然支持，tools 节点保留自定义并行 + interrupt（001 已验证模式）。
- 并行执行保留（多工具同批）。

## 5. 验收要点

- 每个既有工具集（bash/文件/记忆/沙箱/技能/yaml）迁移为 `@tool` 后，`get_all_tools()` 数量与迁移前工具数一致（SC-002）。
- 破坏性命令/非法路径拦截率 100%（SC-003）；拦截审计 `blocked`。
- 高风险工具审批 100% 触发；批准后执行、拒绝后错误回填（SC-003）。
- 工具执行结果（`ToolMessage`）与迁移前 `StructuredToolResult` 语义等价。
