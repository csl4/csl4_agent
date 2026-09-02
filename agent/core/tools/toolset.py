"""工具集（Toolset）与前置条件：一组相关工具 + 共享配置/审批/标签。"""

from enum import Enum
from typing import Any, Callable, List, Optional

from pydantic import BaseModel, Field

from agent.core.tools.base import Tool


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


__all__ = [
    "CallablePrerequisite",
    "Prerequisite",
    "Toolset",
    "ToolsetStatusEnum",
    "ToolsetTag",
    "ToolsetType",
]
