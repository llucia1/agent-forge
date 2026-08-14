from abc import ABC, abstractmethod
from typing import Any, TypedDict

from core.models.project import Project
from core.models.task import Task


class AgentContext(TypedDict, total=False):
    project_memory: dict[str, Any]
    previous_decisions: list[str]
    feedback: list[str]
    rag_context: Any | None
    output_contract: dict[str, Any]
    backend_implementation: dict[str, Any]


class ContextProvider(ABC):
    @abstractmethod
    def build(self, task: Task, project: Project) -> AgentContext:
        """Build context owned and managed by AgentForge."""
