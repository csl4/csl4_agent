"""Logging configuration for the agent."""

import logging
import sys

from agent.common import LOG_LEVEL


def setup_logging(level: str | None = None, format_string: str | None = None) -> None:
    """Configure the root logger for the agent.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL). Defaults to AGENT_LOG_LEVEL env var.
        format_string: Custom log format. Defaults to ISO timestamp + level + logger + message.
    """
    if level is None:
        level = LOG_LEVEL

    if format_string is None:
        format_string = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(format_string))

    root = logging.getLogger("agent")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()
    root.addHandler(handler)