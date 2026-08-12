from abc import ABC, abstractmethod

from core.models.project import Project
from core.models.task import Task


class ModelConfigurationError(LookupError):
    """Raised when model selection does not resolve a valid alias."""


class ModelSelector(ABC):
    @abstractmethod
    def resolve(self, task: Task, project: Project) -> str:
        """Resolve the configured model alias for a task and project."""
