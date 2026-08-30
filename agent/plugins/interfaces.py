"""数据来源(source)与结果出口(destination)的插件接口。"""


# ======================= 中文导览 =======================
# 告警/问题来源与结果出口的插件抽象基类（本框架预留的扩展点，目前无具体实现）。
#   SourcePlugin      → 数据来源（如 AlertManager/Jira/PagerDuty），fetch_* 拉取问题。
#   DestinationPlugin → 结果出口（如 Slack），send_result 推送分析结果。
# 设计要点：抽象了「从哪里来、回答后送到哪」，具体来源/出口可插拔替换。
# =========================================================

from abc import ABC, abstractmethod
from typing import Any, Iterable, List, Optional


class SourcePlugin(ABC):
    """告警/问题来源(AlertManager、Jira、PagerDuty 等)的抽象基类。"""

    name: str = ""

    @abstractmethod
    def fetch_issues(self) -> List[Any]:
        """从来源拉取全部问题。"""
        ...

    @abstractmethod
    def fetch_issue(self, issue_id: str) -> Any:
        """按 ID 拉取单个问题。"""
        ...

    def stream_issues(self) -> Iterable[Any]:
        """以流式方式获取到达的问题。可选，在子类中覆写。"""
        return iter([])

    def write_back_result(self, issue_id: str, result: Any) -> None:
        """将分析结果写回问题。可选，在子类中覆写。"""
        pass


class DestinationPlugin(ABC):
    """结果出口(Slack、PagerDuty 等)的抽象基类。"""

    name: str = ""

    @abstractmethod
    def send_result(self, result: Any, context: Optional[Any] = None) -> None:
        """将分析结果发送到出口。"""
        ...