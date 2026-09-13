import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from core.models.workspace import (
    WorkspacePathValidationError,
    validate_workspace_path,
)


TECHNICAL_BASELINE_FIELDS = (
    "backend_stack",
    "backend_architecture",
    "frontend_stack",
    "frontend_architecture",
    "infrastructure",
    "technical_constraints",
)


class ReviewValidationError(ValueError):
    """Raised when a reviewer engine output violates its contract."""


class ReviewStatus(StrEnum):
    APPROVED = "approved"
    CHANGES_REQUIRED = "changes_required"
    NEEDS_INPUT = "needs_input"


class FindingSeverity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FindingScope(StrEnum):
    BACKEND = "backend"
    FRONTEND = "frontend"
    INTEGRATION = "integration"
    ARCHITECTURE = "architecture"


@dataclass(frozen=True)
class TechnicalBaseline:
    backend_stack: dict[str, Any]
    backend_architecture: dict[str, Any]
    frontend_stack: dict[str, Any]
    frontend_architecture: dict[str, Any]
    infrastructure: dict[str, Any]
    technical_constraints: list[str]

    @classmethod
    def from_dict(cls, payload: Any) -> "TechnicalBaseline":
        if not isinstance(payload, dict):
            raise ReviewValidationError(
                "Review field technical_baseline must be an object"
            )
        if set(payload) != set(TECHNICAL_BASELINE_FIELDS):
            raise ReviewValidationError(
                "Review field technical_baseline must contain exactly: "
                + ", ".join(TECHNICAL_BASELINE_FIELDS)
            )

        for field_name in TECHNICAL_BASELINE_FIELDS[:-1]:
            if not isinstance(payload[field_name], dict):
                raise ReviewValidationError(
                    f"Review baseline field {field_name} must be an object"
                )

        constraints = payload["technical_constraints"]
        if not isinstance(constraints, list) or not all(
            isinstance(item, str) for item in constraints
        ):
            raise ReviewValidationError(
                "Review baseline field technical_constraints must be "
                "an array of strings"
            )

        return cls(
            backend_stack=payload["backend_stack"],
            backend_architecture=payload["backend_architecture"],
            frontend_stack=payload["frontend_stack"],
            frontend_architecture=payload["frontend_architecture"],
            infrastructure=payload["infrastructure"],
            technical_constraints=constraints,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend_stack": self.backend_stack,
            "backend_architecture": self.backend_architecture,
            "frontend_stack": self.frontend_stack,
            "frontend_architecture": self.frontend_architecture,
            "infrastructure": self.infrastructure,
            "technical_constraints": self.technical_constraints,
        }


@dataclass(frozen=True)
class ReviewFinding:
    id: str
    severity: FindingSeverity
    scope: FindingScope
    path: str | None
    rule: str
    message: str
    suggested_action: str

    @classmethod
    def from_dict(cls, payload: Any, index: int) -> "ReviewFinding":
        required_fields = {
            "id",
            "severity",
            "scope",
            "path",
            "rule",
            "message",
            "suggested_action",
        }
        if not isinstance(payload, dict) or set(payload) != required_fields:
            raise ReviewValidationError(
                f"Review finding at index {index} has invalid fields"
            )

        text_fields = (
            "id",
            "rule",
            "message",
            "suggested_action",
        )
        for field_name in text_fields:
            if not isinstance(payload[field_name], str) or not payload[
                field_name
            ].strip():
                raise ReviewValidationError(
                    f"Review finding field {field_name} must be a "
                    "non-empty string"
                )

        try:
            severity = FindingSeverity(payload["severity"])
        except (TypeError, ValueError) as error:
            raise ReviewValidationError(
                "Review finding severity is invalid"
            ) from error

        try:
            scope = FindingScope(payload["scope"])
        except (TypeError, ValueError) as error:
            raise ReviewValidationError(
                "Review finding scope is invalid"
            ) from error

        path = payload["path"]
        if path is not None:
            try:
                validate_workspace_path(path)
            except WorkspacePathValidationError as error:
                raise ReviewValidationError(
                    f"Review finding at index {index} has an unsafe path"
                ) from error

        return cls(
            id=payload["id"],
            severity=severity,
            scope=scope,
            path=path,
            rule=payload["rule"],
            message=payload["message"],
            suggested_action=payload["suggested_action"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": str(self.severity),
            "scope": str(self.scope),
            "path": self.path,
            "rule": self.rule,
            "message": self.message,
            "suggested_action": self.suggested_action,
        }


@dataclass(frozen=True)
class ReviewArtifact:
    version: int
    status: ReviewStatus
    summary: str
    technical_baseline: TechnicalBaseline
    findings: list[ReviewFinding]
    checked_rules: list[str]
    missing_decisions: list[str]

    @classmethod
    def from_json(cls, raw_output: str) -> "ReviewArtifact":
        try:
            payload = json.loads(
                raw_output,
                parse_constant=_reject_non_json_constant,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ReviewValidationError(
                "Review output must be strict JSON"
            ) from error

        if not isinstance(payload, dict):
            raise ReviewValidationError(
                "Review output must be a JSON object"
            )

        required_fields = set(cls.required_fields())
        missing_fields = sorted(required_fields.difference(payload))
        if missing_fields:
            raise ReviewValidationError(
                "Review output is missing required fields: "
                + ", ".join(missing_fields)
            )
        unknown_fields = sorted(set(payload).difference(required_fields))
        if unknown_fields:
            raise ReviewValidationError(
                "Review output contains unknown fields: "
                + ", ".join(unknown_fields)
            )

        if type(payload["version"]) is not int or payload["version"] != 1:
            raise ReviewValidationError(
                "Review output version must be the integer 1"
            )

        try:
            status = ReviewStatus(payload["status"])
        except (TypeError, ValueError) as error:
            raise ReviewValidationError(
                "Review output status is invalid"
            ) from error

        summary = payload["summary"]
        if not isinstance(summary, str) or not summary.strip():
            raise ReviewValidationError(
                "Review field summary must be a non-empty string"
            )

        technical_baseline = TechnicalBaseline.from_dict(
            payload["technical_baseline"]
        )
        findings = cls._parse_findings(payload["findings"])
        checked_rules = cls._parse_string_list(
            payload["checked_rules"],
            "checked_rules",
        )
        missing_decisions = cls._parse_string_list(
            payload["missing_decisions"],
            "missing_decisions",
        )

        if status is ReviewStatus.APPROVED:
            blocking_severities = {
                FindingSeverity.CRITICAL,
                FindingSeverity.HIGH,
            }
            if any(
                finding.severity in blocking_severities
                for finding in findings
            ):
                raise ReviewValidationError(
                    "An approved review cannot contain blocking findings"
                )
            if missing_decisions or not checked_rules:
                raise ReviewValidationError(
                    "An approved review requires checked rules and no "
                    "missing decisions"
                )
        elif status is ReviewStatus.CHANGES_REQUIRED:
            if not findings or missing_decisions or not checked_rules:
                raise ReviewValidationError(
                    "A changes_required review requires findings, checked "
                    "rules and no missing decisions"
                )
        elif not missing_decisions or findings:
            raise ReviewValidationError(
                "A needs_input review requires missing decisions and no "
                "findings"
            )

        return cls(
            version=payload["version"],
            status=status,
            summary=summary,
            technical_baseline=technical_baseline,
            findings=findings,
            checked_rules=checked_rules,
            missing_decisions=missing_decisions,
        )

    @staticmethod
    def _parse_findings(payload: Any) -> list[ReviewFinding]:
        if not isinstance(payload, list):
            raise ReviewValidationError(
                "Review field findings must be an array"
            )
        findings = [
            ReviewFinding.from_dict(raw_finding, index)
            for index, raw_finding in enumerate(payload)
        ]
        finding_ids = [finding.id for finding in findings]
        if len(set(finding_ids)) != len(finding_ids):
            raise ReviewValidationError(
                "Review findings must have unique ids"
            )
        return findings

    @staticmethod
    def _parse_string_list(payload: Any, field_name: str) -> list[str]:
        if not isinstance(payload, list) or not all(
            isinstance(item, str) and item.strip() for item in payload
        ):
            raise ReviewValidationError(
                f"Review field {field_name} must be an array of "
                "non-empty strings"
            )
        if len(set(payload)) != len(payload):
            raise ReviewValidationError(
                f"Review field {field_name} must not contain duplicates"
            )
        return payload

    @staticmethod
    def required_fields() -> tuple[str, ...]:
        return (
            "version",
            "status",
            "summary",
            "technical_baseline",
            "findings",
            "checked_rules",
            "missing_decisions",
        )

    @classmethod
    def output_contract(cls) -> dict[str, Any]:
        baseline_properties = {
            field_name: (
                {"type": "array", "items": {"type": "string"}}
                if field_name == "technical_constraints"
                else {"type": "object"}
            )
            for field_name in TECHNICAL_BASELINE_FIELDS
        }
        return {
            "instruction": (
                "Return only one strict JSON object. Review only against "
                "the supplied authoritative decisions and rule catalog. "
                "Do not assume or substitute technologies, patterns, "
                "libraries or infrastructure. Use a null path for global "
                "findings and only supplied workspace paths otherwise. Do "
                "not use Markdown or code fences."
            ),
            "type": "object",
            "required": list(cls.required_fields()),
            "properties": {
                "version": {"type": "integer", "const": 1},
                "status": {
                    "type": "string",
                    "enum": [
                        "approved",
                        "changes_required",
                        "needs_input",
                    ],
                },
                "summary": {"type": "string"},
                "technical_baseline": {
                    "type": "object",
                    "required": list(TECHNICAL_BASELINE_FIELDS),
                    "properties": baseline_properties,
                    "additionalProperties": False,
                },
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": [
                            "id",
                            "severity",
                            "scope",
                            "path",
                            "rule",
                            "message",
                            "suggested_action",
                        ],
                        "properties": {
                            "id": {"type": "string"},
                            "severity": {
                                "type": "string",
                                "enum": [
                                    "critical",
                                    "high",
                                    "medium",
                                    "low",
                                ],
                            },
                            "scope": {
                                "type": "string",
                                "enum": [
                                    "backend",
                                    "frontend",
                                    "integration",
                                    "architecture",
                                ],
                            },
                            "path": {"type": ["string", "null"]},
                            "rule": {"type": "string"},
                            "message": {"type": "string"},
                            "suggested_action": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
                "checked_rules": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "missing_decisions": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "additionalProperties": False,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "status": str(self.status),
            "summary": self.summary,
            "technical_baseline": self.technical_baseline.to_dict(),
            "findings": [finding.to_dict() for finding in self.findings],
            "checked_rules": self.checked_rules,
            "missing_decisions": self.missing_decisions,
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
