"""
CLI 已批准前缀的持久化。

本模块负责从 ~/.agent/bash_approved_prefixes.yaml 加载和保存 CLI 已批准的
bash 命令前缀。

注意：这仅适用于 CLI 模式。server 模式使用消息元数据传递会话前缀。
CLI 模式必须通过调用 enable_cli_mode() 显式启用——这样可避免 server 模式下
多余的文件 I/O。
"""

import logging
import os
from pathlib import Path
from typing import List

import yaml

# Same directory as the agent config (~/.agent). Defined locally instead of
# importing from agent.config to avoid a circular import
# (agent.config -> plugins.toolsets -> this module).
# ======================= 中文导览 =======================
# 【CLI 免批前缀的持久化】：把用户在一次交互会话里「记住以后免批」的前缀写到磁盘，
#   下次还要用。CLI 专用——server 模式用 message 元数据传会话前缀，不走文件。
# 关键：必须显式 enable_cli_mode() 才开文件读写，免得 server 模式白做 I/O。
# 数据流：
#   enable_cli_mode()      → CLI 启动时开开关（唯一入口）。
#   load_cli_..._prefixes() → 读回已记住的前缀（bash_toolset._merge_cli_approved_prefixes()
#                             在每次校验前合并进工具的 allow 名单）。
#   save_cli_..._prefixes() → 审批通过并勾选「记住」时，把新前缀并进既有集合写回。
# 注意：PREFIXES_FILE 路径不 import agent.config（避免 config → toolsets 循环导入）。

PREFIXES_FILE = Path.home() / ".agent" / "bash_approved_prefixes.yaml"

# CLI mode flag - only when enabled will we read from file
_cli_mode_enabled = False


def enable_cli_mode() -> None:
    """
    为前缀加载启用 CLI 模式。

    在交互式 CLI 会话开始时调用本函数以启用基于文件的前缀加载。
    server 模式不应调用本函数。
    """
    global _cli_mode_enabled
    _cli_mode_enabled = True


def is_cli_mode() -> bool:
    """检查 CLI 模式是否已启用。"""
    return _cli_mode_enabled


def load_cli_bash_tools_approved_prefixes() -> List[str]:
    """
    从 ~/.agent/bash_approved_prefixes.yaml 加载已批准前缀。

    若 CLI 模式未启用（server 模式）则返回空列表，避免不必要的文件 I/O。
    """
    if not _cli_mode_enabled:
        return []

    if PREFIXES_FILE.exists():
        try:
            with open(PREFIXES_FILE, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if isinstance(data, dict) and "approved_prefixes" in data:
                    return data["approved_prefixes"]
        except Exception as e:
            logging.warning(f"Failed to load approved prefixes: {e}")
    return []


def save_cli_bash_tools_approved_prefixes(prefixes: List[str]) -> None:
    """
    将已批准前缀保存到 ~/.agent/bash_approved_prefixes.yaml。

    注意：本函数与 CLI 模式无关，因为保存只会在交互式审批流程中调用，
    而该流程本质上属于 CLI。
    """
    prefixes_file = str(PREFIXES_FILE)
    os.makedirs(os.path.dirname(prefixes_file), exist_ok=True)

    # Load existing prefixes and merge (bypass CLI mode check for internal use)
    existing: set = set()
    if os.path.exists(prefixes_file):
        try:
            with open(prefixes_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if isinstance(data, dict) and "approved_prefixes" in data:
                    existing = set(data["approved_prefixes"])
        except Exception:
            pass

    updated = sorted(set(prefixes) | existing)

    try:
        with open(prefixes_file, "w", encoding="utf-8") as f:
            yaml.safe_dump({"approved_prefixes": updated}, f, default_flow_style=False)
    except Exception as e:
        logging.error(f"Failed to save approved prefixes: {e}")
