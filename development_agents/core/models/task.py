from dataclasses import dataclass, field
from uuid import UUID, uuid4


@dataclass
class Task:
    project_id: UUID
    title: str
    description: str
    agent: str
    id: UUID = field(default_factory=uuid4)
    status: str = "pending"