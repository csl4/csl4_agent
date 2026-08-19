# Docs 模板说明

基于 HolmesGPT 的 MkDocs Material 文档站结构提炼。复制以下文件到你的项目即可快速搭建文档站。

## 快速开始

```bash
# 1. 复制模板文件
cp mkdocs-template.yml mkdocs.yml
cp -r docs-template/ docs/

# 2. 安装依赖
pip install mkdocs-material mkdocs-glightbox mkdocs-awesome-nav

# 3. 启动本地预览
mkdocs serve
```

## 目录结构

```
docs/
├── .nav.yml              # 根导航（可选，awesome-nav 插件用）
├── index.md              # 首页
├── CNAME                 # 自定义域名（如 example.com）
├── installation/         # 安装指南
│   ├── .nav.yml
│   └── *.md
├── reference/            # API 参考、环境变量、故障排查
│   ├── .nav.yml
│   └── *.md
├── assets/               # 图片、GIF、logo
├── overrides/            # 覆盖 MkDocs 主题模板
│   └── partials/
├── stylesheets/          # 自定义 CSS
├── javascripts/          # 自定义 JS
└── snippets/             # 可复用的 markdown 片段
```

## 导航管理

### 方式 A：awesome-nav 插件（推荐，HolmesGPT 用这个）

每个子目录一个 `.nav.yml`，`mkdocs.yml` 的 `nav:` 段可留空或被忽略：

```yaml
# docs/.nav.yml
nav:
  - index.md
  - Installation: installation
  - Reference: reference
```

```yaml
# docs/reference/.nav.yml
nav:
  - API Reference: api.md
  - Environment Variables: env-vars.md
  - Troubleshooting: troubleshooting.md
```

### 方式 B：纯 mkdocs.yml（简单项目推荐）

所有导航写在 `mkdocs.yml` 的 `nav:` 段中，不需要 `.nav.yml` 文件：

```yaml
nav:
  - Home: index.md
  - Installation:
      - CLI: installation/cli.md
      - Docker: installation/docker.md
  - Reference:
      - API: reference/api.md
      - FAQ: reference/faq.md
```

## 编写规范

1. **列表前空行**：header 和列表之间必须空一行，否则 MkDocs 不渲染
2. **Tab 内不用 header**：`=== "Tab"` 内用 `**粗体**` 代替 `### header`
3. **避免过多 header**：小步骤用粗体或代码注释代替独立 header
4. **不写行为描述**：不写 "工具会做 X → Y → Z"，给 prompt 示例即可
5. **不写 Capabilities 列表**：功能列表容易过时，让用户自己发现
6. **不写 Security Best Practices**：假设用户知道基本安全常识

## URL 变更时

改名或移动文档页面时，`grep -rn` 全仓库搜索旧 URL 和旧锚点，更新所有引用：
- `docs/*.md` 中的相对链接
- Python 源码中的错误消息和提示
- `README.md` 和代码注释

## 自定义域

如果你需要多区域/多环境支持（如 `api.example.com` / `api.example.eu`），参考 `docs/custom_fences.py` 实现自定义 fence。

## 同步 Checklist

新增一个功能/集成时，同步更新：
1. `README.md` — 功能列表
2. `docs/xxx/index.md` — 分类列表页
3. `docs/xxx/{name}.md` — 专属文档页
4. 导航文件（`.nav.yml` 或 `mkdocs.yml`）