"""Base configuration model for toolsets with backward-compatible field mapping."""

from typing import Any, ClassVar, Dict, Optional

from pydantic import BaseModel, ConfigDict, model_validator


class ToolsetConfig(BaseModel):
    """Base class for toolset configuration.

    Supports backward-compatible field renames via `_deprecated_mappings`:
        - Key maps to new field name → old field value is migrated
        - Key maps to None → old field is removed with a warning
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