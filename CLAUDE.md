# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

通用 LLM Agent 框架（Python 3.10+）：CLI 交互（Typer）+ 工具执行（插件化 Toolset/Tool）+ 多Agent 编排（A2A 协议，主/编排/业务/动态 SubAgent 四角色）+ 企业级安全策略层（HITL 审批 / 路径与命令守卫 / 审计）。本地优先、离线可测，跨终端（PowerShell / bash / zsh）自动适配。

## Development Commands

```bash
# 安装依赖（uv pip 装进当前激活环境，即 conda base_llm）
uv pip install -e ".[dev]"

# 运行测试
python -m pytest tests -m "not llm"          # 非 LLM 测试
python -m pytest tests/llm/ -n 6             # LLM 测试（并行，需 pytest-xdist）
python -m pytest -k "test_name"              # 运行单个测试

# 代码质量（仅在用户明确要求时运行）
ruff format
ruff check --fix
mypy
```

**注意**：当前仓库正处于 `agent/` → `GSagent/` 的迁移中，`tests/` 目录已从 git 中移除（磁盘上暂不存在），`pyproject.toml` 的 `testpaths` 仍指向 `tests`。恢复测试目录（镜像 `GSagent/` 结构）后上述命令生效；迁移完成前请勿据 `tests/` 的缺失判断代码不可测。

**uv 说明**：依赖管理用 uv 的 pip 兼容接口（`uv pip`），目标是**当前激活的环境**（本机为 conda `base_llm`），不建 `.venv`。本机 base_llm 的 site-packages 对普通用户只读，首次安装前需在管理员 PowerShell 授权一次：`icacls "D:\anaconda\envs\base_llm\Lib\site-packages" /grant "%USERNAME%:(OI)(CI)M"`。依赖声明在 `pyproject.toml`（PEP 621 `[project]` + `[project.optional-dependencies]`，可选组 `dev` / `server`），不要用 Poetry 格式。

## Architecture Overview

### Core Components

| 模块 | 路径 | 职责 |
|---|---|---|
| CLI 入口 | `GSagent/main.py` | 命令路由、配置加载（`run`/`chat`/`serve`/`toolset`/`agents`/`skills`/`history`/`tasks`/`snapshot`/`eval`/`version`） |
| 配置 | `GSagent/config.py` | 组合根：`create_llm` / `create_tool_executor` / `create_tool_calling_llm` / `policy_components` / `create_cost_estimator`；四层覆盖（默认←YAML←环境变量←CLI） |
| 核心引擎 | `GSagent/core/` | ToolCallingLLM 外壳（`core/agents/tool_calling_llm.py`，外部契约不变）、LangGraph 编排层（`core/orchestration/`：state/graph/nodes，宪法 3.2 显式图）、工具执行器（`core/tools/executor.py`）、多Agent 角色、A2A、提示词、截断、供应商 |
| 安全策略 | `GSagent/core/policy/` | PathGuard / CommandGuard / HitlPolicy / AuditLog + 输入/输出侧 Guardrail（`input_guard.py` / `output_guard.py`，宪法 11.1 三道 Guardrail），注入 ToolExecutor 与 ToolCallingLLM |
| Plan-and-Execute | `GSagent/core/plan/` | LLM 产出任务 DAG，按依赖拓扑批次并行执行，失败定位 |
| 运行时 | `GSagent/core/runtime/` | `serve`（FastAPI：线程/回合/SSE）+ SQLite 持久化任务队列（原子租约、取消保护、崩溃恢复） |
| 记忆系统 | `GSagent/core/memory/` | 长期记忆（SQLite `MemoryStore`，跨会话按 scope）+ 会话记忆目录（`SessionMemoryStore` 每轮落盘） |
| 评估体系 | `GSagent/core/eval/` | 三指标（完成率/幻觉率/误拒绝率）+ 基线回归 + 错误案例回流，`OfflineLLM` 离线回归 |
| 快照 / 成本 | `GSagent/core/` | `snapshot.py`（执行前后自动快照）、`cost.py`（本地定价成本估算） |
| 终端适配 | `GSagent/core/env/terminal.py` | PowerShell / bash / zsh 探测与命令改写 |
| 可观测对象模型 | `GSagent/core/observability/` | 业务与可观测四层对象（会话→任务→Agent→事件）+ 九类事件 + 指标聚合（仅事件流）+ 业务↔A2A 协议映射；`telemetry.py` 为 OTel 接入（方案 A 自动埋点 + 手动业务 span，`observability.enabled` 关闭即零初始化）；`emitter.py` 事件流接线（AgentEventEnvelope 随执行产生，trace/span_id 取自 OTel context） |
| 插件系统 | `GSagent/plugins/toolsets/` | 工具集插件（bash/filesystem/sandbox/memory/yaml_loader），注册于 `BUILTIN_PYTHON_TOOLSETS` |
| 通用工具 | `GSagent/utils/` | rich console、StreamEvents 事件流、日志、文件/流式 IO |

### CLI 命令一览

```bash
agent chat                              # 交互（默认命令）；--multi-agent / --plan / --hitl / --max-subagents
agent run "prompt"                      # 单次执行；--prompt-file / --file / --snapshot / --hitl / --json-output-file
agent serve                             # Runtime API（FastAPI）；--host / --port / --hitl=never|auto
agent toolset                           # 列出工具集及状态
agent agents list                       # 多Agent 固定角色与 SubAgent 上限
agent skills list|add <yaml>|rm <name>  # 本地技能库管理
agent history session <id>|command <pat>|usage   # 历史查询 / 用量与成本聚合
agent tasks list|cancel                 # 后台任务视图（投递走 Runtime API）
agent snapshot list|restore             # 会话/工作区快照
agent eval run|baseline|list            # 评估与基线
agent version
```

chat 内斜杠命令：`/exit` `/hitl <mode>` `/remember <内容>` `/memory [query]` `/tool` `/skill`。

### Key Patterns

- **插件架构**：每个工具集（`Toolset`）定义可用工具和参数，注册于 `GSagent/plugins/toolsets/__init__.py` 的 `BUILTIN_PYTHON_TOOLSETS`（名字→工厂 dict，工厂签名 `(install_config) -> Toolset|None`）；核心引擎不依赖具体工具细节
- **组合根**：`Config` 是唯一装配点，`main.py` 只从这里拿拼好的对象（构造函数注入，不内部 new）；CLI 只消费 `StreamMessage` 事件流，不接触底层 Tool/Toolset（宪法 II）
- **配置向后兼容**：重命名字段时用 Pydantic `extra="allow"` + `model_validator` 映射旧名，不在 schema 中保留废弃字段（宪法 III；当前 `config.py` 用 dict + `_deep_merge`，尚未迁移 Pydantic，见 docs/implementation-notes.md）
- **类层次结构**：新增字段/方法时放在最通用的层级（如 `BaseAgent` 承载公共编排行为），不要因 issue 提到特定子类就限缩范围（宪法 V）
- **重试**：使用 `tenacity` 库，不要手写重试循环
- **企业级安全**：策略组件（path/command 守卫 + HITL 三态 + 审计）由 `Config.policy_components()` 统一构建并注入，HITL 支持运行时 `/hitl` 切换；审计 JSONL 全量留痕且密钥脱敏

### Investigation Flow

1. 加载用户输入
2. 选择相关工具/插件
3. 执行 LLM 调用
4. 收集数据
5. 分析结果并返回结论

## Configuration

- 配置文件：`./.GSagent/config.yaml`
- 配置段：`llm` / `agent` / `policy` / `runtime` / `cost` / `eval` / `memory` / `bash` / `sandbox` / `toolsets` / `multi_agent` / `observability`（OTel 开关，默认关）/ `guardrails`（输入/输出侧，默认开）
- 关键环境变量（`GSagent/config.py` 的 `_ENV_OVERRIDES` 声明式表）：
  - `AGENT_API_KEY`（回退 `OPENAI_API_KEY`）、`AGENT_MODEL`、`AGENT_BASE_URL`
  - `AGENT_MAX_STEPS`、`AGENT_MULTI_AGENT`（bool）、`AGENT_MAX_SUBAGENTS`
  - `AGENT_HITL_MODE`（auto|always|never）、`AGENT_WORKSPACE_ROOT`、`AGENT_RECORD_USAGE`、`AGENT_SERVE_PORT`
  - `AGENT_MEMORY_DB`、`AGENT_MEMORY_SESSIONS_DIR`、`AGENT_MEMORY_SCOPE`、`AGENT_MEMORY_USER`（记忆系统，见 `docs/memory.md`；`user` 默认系统登录用户，多用户隔离）
  - `AGENT_LOG_LEVEL`、`AGENT_LOG_FILE`、`AGENT_LOG_DIR`（日志，见 `GSagent/common/env_vars.py` —— 只放 Config 无归属的 import 期常量，模型/key 等配置不要在此重复声明）
  - `AGENT_OTEL_ENABLED` / `AGENT_OTEL_ENDPOINT` / `AGENT_OTEL_SERVICE_NAME` / `AGENT_OTEL_TRACE_CONTENT`（可观测，`observability` 段）、`AGENT_GUARDRAIL_INPUT` / `AGENT_GUARDRAIL_OUTPUT`（护栏开关，`guardrails` 段）

## Development Guidelines

**Git 工作流**：
- `git commit -s --no-verify`（sign off + 跳过本地 pre-commit）
- 只创建新 commit，不 amend；只 merge，不 rebase；只 push，不 force push
- 保持完整提交历史，方便回退

**代码规范**：
- 导入放在文件顶部，不在函数内部 import
- 类型注解必须（mypy 检查）
- **不要主动运行 pre-commit/ruff/mypy**，除非用户明确要求

**测试规范**：
- 新功能需要单元测试，新插件需要集成测试（宪法 IV，Test-First 硬性要求）
- 离线测试用 `ScriptedLLM` 打桩（不走 HTTP，无需 `responses`）
- LLM 相关测试打 `llm` marker 单独跑
- 测试文件结构与源码一致：`tests/` 镜像 `GSagent/`（当前 tests/ 正在迁移，见 Development Commands）

**文件结构**：
- 内置工具集：`GSagent/plugins/toolsets/{name}/`（Python 模块，工厂注册于 `BUILTIN_PYTHON_TOOLSETS`）
- YAML 工具集：`GSagent/plugins/toolsets/*.yaml`
- 提示词构建：`GSagent/core/prompts/`（Python 构建器，非 jinja2 模板）
- 测试：与源码结构一致

## Adding a New Integration

新增一个工具集/集成时，同步更新以下文件：

1. `README.md` — 功能列表表格
2. `docs/{name}.md` — 专属文档页
3. `mkdocs.yml` — `nav:` 段注册（docs 为平铺结构，无 `.nav.yml`）
4. 契约文档：`specs/001-multi-agent-cli/contracts/` 与 `specs/002-enterprise-cli-upgrade/contracts/`（如涉及 CLI/配置契约）

## Documentation

### MkDocs 导航

- 项目用 `mkdocs.yml` 的 `nav:` 段显式声明导航，**不**使用 awesome-nav/`.nav.yml`
- 新增页面必须在 `mkdocs.yml` 的 `nav:` 中注册

### 文档编写规范

- **列表前空行**：header 和列表之间必须空一行，否则 MkDocs 不渲染
- **Tab 内不用 header**：`=== "Tab"` 内用 `**粗体**` 代替 `### header`
- **避免过多 header**：小步骤用粗体或代码注释代替独立 header
- **不写行为描述**：不写 "工具会做 X → Y → Z"，给 prompt 示例即可
- **不写 Capabilities 列表**：功能列表容易过时，让用户自己发现
- **不写 Security Best Practices**：假设用户知道基本安全常识

### URL 变更

改名或移动文档页面时，`grep -rn` 全仓库搜索旧 URL 和旧锚点，更新所有引用（docs/\*.md、Python 源码、README、代码注释）。
