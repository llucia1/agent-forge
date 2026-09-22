from abc import ABC, abstractmethod
from uuid import UUID

from core.models.task import AgentRole
from core.models.task_result import AgentTaskExecution, TaskExecutionResult


class TaskResultWriter(ABC):
    @abstractmethod
    def write(self, result: TaskExecutionResult) -> None:
        """Persist one agent task execution result."""


class LatestTaskExecutionReader(ABC):
    @abstractmethod
    def find_latest(
        self,
        project_id: UUID,
        agent: AgentRole,
    ) -> AgentTaskExecution | None:
        """Return the newest task and optional result for one agent."""
