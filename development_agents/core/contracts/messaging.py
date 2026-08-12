from abc import ABC, abstractmethod

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
    def consume(self, agent: AgentRole, handler: TaskHandler) -> None:
        """Consume tasks for one agent and delegate them to its handler."""
