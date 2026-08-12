from abc import ABC, abstractmethod
from typing import Any

from core.models.project import Project
from core.models.task import AgentRole, Task


class OrchestrationUseCases(ABC):
    @abstractmethod
    def create_project(
        self,
        name: str,
        description: str,
        backend_stack: dict[str, Any] | None = None,
        backend_architecture: dict[str, Any] | None = None,
        frontend_stack: dict[str, Any] | None = None,
        frontend_architecture: dict[str, Any] | None = None,
        infrastructure: dict[str, Any] | None = None,
        technical_constraints: list[str] | None = None,
    ) -> Project:
        """Create and persist a project."""

    @abstractmethod
    def create_task(
        self,
        project: Project,
        title: str,
        description: str,
        agent: AgentRole,
    ) -> Task:
        """Create, persist and publish a task."""
