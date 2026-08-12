from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4


@dataclass
class Project:
    name: str
    description: str
    backend_stack: dict[str, Any] = field(default_factory=dict)
    backend_architecture: dict[str, Any] = field(default_factory=dict)
    frontend_stack: dict[str, Any] = field(default_factory=dict)
    frontend_architecture: dict[str, Any] = field(default_factory=dict)
    infrastructure: dict[str, Any] = field(default_factory=dict)
    technical_constraints: list[str] = field(default_factory=list)
    id: UUID = field(default_factory=uuid4)
    status: str = "draft"
