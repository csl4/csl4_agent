# Quickstart 验证指南: 多Agent CLI 框架

**Date**: 2026-09-02 | **Phase**: 1（/speckit-plan 输出）

> 本文件是**验证/运行指南**：证明特性端到端可用。实现细节见 `tasks.md` 与实现阶段。
> 契约细节见 [contracts/cli.md](./contracts/cli.md)、[contracts/a2a.md](./contracts/a2a.md)、
> [contracts/config.md](./contracts/config.md)；数据模型见 [data-model.md](./data-model.md)。

## 前置条件

- 已安装依赖（`poetry install`），当前分支 `001-multi-agent-cli`。
- LLM 配置就绪（`~/.agent/config.yaml` 或环境变量 `AGENT_MODEL`/`AGENT_API_KEY`）。
- 目标终端：Windows PowerShell / bash（Git Bash）/ zsh 任一可用。

## 场景 1（P1）: 多Agent 协作执行混合任务

```bash
# 启用多Agent 模式并进入交互会话
agent chat --multi-agent
```

**输入**: "分析当前目录的磁盘占用并清理超过 100MB 的临时文件"

**预期结果**:
1. 主 Agent 受理任务，编排 Agent 拆解为子任务，业务 Agent 处理决策，SubAgent 执行命令
   （可在事件流/`--verbose` 看到任务拆解与 SubAgent 启停）。
2. 命令经现有 bash toolset 执行（高风险命令触发确认，见场景 2）。
3. 系统在一次回复内返回结构化结果（SC-006），说明执行了哪些命令、结果如何。
4. 全程无需用户手动拆解命令（FR-001）。

**通过标准**: SC-001（≤5 分钟端到端）、SC-002（混合任务一次成功率 ≥80%）。

## 场景 2（P2）: 安全执行与环境适配

```bash
agent chat --multi-agent
# 输入一个含高风险命令的任务，例如删除目录
```

**预期结果**:
1. 高风险命令在执行前弹出确认请求；选择拒绝 → 不执行（FR-003，SC-003 0 例未授权）。
2. 在 PowerShell / bash / zsh 下分别运行同义命令 → 自动使用适配当前终端的写法（FR-002, SC-004）。
3. 沙箱不可用或命令失败 → 返回明确错误提示，不静默（FR-004）。

**通过标准**: SC-003（高风险 100% 确认/拦截）、SC-004（三终端开箱即用）。

## 场景 3（P3）: 技能注入与历史查询

```bash
# 添加一个技能
agent skills add examples/init-skill.yaml
agent chat --multi-agent
# 下达与"项目初始化"技能匹配的任务
```

**预期结果**:
1. 系统自动加载并应用匹配技能，回复中说明使用了该技能（FR-006）。
2. 询问"当前目录/可用工具" → 回答与真实环境一致（FR-007）。
3. `agent history session <id>` / `agent history command <pattern>` → 2 秒内返回记录（FR-008, SC-005）。

**通过标准**: SC-005（90% 查询 ≤2 秒）。

## 自动化验证

```bash
# 单元 + 集成测试（无 LLM 调用）
poetry run pytest tests/unit tests/integration -m "not llm" --no-cov

# 契约/HTTP mock（responses）
poetry run pytest -k "a2a or contract" --no-cov

# LLM 相关（需 API 密钥，单独跑）
poetry run pytest tests/llm/ -n 6 --no-cov
```

**预期**: 单元/集成测试全绿；`llm` 标记测试不阻塞本地离线验证。

## 手动端到端检查表

- [ ] `agent --help` 显示新增子命令（agents/skills/history）且旧命令不变。
- [ ] 不带 `--multi-agent` 时行为与旧版一致（向后兼容）。
- [ ] 场景 1/2/3 的手动预期结果逐条满足。
- [ ] 用户 Ctrl-C 中断后无残留进程（Edge Case：清理已启动进程）。
