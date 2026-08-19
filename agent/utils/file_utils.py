"""File utilities for the agent."""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def write_json_file(path: str, data: Any) -> None:
    """Write data as pretty-printed JSON to a file.

    Args:
        path: Destination file path.
        data: JSON-serializable data.
    """
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"Wrote JSON output to {file_path}")
