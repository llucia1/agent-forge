from core.contracts.context import AgentContext, ContextProvider
from core.models.project import Project
from core.models.task import Task


class DefaultContextProvider(ContextProvider):
    def build(self, task: Task, project: Project) -> AgentContext:
        return {
            "project_memory": {},
            "previous_decisions": [],
            "feedback": [],
            "rag_context": None,
        }
