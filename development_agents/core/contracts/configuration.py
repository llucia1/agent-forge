from dataclasses import dataclass
from typing import Mapping
from uuid import UUID

from core.models.task import AgentRole
from core.models.qa import QACheck, QAExecutionPolicy


@dataclass(frozen=True)
class DatabaseSettings:
    host: str | None
    port: str | None
    database: str | None
    user: str | None
    password: str | None


@dataclass(frozen=True)
class RabbitMQSettings:
    host: str | None
    port: int
    user: str | None
    password: str | None
    task_queues: Mapping[AgentRole, str]


@dataclass(frozen=True)
class EngineSettings:
    default_engine: str
    default_model: str
    models_by_agent: dict[str, str]
    models_by_project: dict[UUID, str]


@dataclass(frozen=True)
class LiteLLMSettings:
    base_url: str
    api_key: str
    timeout_seconds: float = 180.0


@dataclass(frozen=True)
class ApplicationSettings:
    database: DatabaseSettings
    rabbitmq: RabbitMQSettings
    engine: EngineSettings
    litellm: LiteLLMSettings
    qa_execution: QAExecutionPolicy
    qa_checks: tuple[QACheck, ...]
