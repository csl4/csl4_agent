"""工具参数类型定义（值对象）。"""

from typing import Any, List, Optional

from pydantic import BaseModel


# ---- 值对象：单个工具参数的「类型定义」----
# 用途：描述一个工具参数的长相（类型/描述/是否必填/默认值/枚举/数组元素）。
# 谁创建：工具类在定义 parameters 字段时写死；谁消费：tool_calling_llm / to_openai_tool()。
class ToolParameter(BaseModel):
    """工具的 JSON Schema 参数定义。"""

    type: str = "string"
    description: str = ""
    required: bool = False
    default: Optional[Any] = None
    enum: Optional[List[str]] = None
    # For type="array": the schema of each element (e.g. ToolParameter(type="string")).
    items: Optional["ToolParameter"] = None


__all__ = ["ToolParameter"]
