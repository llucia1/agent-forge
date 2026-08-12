from uuid import UUID

from core.contracts.context import AgentContext
from core.contracts.engines import (
    AgentEngine,
    EngineConfigurationError,
    EngineResult,
)
from core.models.project import Project
from core.models.task import Task


class EngineResolver(AgentEngine):
    def __init__(
        self,
        engines: dict[str, AgentEngine],
        default_engine: str,
        engines_by_agent: dict[str, str] | None = None,
        engines_by_project: dict[UUID, str] | None = None,
    ):
        self.engines = dict(engines)
        self.default_engine = default_engine
        self.engines_by_agent = dict(engines_by_agent or {})
        self.engines_by_project = dict(engines_by_project or {})

    def resolve(self, task: Task, project: Project) -> AgentEngine:
        engine_name = self.engines_by_project.get(project.id)

        if engine_name is None:
            engine_name = self.engines_by_agent.get(str(task.agent))

        if engine_name is None:
            engine_name = self.default_engine

        try:
            return self.engines[engine_name]
        except KeyError as error:
            raise EngineConfigurationError(
                f"Engine is not registered: {engine_name}"
            ) from error

    def run(
        self,
        task: Task,
        project: Project,
        context: AgentContext | None = None,
    ) -> EngineResult:
        engine = self.resolve(task, project)
        return engine.run(task, project, context)
