from typing import Any

from core.contracts.orchestration import OrchestrationUseCases
from core.models.project import Project
from core.models.task import AgentRole, Task


class OrchestratorAgent:
    def __init__(self, orchestration: OrchestrationUseCases):
        self._orchestration = orchestration

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
        arguments = {
            "name": name,
            "description": description,
            "backend_stack": backend_stack,
            "backend_architecture": backend_architecture,
            "frontend_stack": frontend_stack,
            "frontend_architecture": frontend_architecture,
            "infrastructure": infrastructure,
            "technical_constraints": technical_constraints,
        }
        if project_markdown is not None:
            arguments["project_markdown"] = project_markdown
        return self._orchestration.create_project(
            **arguments,
        )

    def register_specification(
        self,
        project: Project,
        spec_path: str,
        markdown: str,
    ) -> str:
        return self._orchestration.register_specification(
            project,
            spec_path,
            markdown,
        )

    def create_task(
        self,
        project: Project,
        title: str,
        description: str,
        agent: AgentRole,
        spec_path: str | None = None,
    ) -> Task:
        arguments = {
            "project": project,
            "title": title,
            "description": description,
            "agent": agent,
        }
        if spec_path is not None:
            arguments["spec_path"] = spec_path
        return self._orchestration.create_task(**arguments)
