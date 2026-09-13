"""评估体系（002-enterprise-cli-upgrade US5 T040，FR-013，R-11）。

- dataset.py：EvalCase YAML 加载（case_id/prompt/expected/tags）
- metrics.py：完成率/幻觉率/误拒绝率 + 基线对比 + 错误案例回流
- runner.py：评估执行（ScriptedLLM 离线回归 / 真实 LLM 联网验收）
"""
