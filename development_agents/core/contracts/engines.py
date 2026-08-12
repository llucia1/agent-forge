from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from core.contracts.context import AgentContext
from core.models.project import Project
from core.models.task import Task


class EngineError(RuntimeError):
    """Base class for provider-neutral engine errors."""


class EngineConfigurationError(LookupError):
    """Raised when an engine lacks valid runtime configuration."""


class EngineHTTPError(EngineError):
    """Raised when an engine gateway returns an HTTP error."""


class EngineTimeoutError(EngineError):
    """Raised when an engine gateway request times out."""


class EngineResponseError(EngineError):
    """Raised when an engine gateway response is invalid."""


@dataclass(frozen=True)
class EngineResult:
    output: str
    metadata: dict[str, Any]
    provider: str


class AgentEngine(ABC):
    @abstractmethod
    def run(
        self,
        task: Task,
        project: Project,
        context: AgentContext | None = None,
    ) -> EngineResult:
        """Process a task using context owned by AgentForge."""
