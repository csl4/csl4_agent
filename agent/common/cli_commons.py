"""Shared CLI option definitions.

Options that appear in multiple commands are defined once here and reused,
following the HolmesGPT cli_commons pattern. The defaults for options that
are also in the config file MUST be None, or the CLI defaults will override
settings in the config file.
"""

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
