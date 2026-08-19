"""Environment variable constants."""

import os

# --- LLM Configuration ---
API_KEY = os.getenv("AGENT_API_KEY", os.getenv("OPENAI_API_KEY", ""))
MODEL = os.getenv("AGENT_MODEL", "gpt-4o")
BASE_URL = os.getenv("AGENT_BASE_URL", "")

# --- Agent Configuration ---
MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "20"))
TOOL_RESULTS_DIR = os.getenv("AGENT_TOOL_RESULTS_DIR", "/tmp/agent_tool_results")

# --- Logging ---
LOG_LEVEL = os.getenv("AGENT_LOG_LEVEL", "INFO")

# --- Server ---
SERVER_HOST = os.getenv("AGENT_SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("AGENT_SERVER_PORT", "8000"))