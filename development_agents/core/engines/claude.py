from typing import Any

from core.engines.base import AgentEngine, EngineResult
from core.models.project import Project
from core.models.task import Task


class ClaudeEngine(AgentEngine):
    def run(
        self,
        task: Task,
        project: Project,
        context: dict[str, Any] | None = None,
    ) -> EngineResult:
        return EngineResult(
            output="ClaudeEngine stub: provider integration is not implemented.",
            metadata={"stub": True},
            provider="claude",
        )
