"""共享的 CLI 选项定义。

出现在多个命令中的选项在此定义一次并复用，
遵循 HolmesGPT 的 cli_commons 模式。同时存在于配置文件中的选项，
其默认值必须为 None，否则 CLI 默认值会覆盖配置文件中的设置。
"""

# ======================= 中文导览 =======================
# 跨命令复用的 Typer 选项定义（CLI 参数 → Config 装配的入口之一）。
# opt_* 一批：--api-key / --model / --base-url / --config / --max-steps / --verbose / --no-compaction / --json-output-file。
# 铁律：凡也存在于 config 文件的选项，默认值【必须是 None】——
#   CLI 非空值才覆盖配置文件，否则 CLI 默认值会压掉配置（见 agent/main.py 装配）。
# 数据流：CLI 键入 → opt_* 变量 → Config 构造函数 → create_llm / create_tool_calling_llm。
# =========================================================

from pathlib import Path
from typing import List, Optional

import typer

# LLM API key (falls back to env vars / config file)
opt_api_key: Optional[str] = typer.Option(
    None,
    "--api-key",
    help="API key for the LLM provider (if not given, uses AGENT_API_KEY / OPENAI_API_KEY env or config file)",
)

# LLM model name
opt_model: Optional[str] = typer.Option(
    None,
    "--model",
    "-m",
    help="Model to use for the LLM",
)

# Base URL for the LLM API
opt_base_url: Optional[str] = typer.Option(
    None,
    "--base-url",
    help="Base URL for the LLM API (for OpenAI-compatible providers)",
)

# Config file path
opt_config_file: Optional[Path] = typer.Option(
    None,
    "--config",
    "-c",
    help="Path to the config file. Defaults to ~/.agent/config.yaml when it exists. CLI arguments take precedence over config file settings",
)

# Maximum agent iterations
opt_max_steps: Optional[int] = typer.Option(
    None,
    "--max-steps",
    help="Advanced. Maximum number of steps the LLM can take to investigate the issue",
)

# Verbose flag (repeatable: -v, -vv, -vvv)
opt_verbose: Optional[List[bool]] = typer.Option(
    [],
    "--verbose",
    "-v",
    help="Verbose output. Pass multiple times to increase verbosity (-v/-vv/-vvv)",
)

# Disable context compaction
opt_no_compaction: bool = typer.Option(
    False,
    "--no-compaction",
    help="Disable automatic context compaction when the conversation nears the context window limit",
)

# JSON output file
opt_json_output_file: Optional[str] = typer.Option(
    None,
    "--json-output-file",
    help="Save the complete output in JSON format to a file",
    envvar="AGENT_JSON_OUTPUT_FILE",
)
