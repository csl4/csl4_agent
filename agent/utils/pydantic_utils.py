"""工具集的基础配置模型，支持向后兼容的字段映射。"""

# ======================= 中文导览 =======================
# 工具集配置的【公共基类 / 配置文件迁移器】：
# ToolsetConfig（Pydantic BaseModel，extra="allow"）是所有工具集配置
#   （BashExecutorConfig / FilesystemToolConfig…）的父类，多接未知字段不报错。
# 向后兼容：子类声明 _deprecated_mappings = { 旧字段名: 新字段名 or None }，
#   model_validator(before) 在实例化时
#     · 旧字段值迁移到新字段名（若新字段没给）。
#     · 映射到 None 的旧字段 → 警告后丢弃（从 schema 中删除废弃字段，
#       CLAUDE.md 「配置向后兼容」约定的实现）。
# 设计理念：兼容旧配置，但不在新 schema 里保留废弃字段——消灭漂移。
# =========================================================

from typing import Any, ClassVar, Dict, Optional

from pydantic import BaseModel, ConfigDict, model_validator


class ToolsetConfig(BaseModel):
    """工具集配置的基类。

    通过 `_deprecated_mappings` 支持向后兼容的字段重命名：
        - 键映射到新字段名 → 旧字段值被迁移
        - 键映射到 None → 旧字段被移除并发出警告
    """

    model_config = ConfigDict(extra="allow")

    _deprecated_mappings: ClassVar[Dict[str, Optional[str]]] = {}

    @model_validator(mode="before")
    @classmethod
    def _handle_deprecated_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        for old_name, new_name in cls._deprecated_mappings.items():
            if old_name in data:
                if new_name is None:
                    # Field is removed — warn and discard
                    import warnings

                    warnings.warn(
                        f"'{old_name}' is deprecated and has been removed. "
                        f"Its value will be ignored.",
                        DeprecationWarning,
                        stacklevel=2,
                    )
                    data.pop(old_name)
                elif new_name not in data:
                    # Migrate old field value to new field
                    data[new_name] = data.pop(old_name)

        return data