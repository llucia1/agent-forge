from typing import Any

from core.models.project import Project
from core.models.task import Task
from core.infrastructure.rabbitmq import TaskPublisher
from core.infrastructure.repositories.project_repository import ProjectRepository
from core.infrastructure.repositories.task_repository import TaskRepository


class Orchestrator:
    def __init__(
        self,
        project_repository: ProjectRepository | None = None,
        task_repository: TaskRepository | None = None,
        task_publisher: TaskPublisher | None = None,
    ):
        self.project_repository = (
            project_repository
            if project_repository is not None
            else ProjectRepository()
        )
        self.task_repository = (
            task_repository
            if task_repository is not None
            else TaskRepository()
        )
        self.task_publisher = (
            task_publisher
            if task_publisher is not None
            else TaskPublisher()
        )

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
        project = Project(
            name=name,
            description=description,
            backend_stack=(
                backend_stack if backend_stack is not None else {}
            ),
            backend_architecture=(
                backend_architecture
                if backend_architecture is not None
                else {}
            ),
            frontend_stack=(
                frontend_stack if frontend_stack is not None else {}
            ),
            frontend_architecture=(
                frontend_architecture
                if frontend_architecture is not None
                else {}
            ),
            infrastructure=(
                infrastructure if infrastructure is not None else {}
            ),
            technical_constraints=(
                technical_constraints
                if technical_constraints is not None
                else []
            ),
        )

        self.project_repository.create(project)

        return project

    def create_task(
        self,
        project: Project,
        title: str,
        description: str,
        agent: str,
    ) -> Task:
        task = Task(
            project_id=project.id,
            title=title,
            description=description,
            agent=agent,
        )

        self.task_repository.create(task)
        self.task_publisher.publish(task)

        return task
