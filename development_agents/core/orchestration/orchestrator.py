from typing import Any
from uuid import UUID

from core.contracts.messaging import TaskPublisher
from core.contracts.orchestration import OrchestrationUseCases
from core.contracts.repositories import (
    ProjectCreator,
    ProjectDeleter,
    ProjectReader,
    TaskCreator,
    TaskDeleter,
)
from core.contracts.workspaces import (
    ProjectWorkspaceInitializer,
    ProjectWorkspaceRemover,
    SpecificationReader,
    SpecificationWriter,
)
from core.models.project import Project
from core.models.specification import (
    FeatureSpecification,
    ProjectSpecification,
    validate_specification_path,
)
from core.models.task import AgentRole, Task


class Orchestrator(OrchestrationUseCases):
    def __init__(
        self,
        project_creator: ProjectCreator,
        task_creator: TaskCreator,
        task_publisher: TaskPublisher,
        workspace_initializer: ProjectWorkspaceInitializer,
        project_reader: ProjectReader,
        project_deleter: ProjectDeleter,
        task_deleter: TaskDeleter,
        workspace_remover: ProjectWorkspaceRemover,
        specification_reader: SpecificationReader | None = None,
        specification_writer: SpecificationWriter | None = None,
    ):
        self.project_creator = project_creator
        self.task_creator = task_creator
        self.task_publisher = task_publisher
        self.workspace_initializer = workspace_initializer
        self.project_reader = project_reader
        self.project_deleter = project_deleter
        self.task_deleter = task_deleter
        self.workspace_remover = workspace_remover
        self.specification_reader = specification_reader
        self.specification_writer = specification_writer

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
        project_markdown: str | None = None,
    ) -> Project:
        self._validate_text(name, "Project name")
        self._validate_text(description, "Project description")
        technical_mappings = {
            "backend_stack": backend_stack,
            "backend_architecture": backend_architecture,
            "frontend_stack": frontend_stack,
            "frontend_architecture": frontend_architecture,
            "infrastructure": infrastructure,
        }
        invalid_mappings = [
            field_name
            for field_name, value in technical_mappings.items()
            if value is not None and not isinstance(value, dict)
        ]
        if invalid_mappings:
            raise ValueError(
                "Project technical fields must be objects: "
                + ", ".join(invalid_mappings)
            )
        if technical_constraints is not None and (
            not isinstance(technical_constraints, list)
            or not all(
                isinstance(constraint, str) and constraint.strip()
                for constraint in technical_constraints
            )
        ):
            raise ValueError(
                "Project technical_constraints must be non-empty strings"
            )
        project = Project(
            name=name,
            description=description,
            backend_stack=(
                dict(backend_stack) if backend_stack is not None else {}
            ),
            backend_architecture=(
                dict(backend_architecture)
                if backend_architecture is not None
                else {}
            ),
            frontend_stack=(
                dict(frontend_stack) if frontend_stack is not None else {}
            ),
            frontend_architecture=(
                dict(frontend_architecture)
                if frontend_architecture is not None
                else {}
            ),
            infrastructure=(
                dict(infrastructure) if infrastructure is not None else {}
            ),
            technical_constraints=(
                list(technical_constraints)
                if technical_constraints is not None
                else []
            ),
        )

        self.project_creator.create(project)
        try:
            self.workspace_initializer.initialize(project.id)
            project_specification = ProjectSpecification.from_markdown(
                project_markdown
                if project_markdown is not None
                else self._default_project_markdown(project)
            )
            if self.specification_writer is not None:
                self.specification_writer.write_project_specification(
                    project.id,
                    project_specification,
                )
        except Exception:
            self._compensate_project(project.id)
            raise

        return project

    def register_specification(
        self,
        project: Project,
        spec_path: str,
        markdown: str,
    ) -> str:
        if not isinstance(project, Project):
            raise ValueError("Specification project must be a Project")
        if self.project_reader.find_by_id(project.id) is None:
            raise ValueError(
                f"Cannot register a spec for an unknown project: {project.id}"
            )
        if self.specification_writer is None:
            raise ValueError("Specification writer is not configured")
        validate_specification_path(spec_path)
        specification = FeatureSpecification.from_markdown(markdown)
        self.specification_writer.write_specification(
            project.id,
            spec_path,
            specification,
        )
        return spec_path

    def create_task(
        self,
        project: Project,
        title: str,
        description: str,
        agent: AgentRole,
        spec_path: str | None = None,
    ) -> Task:
        if not isinstance(project, Project):
            raise ValueError("Task project must be a Project")
        self._validate_text(title, "Task title")
        self._validate_text(description, "Task description")
        if not isinstance(agent, AgentRole):
            raise ValueError("Task agent must be a supported AgentRole")
        if self.project_reader.find_by_id(project.id) is None:
            raise ValueError(
                f"Cannot create a task for an unknown project: {project.id}"
            )
        specification_roles = {
            AgentRole.BACKEND,
            AgentRole.FRONTEND,
            AgentRole.REVIEWER,
            AgentRole.QA,
        }
        if agent in specification_roles and spec_path is None:
            raise ValueError(f"Task for {agent} requires a specification")
        if spec_path is not None:
            validate_specification_path(spec_path)
            if self.specification_reader is None:
                raise ValueError("Specification reader is not configured")
            if self.specification_reader.read_specification(
                project.id,
                spec_path,
            ) is None:
                raise ValueError(
                    f"Task specification does not exist: {spec_path}"
                )
        task = Task(
            project_id=project.id,
            title=title,
            description=description,
            agent=agent,
            spec_path=spec_path,
        )

        self.task_creator.create(task)
        try:
            self.task_publisher.publish(task)
        except Exception:
            try:
                self.task_deleter.delete(task.id)
            except Exception as cleanup_error:
                raise RuntimeError(
                    f"Could not compensate failed task publication: {task.id}"
                ) from cleanup_error
            raise

        return task

    def _compensate_project(self, project_id: UUID) -> None:
        cleanup_error = None
        try:
            self.workspace_remover.remove(project_id)
        except Exception as error:
            cleanup_error = error
        try:
            self.project_deleter.delete(project_id)
        except Exception as error:
            cleanup_error = cleanup_error or error
        if cleanup_error is not None:
            raise RuntimeError(
                f"Could not compensate failed project creation: {project_id}"
            ) from cleanup_error

    @staticmethod
    def _validate_text(value: str, field_name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be non-empty text")

    @staticmethod
    def _default_project_markdown(project: Project) -> str:
        rules = project.technical_constraints or [
            "Implement only explicitly registered feature specifications."
        ]
        return (
            f"# {project.name}\n\n"
            f"## Objective\n{project.description}\n\n"
            "## Scope\nThe functional scope is defined by specs/*.md.\n\n"
            "## Global Rules\n"
            + "\n".join(f"- {rule}" for rule in rules)
            + "\n"
        )
