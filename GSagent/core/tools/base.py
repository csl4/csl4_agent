"""工具基类（Transformer + Tool）：模板方法固化「审批→校验→执行→变换→返回」。"""


# ======================= 中文导览 =======================
# 本文件是「行为对象 / 机器」的基座：定义工具的抽象骨架。
# 核心类：
#   Transformer(ABC) → 工具结果的变换器（截断/精简），在 _invoke() 成功后依次应用
#   Tool(ABC)        → 所有工具的基类。【模板方法】invoke() 固化五步固定流程，
#                       子类只需填 _invoke() 一个洞。
# =========================================================


import fnmatch
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from GSagent.core.models import (
    ApprovalRequirement,
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolInvokeContext,
    ToolParameter,
)


# ---- 行为对象：结果变换器（可插拔的「后处理」注解点）----
# 输入：一个 StructuredToolResult；输出：变换后的 StructuredToolResult。
# 设计要点：挂到 Tool.transformers 上，在 _invoke() 成功后由 invoke() 依次调用；
#           让「结果瘦身/截断」这类横切关注点与工具核心逻辑解耦。
class Transformer(ABC, BaseModel):
    """工具结果变换器的基类。"""

    @abstractmethod
    def transform(self, result: StructuredToolResult) -> StructuredToolResult:
        """变换工具结果。在 _invoke() 成功后调用。"""
        ...


# ======================= 核心行为对象：Tool（所有工具基类）=======================
# 输入：params(Dict) + ToolInvokeContext；输出：StructuredToolResult。
# 设计理念（模板方法模式）：invoke() 把【固定流程】锁死，子类只填 _invoke() 一个洞。
# 固定顺序（invoke()）：① 审批检查（user_approved=False 时）→ ② 参数强转 _coerce_params()
#                      → ③ _invoke()（子类实现）→ ④ 成功时套 transformers → ⑤ 返回。
# 好处：所有工具的「审批→校验→执行→清理→返回」流程完全一致，杜绝某人手写乱序造成安全漏洞。
# 附带能力：to_openai_tool() 把 Tool 转成 OpenAI 函数定义，供 LLM 识别。
class Tool(ABC, BaseModel):
    """所有工具的抽象基类。

    子类实现 _invoke() 以提供实际的工具逻辑。
    invoke() 模板方法处理审批、参数强转与 transformers。
    """

    name: str
    description: str
    parameters: Dict[str, ToolParameter] = Field(default_factory=dict)
    transformers: Optional[List[Transformer]] = None

    class Config:
        """Tool 的 Pydantic 配置。"""

        arbitrary_types_allowed = True

    # 【模板方法】invoke() 固定五步：审批 → 强转 → _invoke → transformers → 返回。
    # 子类【不要】覆写它，只覆写 _invoke()。
    def invoke(self, params: Dict[str, Any], context: ToolInvokeContext) -> StructuredToolResult:
        """工具调用的模板方法。

        顺序：
        1. 审批检查（若 user_approved 为 False）
        2. 强转参数类型
        3. 调用 _invoke()
        4. 应用 transformers（SUCCESS 时）
        5. 返回结果
        """
        # 1. Approval check — if params are tainted (from LLM), verify approval
        if not context.user_approved:
            context.tool_name = self.name
            approval = self.requires_approval(params, context)
            if approval and approval.needs_approval:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.APPROVAL_REQUIRED,
                    params=params,
                    error=approval.reason,
                    prefixes_to_save=approval.prefixes_to_save,
                )

        # 2. Coerce parameter types
        coerced = self._coerce_params(params)

        # 3. Execute the tool
        try:
            result = self._invoke(coerced, context)
        except Exception as e:
            result = StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Tool execution failed: {e}",
                params=coerced,
            )

        # 4. Apply transformers on success
        if result.status == StructuredToolResultStatus.SUCCESS and self.transformers:
            for transformer in self.transformers:
                result = transformer.transform(result)

        return result

    # ---- 子类唯一要填的洞：真正的工具逻辑写在这里 ----
    # 审批/强转/transformer 都由基类统一处理，子类只管「干活」。
    @abstractmethod
    def _invoke(self, params: Dict[str, Any], context: ToolInvokeContext) -> StructuredToolResult:
        """子类在此实现实际的工具逻辑。"""
        ...

    # 审批钩子：默认按 tools 集的 approval_required_tools 通配符匹配；子类可覆写为
    # 「更细粒度」的判断（如 Bash 只对危险命令要审批）。返回 ApprovalRequirement 表示要暂停。
    def requires_approval(
        self, params: Dict[str, Any], context: ToolInvokeContext
    ) -> Optional[ApprovalRequirement]:
        """工具专属的审批检查。

        默认实现会执行工具集的 approval_required_tools 模式匹配
        （针对工具名的 fnmatch glob 通配）。子类可覆写此方法以实现
        更细粒度的逻辑——例如 Bash 工具可能只对危险命令要求审批，
        HTTP 工具只对非 GET 请求要求审批。
        """
        toolset = getattr(context, "toolset", None)
        if toolset is None:
            return None

        for pattern in (toolset.approval_required_tools or []):
            if fnmatch.fnmatch(self.name, pattern):
                return ApprovalRequirement(
                    needs_approval=True,
                    reason=f"Tool '{self.name}' matches approval pattern '{pattern}'.",
                    tool_name=self.name,
                    params=params,
                )
        return None

    def _coerce_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """将参数值强转为声明的类型。"""
        coerced: Dict[str, Any] = {}
        for name, param_def in self.parameters.items():
            if name not in params:
                if param_def.default is not None:
                    coerced[name] = param_def.default
                continue

            value = params[name]
            if param_def.type == "integer":
                try:
                    coerced[name] = int(value)
                except (ValueError, TypeError):
                    coerced[name] = value
            elif param_def.type == "number":
                try:
                    coerced[name] = float(value)
                except (ValueError, TypeError):
                    coerced[name] = value
            elif param_def.type == "boolean":
                if isinstance(value, str):
                    coerced[name] = value.lower() in ("true", "1", "yes")
                else:
                    coerced[name] = bool(value)
            else:
                coerced[name] = value

        return coerced

    def to_openai_tool(self) -> Dict[str, Any]:
        """转换为 OpenAI 兼容的工具定义。"""
        # 输出：OpenAI 格式的 function 定义（name/description/parameters），
        #      供 LLM 渲染 tools 参数表。这是「内部类 → LLM 协议」的适配点之一。
        properties: Dict[str, Any] = {}
        required: List[str] = []

        for name, param in self.parameters.items():
            prop: Dict[str, Any] = {
                "type": param.type,
                "description": param.description,
            }
            if param.enum:
                prop["enum"] = param.enum
            if param.default is not None:
                prop["default"] = param.default
            if param.type == "array" and param.items is not None:
                prop["items"] = {
                    "type": param.items.type,
                    "description": param.items.description,
                }

            properties[name] = prop
            if param.required:
                required.append(name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }


__all__ = ["Tool", "Transformer"]
