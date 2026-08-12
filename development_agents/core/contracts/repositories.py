from abc import ABC, abstractmethod
from uuid import UUID

from core.models.project import Project
from core.models.task import Task, TaskStatus


class ProjectCreator(ABC):
    @abstractmethod
    def create(self, project: Project) -> None:
        """Persist a new project."""


class ProjectReader(ABC):
    @abstractmethod
    def find_by_id(self, project_id: UUID) -> Project | None:
        """Return a project by id when it exists."""


class TaskCreator(ABC):
    @abstractmethod
    def create(self, task: Task) -> None:
        """Persist a new task."""


class TaskStatusWriter(ABC):
    @abstractmethod
    def update_status(self, task_id: UUID, status: TaskStatus) -> None:
        """Persist a task status transition."""
