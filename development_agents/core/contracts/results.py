from abc import ABC, abstractmethod

from core.models.task_result import TaskExecutionResult


class TaskResultWriter(ABC):
    @abstractmethod
    def write(self, result: TaskExecutionResult) -> None:
        """Persist one agent task execution result."""
