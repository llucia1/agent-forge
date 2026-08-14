import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ArchitectureArtifactValidationError(ValueError):
    """Raised when an architecture engine output violates its contract."""


class ArchitectureStatus(StrEnum):
    COMPLETE = "complete"
    NEEDS_INPUT = "needs_input"


@dataclass(frozen=True)
class ArchitectureArtifact:
    version: int
    status: ArchitectureStatus
    missing_decisions: list[str]
    backend_stack: dict[str, Any]
    backend_architecture: dict[str, Any]
    frontend_stack: dict[str, Any]
    frontend_architecture: dict[str, Any]
    infrastructure: dict[str, Any]
    technical_constraints: list[str]
    modules: list[dict[str, Any]]
    interfaces: list[dict[str, Any]]
    apis: list[dict[str, Any]]
    persistence: dict[str, Any]
    execution_plan: list[dict[str, Any]]

    @classmethod
    def from_json(cls, raw_output: str) -> "ArchitectureArtifact":
        try:
            payload = json.loads(
                raw_output,
                parse_constant=_reject_non_json_constant,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ArchitectureArtifactValidationError(
                "Architecture output must be strict JSON"
            ) from error

        if not isinstance(payload, dict):
            raise ArchitectureArtifactValidationError(
                "Architecture output must be a JSON object"
            )

        required_fields = set(cls.required_fields())
        missing_fields = sorted(required_fields.difference(payload))
        if missing_fields:
            raise ArchitectureArtifactValidationError(
                "Architecture output is missing required fields: "
                + ", ".join(missing_fields)
            )

        unknown_fields = sorted(set(payload).difference(required_fields))
        if unknown_fields:
            raise ArchitectureArtifactValidationError(
                "Architecture output contains unknown fields: "
                + ", ".join(unknown_fields)
            )

        if type(payload["version"]) is not int or payload["version"] != 1:
            raise ArchitectureArtifactValidationError(
                "Architecture output version must be the integer 1"
            )

        try:
            status = ArchitectureStatus(payload["status"])
        except (TypeError, ValueError) as error:
            raise ArchitectureArtifactValidationError(
                "Architecture output status must be complete or needs_input"
            ) from error

        missing_decisions = payload["missing_decisions"]
        if not isinstance(missing_decisions, list) or not all(
            isinstance(item, str) and item.strip()
            for item in missing_decisions
        ):
            raise ArchitectureArtifactValidationError(
                "Architecture field missing_decisions must be "
                "an array of non-empty strings"
            )
        if len(set(missing_decisions)) != len(missing_decisions):
            raise ArchitectureArtifactValidationError(
                "Architecture field missing_decisions must not "
                "contain duplicates"
            )

        object_fields = (
            "backend_stack",
            "backend_architecture",
            "frontend_stack",
            "frontend_architecture",
            "infrastructure",
            "persistence",
        )
        for field_name in object_fields:
            if not isinstance(payload[field_name], dict):
                raise ArchitectureArtifactValidationError(
                    f"Architecture field {field_name} must be an object"
                )

        constraints = payload["technical_constraints"]
        if not isinstance(constraints, list) or not all(
            isinstance(item, str) for item in constraints
        ):
            raise ArchitectureArtifactValidationError(
                "Architecture field technical_constraints must be "
                "an array of strings"
            )

        object_list_fields = (
            "modules",
            "interfaces",
            "apis",
            "execution_plan",
        )
        for field_name in object_list_fields:
            value = payload[field_name]
            if not isinstance(value, list) or not all(
                isinstance(item, dict) for item in value
            ):
                raise ArchitectureArtifactValidationError(
                    f"Architecture field {field_name} must be "
                    "an array of objects"
                )

        if status is ArchitectureStatus.COMPLETE and missing_decisions:
            raise ArchitectureArtifactValidationError(
                "A complete architecture cannot have missing decisions"
            )
        if status is ArchitectureStatus.NEEDS_INPUT:
            if not missing_decisions:
                raise ArchitectureArtifactValidationError(
                    "A needs_input architecture requires missing decisions"
                )
            definitive_fields = (
                payload["modules"],
                payload["interfaces"],
                payload["apis"],
                payload["persistence"],
                payload["execution_plan"],
            )
            if any(definitive_fields):
                raise ArchitectureArtifactValidationError(
                    "A needs_input architecture cannot contain a "
                    "definitive design"
                )

        return cls(
            version=payload["version"],
            status=status,
            missing_decisions=missing_decisions,
            backend_stack=payload["backend_stack"],
            backend_architecture=payload["backend_architecture"],
            frontend_stack=payload["frontend_stack"],
            frontend_architecture=payload["frontend_architecture"],
            infrastructure=payload["infrastructure"],
            technical_constraints=constraints,
            modules=payload["modules"],
            interfaces=payload["interfaces"],
            apis=payload["apis"],
            persistence=payload["persistence"],
            execution_plan=payload["execution_plan"],
        )

    @staticmethod
    def required_fields() -> tuple[str, ...]:
        return (
            "version",
            "status",
            "missing_decisions",
            "backend_stack",
            "backend_architecture",
            "frontend_stack",
            "frontend_architecture",
            "infrastructure",
            "technical_constraints",
            "modules",
            "interfaces",
            "apis",
            "persistence",
            "execution_plan",
        )

    @classmethod
    def output_contract(cls) -> dict[str, Any]:
        object_schema = {"type": "object"}
        object_array_schema = {
            "type": "array",
            "items": {"type": "object"},
        }
        return {
            "instruction": (
                "Return only one strict JSON object. Do not use Markdown "
                "or code fences."
            ),
            "type": "object",
            "required": list(cls.required_fields()),
            "properties": {
                "version": {"type": "integer", "const": 1},
                "status": {
                    "type": "string",
                    "enum": ["complete", "needs_input"],
                },
                "missing_decisions": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "backend_stack": object_schema,
                "backend_architecture": object_schema,
                "frontend_stack": object_schema,
                "frontend_architecture": object_schema,
                "infrastructure": object_schema,
                "technical_constraints": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "modules": object_array_schema,
                "interfaces": object_array_schema,
                "apis": object_array_schema,
                "persistence": object_schema,
                "execution_plan": object_array_schema,
            },
            "additionalProperties": False,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "status": str(self.status),
            "missing_decisions": self.missing_decisions,
            "backend_stack": self.backend_stack,
            "backend_architecture": self.backend_architecture,
            "frontend_stack": self.frontend_stack,
            "frontend_architecture": self.frontend_architecture,
            "infrastructure": self.infrastructure,
            "technical_constraints": self.technical_constraints,
            "modules": self.modules,
            "interfaces": self.interfaces,
            "apis": self.apis,
            "persistence": self.persistence,
            "execution_plan": self.execution_plan,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"Non-JSON numeric constant: {value}")
