"""agent 的文件工具。"""

# ======================= 中文导览 =======================
# 轻量文件工具：把 JSON 写盘（当前唯一用途）。
# write_json_file(path, data)：路径不存在则建父目录，ensure_ascii=False 保留中文、
#   default=str 兜底非序列化对象；供 agent/main.py 的 save 命令落地对话记录。
# =========================================================

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def write_json_file(path: str, data: Any) -> None:
    """将数据以美化（pretty-print）格式的 JSON 写入文件。

    参数:
        path: 目标文件路径。
        data: 可 JSON 序列化的数据。
    """
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"Wrote JSON output to {file_path}")
