from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from core.models.task import AgentRole, TaskStatus


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class TaskExecutionResult:
    task_id: UUID
    project_id: UUID
    agent: str
    provider: str
    model_alias: str | None
    output: str
    metadata: dict[str, Any]
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True)
class AgentTaskExecution:
    task_id: UUID
    project_id: UUID
    agent: AgentRole
    task_status: TaskStatus
    result: TaskExecutionResult | None
