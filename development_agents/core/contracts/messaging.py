from abc import ABC, abstractmethod
from collections.abc import Mapping

from core.models.task import AgentRole, Task


class TaskPublisher(ABC):
    @abstractmethod
    def publish(self, task: Task) -> None:
        """Publish a task for asynchronous handling."""


class TaskHandler(ABC):
    @abstractmethod
    def handle(self, task: Task) -> None:
        """Handle a delivered task."""


class TaskConsumer(ABC):
    @abstractmethod
    def consume(self, handlers: Mapping[AgentRole, TaskHandler]) -> None:
        """Consume each configured agent queue with its assigned handler."""
