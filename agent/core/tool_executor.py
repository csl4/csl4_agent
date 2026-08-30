"""工具注册、查找与执行引擎。"""


# ======================= 中文导览 =======================
# 本文件是「工具注册中心 + 分发器」（行为对象）：
#   ToolExecutor —— 输入(工具名 + params + ToolInvokeContext) → 输出(ToolCallResult)。
# 干了四件事：
#   ① 注册：把各 Toolset 里的 Tool 建成 name→Tool 索引（并反向建 tool→toolset 索引）
#   ② 懒加载：首次用到某工具才 check 其 toolset 前置条件，不达标 mark_failed() 并重建索引
#   ③ tag 过滤：按运行模式裁剪工具集（CLI 只装 CORE+CLI，server 装 CORE+CLUSTER）
#   ④ 名字冲突解决：MCP 工具自动加 {toolset}__{tool} 前缀
# =========================================================


import logging
import time
from typing import Any, Dict, List, Optional

from agent.core.models import (
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolCallResult,
    ToolParameter,
)
from agent.core.tools import Tool, Toolset, ToolsetStatusEnum, ToolsetTag, ToolsetType

logger = logging.getLogger(__name__)


# ---- 行为对象：工具注册中心 + 分发器 ----
# 输入：工具名 + 参数 + ToolInvokeContext；输出：ToolCallResult（外包一层，附耗时/tool_call_id）。
# 设计要点：除了执行，还负责 ①建索引 ②懒初始化 ③tag过滤 ④MCP 名字前缀去冲突。
#          它【不】关心工具内部逻辑，只做「按名找到 Tool → 用你的 context 调 .invoke() → 包结果」。
class ToolExecutor:
    """管理工具的注册、查找、懒初始化与执行。

    支持：
    - 多种工具集类型（YAML、PYTHON、HTTP、MCP）
    - 工具集懒初始化（首次使用时才检查前置条件）
    - 名字冲突解决（MCP 工具自动加 {toolset}__{tool} 前缀）
    - 基于标签的工具集过滤（toolset_tag_filter）
    """

    def __init__(
        self,
        toolsets: Optional[List[Toolset]] = None,
        toolset_tag_filter: Optional[List[ToolsetTag]] = None,
    ):
        """初始化 ToolExecutor。

        参数:
            toolsets: 要注册的初始工具对象集列表。
            toolset_tag_filter: 若提供，则只加载至少含一个匹配标签的工具集。
                None 表示不过滤（加载所有已启用的工具集）。
                示例：CLI 模式用 [ToolsetTag.CORE, ToolsetTag.CLI]，
                server 模式用 [ToolsetTag.CORE, ToolsetTag.CLUSTER]。
        """
        self.toolsets: List[Toolset] = toolsets or []
        self.toolset_tag_filter: Optional[List[ToolsetTag]] = toolset_tag_filter
        self.enabled_toolsets: List[Toolset] = []
        self.tools_by_name: Dict[str, Tool] = {} # key是工具名，value 对应的工具对象
        self._tool_to_toolset: Dict[str, Toolset] = {}
        self._initialized_toolsets: set = set()
        self._build_index()

    def _build_index(self) -> None:
        """根据所有已启用的工具集重建工具名索引。

        按状态（ENABLED）过滤工具集；若设置了 toolset_tag_filter，则再按标签过滤。
        工具集必须至少含一个匹配标签才能通过过滤。
        启用过滤时，不带标签的工具集会被排除。
        """
        self.tools_by_name.clear()
        self._tool_to_toolset.clear()

        # Filter by status first
        enabled = [ts for ts in self.toolsets if ts.status == ToolsetStatusEnum.ENABLED]

        # Apply tag filter if configured
        if self.toolset_tag_filter is not None:
            filtered: List[Toolset] = []
            for ts in enabled:
                if not ts.tags:
                    logger.debug(
                        f"Toolset '{ts.name}' has no tags, skipping "
                        f"(tag filter active: {[t.value for t in self.toolset_tag_filter]})"
                    )
                    continue
                # Check if any toolset tag matches the filter
                if any(tag in self.toolset_tag_filter for tag in ts.tags):
                    filtered.append(ts)
                else:
                    logger.debug(
                        f"Toolset '{ts.name}' tags {[t.value for t in ts.tags]} "
                        f"don't match filter {[t.value for t in self.toolset_tag_filter]}"
                    )
            self.enabled_toolsets = filtered
        else:
            self.enabled_toolsets = enabled

        for toolset in self.enabled_toolsets:
            for tool in toolset.tools:
                name = self._resolve_tool_name(tool.name, toolset)
                if name in self.tools_by_name:
                    logger.warning(
                        f"Tool name conflict: '{name}' already registered. "
                        f"Skipping duplicate from toolset '{toolset.name}'."
                    )
                    continue
                self.tools_by_name[name] = tool
                self._tool_to_toolset[name] = toolset

        logger.info(
            f"Built tool index: {len(self.tools_by_name)} tools from "
            f"{len(self.enabled_toolsets)} toolsets"
            + (
                f" (tag filter: {[t.value for t in self.toolset_tag_filter]})"
                if self.toolset_tag_filter
                else ""
            )
        )

    def _resolve_tool_name(self, tool_name: str, toolset: Toolset) -> str:
        """解析工具名，为 MCP 工具加前缀以避免冲突。"""
        if toolset.type == ToolsetType.MCP:
            return f"{toolset.name}__{tool_name}"
        return tool_name

    def get_tool_by_name(self, name: str) -> Optional[Tool]:
        """按名称查找工具。未找到时返回 None。"""
        return self.tools_by_name.get(name)

    def get_toolset_name(self, tool_name: str) -> Optional[str]:
        """获取拥有指定工具的工具集名称。"""
        toolset = self.get_toolset_for(tool_name)
        return toolset.name if toolset else None

    def get_toolset_for(self, tool_name: str) -> Optional[Toolset]:
        """获取拥有指定工具的 Toolset 实例（公开访问器）。"""
        return self._tool_to_toolset.get(tool_name)

    # 懒初始化：首次用到某工具时检查其 toolset 前置条件，失败则 mark_failed + 重建索引。
    def ensure_toolset_initialized(self, tool_name: str) -> Optional[str]:
        """为指定工具名懒初始化其所属的工具集。

        初始化失败时返回错误信息，成功时返回 None。
        """
        toolset = self._tool_to_toolset.get(tool_name)
        if not toolset:
            return f"Tool '{tool_name}' not found in any toolset."

        if toolset.name in self._initialized_toolsets:
            return None

        if not toolset.check_prerequisites():
            toolset.mark_failed()
            self._build_index()
            return f"Toolset '{toolset.name}' prerequisites check failed."

        self._initialized_toolsets.add(toolset.name)
        return None

    # 核心入口：按名找到 Tool，调 .invoke()，再用计时包成 ToolCallResult 返回。
    def execute_tool(
        self,
        tool_name: str,
        params: Dict[str, Any],
        context: Any,
        tool_call_id: str = "",
    ) -> ToolCallResult:
        """按名称执行工具并返回 ToolCallResult。

        参数:
            tool_name: 要执行的工具名称。
            params: 传给工具的参数。
            context: ToolInvokeContext（或兼容对象），包含 user_approved 等字段。
            tool_call_id: LLM 工具调用的 ID，用于关联。

        返回:
            包装了 StructuredToolResult 的 ToolCallResult。
        """
        start = time.time()

        tool = self.get_tool_by_name(tool_name)
        if tool is None:
            return ToolCallResult(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                result=StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"Tool '{tool_name}' not found.",
                ),
            )

        result = tool.invoke(params, context)
        elapsed = (time.time() - start) * 1000

        return ToolCallResult(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            result=result,
            execution_time_ms=elapsed,
        )

    def get_tools_as_openai(self) -> List[Dict[str, Any]]:
        """以 OpenAI 兼容格式获取所有已注册的工具。"""
        return [tool.to_openai_tool() for tool in self.tools_by_name.values()]

    def add_toolset(self, toolset: Toolset) -> None:
        """注册新的工具集并重建索引。"""
        self.toolsets.append(toolset)
        self._build_index()