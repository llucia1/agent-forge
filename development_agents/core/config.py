import json
import os
from collections.abc import Mapping
from uuid import UUID

from core.contracts.configuration import (
    ApplicationSettings,
    DatabaseSettings,
    EngineSettings,
    LiteLLMSettings,
    RabbitMQSettings,
)
from core.models.task import AgentRole


DEFAULT_TASK_QUEUES = {
    AgentRole.ARCHITECT: "tasks.architect",
    AgentRole.BACKEND: "tasks.backend",
    AgentRole.FRONTEND: "tasks.frontend",
    AgentRole.REVIEWER: "tasks.reviewer",
    AgentRole.QA: "tasks.qa",
    AgentRole.DEVOPS: "tasks.devops",
}


class ConfigurationError(ValueError):
    """Raised when AgentForge configuration is invalid."""


def load_settings(
    environ: Mapping[str, str] | None = None,
) -> ApplicationSettings:
    source = os.environ if environ is None else environ
    return ApplicationSettings(
        database=DatabaseSettings(
            host=source.get("POSTGRES_HOST"),
            port=source.get("POSTGRES_PORT"),
            database=source.get("POSTGRES_DB"),
            user=source.get("POSTGRES_USER"),
            password=source.get("POSTGRES_PASSWORD"),
        ),
        rabbitmq=RabbitMQSettings(
            host=source.get("RABBITMQ_HOST"),
            port=int(source.get("RABBITMQ_PORT", "5672")),
            user=source.get("RABBITMQ_USER"),
            password=source.get("RABBITMQ_PASSWORD"),
            task_queues=dict(DEFAULT_TASK_QUEUES),
        ),
        engine=_load_engine_settings(source),
        litellm=LiteLLMSettings(
            base_url=source.get("LITELLM_BASE_URL", ""),
            api_key=source.get("LITELLM_API_KEY", ""),
            timeout_seconds=float(
                source.get("LITELLM_TIMEOUT_SECONDS", "600")
            ),
        ),
    )


def _load_engine_settings(source: Mapping[str, str]) -> EngineSettings:
    models_by_agent = _parse_alias_mapping(
        source.get("AGENTFORGE_MODELS_BY_AGENT", "{}"),
        "AGENTFORGE_MODELS_BY_AGENT",
    )
    project_aliases = _parse_alias_mapping(
        source.get("AGENTFORGE_MODELS_BY_PROJECT", "{}"),
        "AGENTFORGE_MODELS_BY_PROJECT",
    )

    models_by_project = {}
    for project_id, alias in project_aliases.items():
        try:
            models_by_project[UUID(project_id)] = alias
        except ValueError as error:
            raise ConfigurationError(
                "AGENTFORGE_MODELS_BY_PROJECT contains an invalid "
                f"project UUID: {project_id}"
            ) from error

    return EngineSettings(
        default_engine=_read_setting(
            source,
            "AGENTFORGE_DEFAULT_ENGINE",
        ),
        default_model=_read_setting(
            source,
            "AGENTFORGE_DEFAULT_MODEL",
        ),
        models_by_agent=models_by_agent,
        models_by_project=models_by_project,
    )


def _parse_alias_mapping(raw_value: str, setting_name: str) -> dict[str, str]:
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError as error:
        raise ConfigurationError(
            f"{setting_name} must be a valid JSON object"
        ) from error

    if not isinstance(value, dict):
        raise ConfigurationError(f"{setting_name} must be a JSON object")

    aliases = {}
    for key, alias in value.items():
        if not key.strip() or not isinstance(alias, str) or not alias.strip():
            raise ConfigurationError(
                f"{setting_name} keys and values must be non-empty strings"
            )
        aliases[key.strip()] = alias.strip()

    return aliases


def _read_setting(
    environ: Mapping[str, str],
    setting_name: str,
) -> str:
    value = environ.get(setting_name, "").strip()
    if not value:
        raise ConfigurationError(f"{setting_name} must not be empty")
    return value
