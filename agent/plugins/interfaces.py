"""Plugin interfaces for sources and destinations."""

from abc import ABC, abstractmethod
from typing import Any, Iterable, List, Optional


class SourcePlugin(ABC):
    """Abstract base for alert/issue sources (AlertManager, Jira, PagerDuty, etc.)."""

    name: str = ""

    @abstractmethod
    def fetch_issues(self) -> List[Any]:
        """Fetch all issues from the source."""
        ...

    @abstractmethod
    def fetch_issue(self, issue_id: str) -> Any:
        """Fetch a single issue by ID."""
        ...

    def stream_issues(self) -> Iterable[Any]:
        """Stream issues as they arrive. Optional, override in subclasses."""
        return iter([])

    def write_back_result(self, issue_id: str, result: Any) -> None:
        """Write analysis results back to the issue. Optional, override in subclasses."""
        pass


class DestinationPlugin(ABC):
    """Abstract base for result destinations (Slack, PagerDuty, etc.)."""

    name: str = ""

    @abstractmethod
    def send_result(self, result: Any, context: Optional[Any] = None) -> None:
        """Send analysis results to the destination."""
        ...