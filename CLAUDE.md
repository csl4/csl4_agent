# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

<!-- TODO: 一句话描述你的项目 -->

## Development Commands

```bash
# 安装依赖
poetry install
poetry install --with dev

# 运行测试
poetry run pytest tests -m "not llm"          # 非 LLM 测试
poetry run pytest tests/llm/ -n 6 --no-cov    # LLM 测试（并行）
poetry run pytest -k "test_name" --no-cov     # 运行单个测试

# 代码质量（仅在用户明确要求时运行）
poetry run ruff format
poetry run ruff check --fix
poetry run mypy
```

**Poetry 版本注意**：锁定依赖时使用 Poetry 1.8.x（`poetry lock --no-update`），避免 Poetry 2.x 重写整个 lockfile。

## Architecture Overview

### Core Components

<!-- TODO: 列出你的核心模块和职责 -->

| 模块 | 路径 | 职责 |
|---|---|---|
| CLI 入口 | `xxx/main.py` | 命令路由、配置加载 |
| 核心引擎 | `xxx/core/` | 主要业务逻辑 |
| 插件系统 | `xxx/plugins/` | 可扩展的插件接口 |

### Key Patterns

<!-- TODO: 列出项目中最重要的 3-5 个设计模式 -->

- **插件架构**：每个插件定义可用工具和参数，动态加载，可通过配置文件自定义
- **配置向后兼容**：重命名字段时使用 Pydantic `extra="allow"` + `model_validator` 映射旧名，不在 schema 中保留废弃字段
- **类层次结构**：新增字段/方法时放在最通用的层级，不要因 issue 提到特定子类就限缩范围
- **重试**：使用 `tenacity` 库，不要手写重试循环

### Investigation Flow

<!-- TODO: 描述核心业务流程的 3-6 个步骤 -->

1. 加载用户输入
2. 选择相关工具/插件
3. 执行 LLM 调用
4. 收集数据
5. 分析结果并返回结论

## Configuration

- 配置文件：`~/.xxx/config.yaml`
- 关键环境变量：`API_KEY`, `MODEL`, `RUN_LIVE`

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
- HTTP mock 用 `responses` 库，不要用 `@patch("requests.get")`
- 测试文件结构与源码一致：`tests/` 镜像 `src/`

**文件结构**：
- 插件：`xxx/plugins/{name}.yaml` 或 `xxx/plugins/{name}/`
- 模板：`xxx/plugins/prompts/{name}.jinja2`
- 测试：与源码结构一致

## Adding a New Integration

<!-- TODO: 根据你的项目调整这个 checklist -->

新增一个集成/插件时，同步更新以下文件：

1. `README.md` — 功能列表表格
2. `docs/xxx/index.md` — 分类列表页
3. `docs/xxx/{name}.md` — 专属文档页
4. 导航文件（`.nav.yml` 或 `mkdocs.yml`）

## Documentation

### MkDocs 导航

- 使用 `awesome-nav` 插件时，导航由每个子目录的 `.nav.yml` 控制，**不是** `mkdocs.yml` 的 `nav:` 段
- 新增页面必须在对应目录的 `.nav.yml` 中注册

### 文档编写规范

- **列表前空行**：header 和列表之间必须空一行，否则 MkDocs 不渲染
- **Tab 内不用 header**：`=== "Tab"` 内用 `**粗体**` 代替 `### header`
- **避免过多 header**：小步骤用粗体或代码注释代替独立 header
- **不写行为描述**：不写 "工具会做 X → Y → Z"，给 prompt 示例即可
- **不写 Capabilities 列表**：功能列表容易过时，让用户自己发现
- **不写 Security Best Practices**：假设用户知道基本安全常识

### URL 变更

改名或移动文档页面时，`grep -rn` 全仓库搜索旧 URL 和旧锚点，更新所有引用（docs/\*.md、Python 源码、README、代码注释）。

<!-- TODO: 如果你有自己的多区域/多环境需求，在这里添加说明 -->