from typing import Any

from core.contracts.messaging import TaskPublisher
from core.contracts.orchestration import OrchestrationUseCases
from core.contracts.repositories import ProjectCreator, TaskCreator
from core.contracts.workspaces import ProjectWorkspaceInitializer
from core.models.project import Project
from core.models.task import AgentRole, Task


class Orchestrator(OrchestrationUseCases):
    def __init__(
        self,
        project_creator: ProjectCreator,
        task_creator: TaskCreator,
        task_publisher: TaskPublisher,
        workspace_initializer: ProjectWorkspaceInitializer,
    ):
        self.project_creator = project_creator
        self.task_creator = task_creator
        self.task_publisher = task_publisher
        self.workspace_initializer = workspace_initializer

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

        self.project_creator.create(project)
        self.workspace_initializer.initialize(project.id)

        return project

    def create_task(
        self,
        project: Project,
        title: str,
        description: str,
        agent: AgentRole,
    ) -> Task:
        task = Task(
            project_id=project.id,
            title=title,
            description=description,
            agent=agent,
        )

        self.task_creator.create(task)
        self.task_publisher.publish(task)

        return task
