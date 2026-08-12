from abc import ABC, abstractmethod
from typing import Any

from core.models.project import Project
from core.models.task import Task


class ContextProvider(ABC):
    @abstractmethod
    def build(self, task: Task, project: Project) -> dict[str, Any]:
        """Build context owned and managed by AgentForge."""


class DefaultContextProvider(ContextProvider):
    def build(self, task: Task, project: Project) -> dict[str, Any]:
        return {
            "project_memory": {},
            "previous_decisions": [],
            "feedback": [],
            "rag_context": None,
        }
