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
    ) -> Project:
        return self._orchestration.create_project(
            name=name,
            description=description,
            backend_stack=backend_stack,
            backend_architecture=backend_architecture,
            frontend_stack=frontend_stack,
            frontend_architecture=frontend_architecture,
            infrastructure=infrastructure,
            technical_constraints=technical_constraints,
        )

    def create_task(
        self,
        project: Project,
        title: str,
        description: str,
        agent: AgentRole,
    ) -> Task:
        return self._orchestration.create_task(
            project=project,
            title=title,
            description=description,
            agent=agent,
        )
