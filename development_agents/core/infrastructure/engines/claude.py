from core.contracts.context import AgentContext
from core.contracts.engines import AgentEngine, EngineResult
from core.models.project import Project
from core.models.task import Task


class ClaudeEngine(AgentEngine):
    def run(
        self,
        task: Task,
        project: Project,
        context: AgentContext | None = None,
    ) -> EngineResult:
        return EngineResult(
            output="ClaudeEngine stub: provider integration is not implemented.",
            metadata={"stub": True},
            provider="claude",
        )
