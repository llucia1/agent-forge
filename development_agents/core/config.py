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
from core.models.qa import (
    ExecutableRule,
    QACheck,
    QACommand,
    QAExecutionPolicy,
    QAValidationError,
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
    qa_execution = _load_qa_execution_policy(source)
    qa_checks = _load_qa_checks(source)
    _validate_qa_configuration(qa_execution, qa_checks)
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
        qa_execution=qa_execution,
        qa_checks=qa_checks,
    )


def _load_qa_execution_policy(
    source: Mapping[str, str],
) -> QAExecutionPolicy:
    setting_name = "AGENTFORGE_QA_EXECUTABLE_POLICY"
    raw_value = source.get(setting_name, "{}")
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError as error:
        raise ConfigurationError(
            f"{setting_name} must be a valid JSON object"
        ) from error
    if not isinstance(payload, dict):
        raise ConfigurationError(f"{setting_name} must be a JSON object")

    rules = []
    expected_fields = {
        "allowed_arguments",
        "allowed_working_directories",
        "max_timeout_seconds",
    }
    for executable, raw_rule in payload.items():
        if (
            not isinstance(executable, str)
            or not executable.strip()
            or "/" in executable
            or "\\" in executable
        ):
            raise ConfigurationError(
                f"{setting_name} executable names must be bare names"
            )
        if not isinstance(raw_rule, dict) or set(raw_rule) != expected_fields:
            raise ConfigurationError(
                f"{setting_name} rule for {executable} has invalid fields"
            )
        allowed_arguments = raw_rule["allowed_arguments"]
        if not isinstance(allowed_arguments, list) or not all(
            isinstance(arguments, list)
            and all(isinstance(argument, str) for argument in arguments)
            for arguments in allowed_arguments
        ):
            raise ConfigurationError(
                f"{setting_name} allowed_arguments must be arrays of strings"
            )
        working_directories = raw_rule["allowed_working_directories"]
        if not isinstance(working_directories, list) or not all(
            isinstance(directory, str) and directory
            for directory in working_directories
        ):
            raise ConfigurationError(
                f"{setting_name} working directories must be strings"
            )
        timeout = raw_rule["max_timeout_seconds"]
        if type(timeout) is not int or timeout <= 0:
            raise ConfigurationError(
                f"{setting_name} timeouts must be positive integers"
            )
        rules.append(
            ExecutableRule(
                executable=executable,
                allowed_arguments=tuple(
                    tuple(arguments) for arguments in allowed_arguments
                ),
                allowed_working_directories=tuple(working_directories),
                max_timeout_seconds=timeout,
            )
        )
    max_output_bytes = int(
        source.get("AGENTFORGE_QA_MAX_OUTPUT_BYTES", "65536")
    )
    if max_output_bytes <= 0:
        raise ConfigurationError(
            "AGENTFORGE_QA_MAX_OUTPUT_BYTES must be positive"
        )
    return QAExecutionPolicy(
        executable_rules=tuple(rules),
        max_output_bytes=max_output_bytes,
        network_access=False,
    )


def _load_qa_checks(source: Mapping[str, str]) -> tuple[QACheck, ...]:
    setting_name = "AGENTFORGE_QA_CHECKS"
    raw_value = source.get(setting_name, "[]")
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError as error:
        raise ConfigurationError(
            f"{setting_name} must be a valid JSON array"
        ) from error
    if not isinstance(payload, list):
        raise ConfigurationError(f"{setting_name} must be a JSON array")

    expected_fields = {
        "id",
        "executable",
        "arguments",
        "working_directory",
        "evidence_paths",
        "timeout_seconds",
    }
    checks = []
    for index, raw_check in enumerate(payload):
        if not isinstance(raw_check, dict) or set(raw_check) != expected_fields:
            raise ConfigurationError(
                f"{setting_name} check at index {index} has invalid fields"
            )
        arguments = raw_check["arguments"]
        evidence_paths = raw_check["evidence_paths"]
        if not isinstance(arguments, list) or not all(
            isinstance(argument, str) for argument in arguments
        ):
            raise ConfigurationError(
                f"{setting_name} arguments must be arrays of strings"
            )
        if not isinstance(evidence_paths, list) or not all(
            isinstance(path, str) for path in evidence_paths
        ):
            raise ConfigurationError(
                f"{setting_name} evidence_paths must be arrays of strings"
            )
        try:
            checks.append(
                QACheck(
                    id=raw_check["id"],
                    command=QACommand(
                        executable=raw_check["executable"],
                        arguments=tuple(arguments),
                        working_directory=raw_check["working_directory"],
                    ),
                    evidence_paths=tuple(evidence_paths),
                    timeout_seconds=raw_check["timeout_seconds"],
                )
            )
        except (TypeError, QAValidationError) as error:
            raise ConfigurationError(
                f"{setting_name} check at index {index} is invalid"
            ) from error

    check_ids = [check.id for check in checks]
    if len(set(check_ids)) != len(check_ids):
        raise ConfigurationError(
            f"{setting_name} must not contain duplicate check ids"
        )
    return tuple(checks)


def _validate_qa_configuration(
    policy: QAExecutionPolicy,
    checks: tuple[QACheck, ...],
) -> None:
    for check in checks:
        rule = policy.rule_for(check.command.executable)
        if rule is None:
            raise ConfigurationError(
                f"QA check {check.id} uses an executable outside the policy"
            )
        if check.command.arguments not in rule.allowed_arguments:
            raise ConfigurationError(
                f"QA check {check.id} uses arguments outside the policy"
            )
        if (
            check.command.working_directory
            not in rule.allowed_working_directories
        ):
            raise ConfigurationError(
                f"QA check {check.id} uses a working directory outside "
                "the policy"
            )
        if check.timeout_seconds > rule.max_timeout_seconds:
            raise ConfigurationError(
                f"QA check {check.id} exceeds the policy timeout"
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
