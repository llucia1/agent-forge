import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID


class ConfigurationError(ValueError):
    """Raised when AgentForge configuration is invalid."""


@dataclass(frozen=True)
class EngineSettings:
    default_engine: str
    default_model: str
    models_by_agent: dict[str, str]
    models_by_project: dict[UUID, str]

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "EngineSettings":
        source = os.environ if environ is None else environ
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

        return cls(
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
