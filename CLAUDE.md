# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

通用 LLM Agent 框架（Python 3.10+）：CLI 交互（Typer）+ 工具执行（插件化 Toolset/Tool）+ 多Agent 编排（A2A 协议，主/编排/业务/动态 SubAgent 四角色）。本地优先、离线可测，跨终端（PowerShell / bash / zsh）自动适配。

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

**uv 说明**：依赖管理用 uv 的 pip 兼容接口（`uv pip`），目标是**当前激活的环境**（本机为 conda `base_llm`），不建 `.venv`、无 `uv.lock`。本机 base_llm 的 site-packages 对普通用户只读，首次安装前需在管理员 PowerShell 授权一次：`icacls "D:\anaconda\envs\base_llm\Lib\site-packages" /grant "%USERNAME%:(OI)(CI)M"`。依赖声明在 `pyproject.toml`（PEP 621 `[project]` + `[project.optional-dependencies]`），不要用 Poetry 格式。

## Architecture Overview

### Core Components

| 模块 | 路径 | 职责 |
|---|---|---|
| CLI 入口 | `agent/main.py` | 命令路由、配置加载（`chat`/`run`/`serve`/`toolset`/`agents`/`skills`/`history`/`version`） |
| 配置 | `agent/config.py` | 组合根：`create_llm` / `create_tool_executor` / `create_tool_calling_llm`；四层覆盖（默认←YAML←环境变量←CLI） |
| 核心引擎 | `agent/core/` | 主循环（`tool_calling_llm.py`）、工具执行器（`tool_executor.py`）、多Agent 角色、A2A、提示词、截断、供应商 |
| 插件系统 | `agent/plugins/toolsets/` | 工具集插件（bash/filesystem/sandbox），注册于 `BUILTIN_PYTHON_TOOLSETS` |

### Key Patterns

- **插件架构**：每个工具集（`Toolset`）定义可用工具和参数，注册于 `agent/plugins/toolsets/__init__.py` 的 `BUILTIN_PYTHON_TOOLSETS`（名字→工厂 dict）；核心引擎不依赖具体工具细节
- **配置向后兼容**：重命名字段时使用 Pydantic `extra="allow"` + `model_validator` 映射旧名，不在 schema 中保留废弃字段（宪法 III；当前 `config.py` 用 dict + `_deep_merge`，尚未迁移 Pydantic，见 docs/implementation-notes.md）
- **类层次结构**：新增字段/方法时放在最通用的层级（如 `BaseAgent` 承载公共编排行为），不要因 issue 提到特定子类就限缩范围
- **重试**：使用 `tenacity` 库，不要手写重试循环
- **CLI 契约**：CLI 只消费 `StreamMessage` 事件流，不接触底层 Tool/Toolset（宪法 II）

### Investigation Flow

1. 加载用户输入
2. 选择相关工具/插件
3. 执行 LLM 调用
4. 收集数据
5. 分析结果并返回结论

## Configuration

- 配置文件：`~/.agent/config.yaml`
- 关键环境变量（`agent/config.py` 的 `_ENV_OVERRIDES` 声明式表）：
  - `AGENT_API_KEY`（回退 `OPENAI_API_KEY`）、`AGENT_MODEL`、`AGENT_BASE_URL`
  - `AGENT_MAX_STEPS`、`AGENT_MULTI_AGENT`（bool）、`AGENT_MAX_SUBAGENTS`
  - `AGENT_LOG_LEVEL`、`AGENT_LOG_FILE`、`AGENT_LOG_DIR`（日志，见 `agent/common/env_vars.py`）

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
- 新功能需要单元测试，新插件需要集成测试
- 离线测试用 `tests/helpers.py` 的 `ScriptedLLM` 打桩（不走 HTTP，无需 `responses`）
- LLM 相关测试打 `llm` marker 单独跑
- 测试文件结构与源码一致：`tests/` 镜像 `agent/`

**文件结构**：
- 内置工具集：`agent/plugins/toolsets/{name}/`（Python 模块，工厂注册于 `BUILTIN_PYTHON_TOOLSETS`）
- YAML 工具集：`agent/plugins/toolsets/*.yaml`
- 提示词构建：`agent/core/prompts/`（Python 构建器，非 jinja2 模板）
- 测试：与源码结构一致

## Adding a New Integration

新增一个工具集/集成时，同步更新以下文件：

1. `README.md` — 功能列表表格
2. `docs/{name}.md` — 专属文档页
3. `mkdocs.yml` — `nav:` 段注册（docs 为平铺结构，无 `.nav.yml`）
4. 契约文档：`specs/001-multi-agent-cli/contracts/`（如涉及 CLI/配置契约）

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
