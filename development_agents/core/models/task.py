from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID, uuid4


class _ProtocolValue(StrEnum):
    def __repr__(self) -> str:
        return repr(self.value)


class AgentRole(_ProtocolValue):
    ARCHITECT = "architect"
    BACKEND = "backend"
    FRONTEND = "frontend"
    REVIEWER = "reviewer"
    QA = "qa"
    DEVOPS = "devops"


class TaskStatus(_ProtocolValue):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    NEEDS_INPUT = "needs_input"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Task:
    project_id: UUID
    title: str
    description: str
    agent: AgentRole
    id: UUID = field(default_factory=uuid4)
    status: TaskStatus = TaskStatus.PENDING
