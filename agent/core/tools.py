"""工具与工具集的基类，用于 agent 插件系统。"""


# ======================= 中文导览 =======================
# 本文件是「行为对象 / 机器」的基座：定义工具与工具集的抽象骨架。
# 核心类：
#   Transformer(ABC) → 工具结果的变换器（截断/精简），在 _invoke() 成功后依次应用
#   Tool(ABC)        → 所有工具的基类。【模板方法】invoke() 固化五步固定流程，
#                       子类只需填 _invoke() 一个洞。
#   Toolset          → 一组相关工具的集合，携带共享 config / 前置条件 / 审批模式 / 标签
# =========================================================


import fnmatch
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union

from pydantic import BaseModel, Field

from agent.core.models import (
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


# ---- 枚举：工具集类型 / 生命周期状态 / 分类标签 ----
# 类型决定 Loader 如何解释工具集；状态决定是否加载；标签用于按运行模式过滤工具集。
class ToolsetType(str, Enum):
    """工具集实现的类型。"""

    YAML = "YAML"
    PYTHON = "PYTHON"
    HTTP = "HTTP"
    MCP = "MCP"


class ToolsetStatusEnum(str, Enum):
    """工具集生命周期状态。"""

    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    FAILED = "FAILED"


class ToolsetTag(str, Enum):
    """用于对工具集进行分类的标签。"""

    CORE = "CORE" #核心模块
    CLUSTER = "CLUSTER" # 集群模块
    CLI = "CLI" # cli 模块


# ---- 前置条件：工具集启动前「环境是否具备」的检查 ----
# 设计要点：Prerequisite 是基类；CallablePrerequisite 用 callable 实现，便于把任意
#           环境检查（如 root_dir 是否存在）声明成工具集的前置依赖，首次使用才检查（懒加载）。
class Prerequisite(BaseModel):
    """工具集前置条件的基类。"""

    name: str
    description: str = ""

    def check(self, config: Any) -> bool:
        """检查前置条件是否满足。在子类中覆写。"""
        return True


class CallablePrerequisite(Prerequisite):
    """由可调用函数支撑的前置条件。"""

    callable: Callable[[Any], bool]

    def check(self, config: Any) -> bool:
        """执行可调用函数以检查前置条件。"""
        try:
            return self.callable(config)
        except Exception:
            return False


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


# ---- 行为对象：工具集（一组相关工具 + 共享配置/前置/审批/标签）----
# 输入：由各 create_xxx_toolset() 工厂构建；输出：被 ToolExecutor 注册并索引其中的工具。
# 关键职责：check_prerequisites() 决定是否启用；approval_required_tools 控制默认审批；
#           tags 决定该工具集在哪种运行模式(CLI/server)被加载。
class Toolset(BaseModel):
    """一组相关工具的集合，携带共享的配置与前置条件。"""

    name: str
    description: str
    tools: List[Tool] = Field(default_factory=list)
    prerequisites: List[Prerequisite] = Field(default_factory=list)
    config: Optional[Any] = None
    approval_required_tools: List[str] = Field(default_factory=list)
    tags: List[ToolsetTag] = Field(default_factory=list)
    type: Optional[ToolsetType] = None
    status: ToolsetStatusEnum = ToolsetStatusEnum.ENABLED

    class Config:
        """Toolset 的 Pydantic 配置。"""

        arbitrary_types_allowed = True

    def check_prerequisites(self) -> bool:
        """检查所有前置条件是否满足。"""
        for prereq in self.prerequisites:
            if not prereq.check(self.config):
                return False
        return True

    def mark_failed(self) -> None:
        """将此工具集标记为失败。"""
        self.status = ToolsetStatusEnum.FAILED