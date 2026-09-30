import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from core.models.review import ReviewStatus, TechnicalBaseline
from core.models.workspace import (
    ProjectSnapshot,
    WorkspacePathValidationError,
    validate_workspace_path,
)


class QAValidationError(ValueError):
    """Raised when a QA plan or artifact violates its contract."""


class QAStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_INPUT = "needs_input"


class QACheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    NOT_RUN = "not_run"


@dataclass(frozen=True)
class QACommand:
    executable: str
    arguments: tuple[str, ...]
    working_directory: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.executable, str)
            or not self.executable.strip()
            or not all(
                isinstance(argument, str) for argument in self.arguments
            )
        ):
            raise QAValidationError("QA command is invalid")
        if self.working_directory != ".":
            try:
                validate_workspace_path(self.working_directory)
            except WorkspacePathValidationError as error:
                raise QAValidationError(
                    "QA working directory must be canonical and relative"
                ) from error


@dataclass(frozen=True)
class QACheck:
    id: str
    command: QACommand
    evidence_paths: tuple[str, ...]
    timeout_seconds: int

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise QAValidationError("QA check id must not be empty")
        if not self.evidence_paths:
            raise QAValidationError("QA check requires evidence paths")
        try:
            for path in self.evidence_paths:
                validate_workspace_path(path)
        except WorkspacePathValidationError as error:
            raise QAValidationError(
                "QA evidence paths must be canonical and relative"
            ) from error
        if len(set(self.evidence_paths)) != len(self.evidence_paths):
            raise QAValidationError(
                "QA evidence paths must not contain duplicates"
            )
        if type(self.timeout_seconds) is not int or self.timeout_seconds <= 0:
            raise QAValidationError("QA timeout must be a positive integer")


@dataclass(frozen=True)
class QAPlan:
    version: int
    selected_check_ids: tuple[str, ...]
    rationale: dict[str, str]
    missing_decisions: tuple[str, ...]

    @classmethod
    def from_json(cls, raw_output: str) -> "QAPlan":
        try:
            payload = json.loads(raw_output, parse_constant=_reject_constant)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise QAValidationError("QA plan must be strict JSON") from error
        required = {
            "version",
            "selected_check_ids",
            "rationale",
            "missing_decisions",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            raise QAValidationError("QA plan has invalid fields")
        if type(payload["version"]) is not int or payload["version"] != 1:
            raise QAValidationError("QA plan version must be the integer 1")
        selected = _string_list(payload["selected_check_ids"], "selected_check_ids")
        missing = _string_list(payload["missing_decisions"], "missing_decisions")
        rationale = payload["rationale"]
        if not isinstance(rationale, dict) or not all(
            isinstance(key, str)
            and key.strip()
            and isinstance(value, str)
            and value.strip()
            for key, value in rationale.items()
        ):
            raise QAValidationError(
                "QA plan rationale must map ids to non-empty strings"
            )
        if set(rationale) != set(selected):
            raise QAValidationError(
                "QA plan rationale must match selected_check_ids"
            )
        if missing and selected:
            raise QAValidationError(
                "A QA plan with missing decisions cannot select checks"
            )
        return cls(
            version=1,
            selected_check_ids=tuple(selected),
            rationale=dict(rationale),
            missing_decisions=tuple(missing),
        )

    @staticmethod
    def output_contract(allowed_check_ids: list[str]) -> dict[str, Any]:
        return {
            "instruction": (
                "Return only strict JSON. Select and order only supplied "
                "check ids. Never output executables, commands, arguments, "
                "paths or shell text. Include every required check."
            ),
            "type": "object",
            "required": [
                "version",
                "selected_check_ids",
                "rationale",
                "missing_decisions",
            ],
            "properties": {
                "version": {"type": "integer", "const": 1},
                "selected_check_ids": {
                    "type": "array",
                    "items": {"type": "string", "enum": allowed_check_ids},
                },
                "rationale": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "missing_decisions": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "additionalProperties": False,
        }


@dataclass(frozen=True)
class ExecutableRule:
    executable: str
    allowed_arguments: tuple[tuple[str, ...], ...]
    allowed_working_directories: tuple[str, ...]
    max_timeout_seconds: int


@dataclass(frozen=True)
class QAExecutionPolicy:
    executable_rules: tuple[ExecutableRule, ...]
    max_output_bytes: int
    network_access: bool = False

    def __post_init__(self) -> None:
        executables = [rule.executable for rule in self.executable_rules]
        if len(set(executables)) != len(executables):
            raise QAValidationError(
                "QA execution policy contains duplicate executables"
            )
        if self.max_output_bytes <= 0:
            raise QAValidationError(
                "QA execution policy output limit must be positive"
            )

    def rule_for(self, executable: str) -> ExecutableRule | None:
        return next(
            (
                rule
                for rule in self.executable_rules
                if rule.executable == executable
            ),
            None,
        )


@dataclass(frozen=True)
class ReviewGate:
    task_id: UUID
    result_id: UUID
    status: ReviewStatus

    def to_dict(self) -> dict[str, str]:
        return {
            "task_id": str(self.task_id),
            "result_id": str(self.result_id),
            "status": str(self.status),
        }


@dataclass(frozen=True)
class QACheckResult:
    id: str
    status: QACheckStatus
    exit_code: int | None
    duration_ms: int
    stdout_excerpt: str
    stderr_excerpt: str
    output_truncated: bool
    failure_reason: str | None
    evidence_hashes: dict[str, str]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.id, str)
            or not self.id.strip()
            or type(self.duration_ms) is not int
            or self.duration_ms < 0
        ):
            raise QAValidationError("QA check result is invalid")
        if not all(
            isinstance(path, str)
            and path
            and isinstance(digest, str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
            for path, digest in self.evidence_hashes.items()
        ):
            raise QAValidationError("QA evidence hashes are invalid")

    @classmethod
    def from_dict(cls, payload: Any, index: int) -> "QACheckResult":
        required = {
            "id",
            "status",
            "exit_code",
            "duration_ms",
            "stdout_excerpt",
            "stderr_excerpt",
            "output_truncated",
            "failure_reason",
            "evidence_hashes",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            raise QAValidationError(
                f"QA check result at index {index} has invalid fields"
            )
        try:
            status = QACheckStatus(payload["status"])
        except (TypeError, ValueError) as error:
            raise QAValidationError(
                f"QA check result at index {index} has invalid status"
            ) from error
        if payload["exit_code"] is not None and type(payload["exit_code"]) is not int:
            raise QAValidationError("QA check exit_code must be an integer or null")
        if type(payload["duration_ms"]) is not int:
            raise QAValidationError("QA check duration_ms must be an integer")
        if not all(
            isinstance(payload[field], str)
            for field in ("id", "stdout_excerpt", "stderr_excerpt")
        ):
            raise QAValidationError("QA check text fields are invalid")
        if type(payload["output_truncated"]) is not bool:
            raise QAValidationError("QA output_truncated must be boolean")
        failure_reason = payload["failure_reason"]
        if failure_reason is not None and not isinstance(failure_reason, str):
            raise QAValidationError("QA failure_reason must be text or null")
        if not isinstance(payload["evidence_hashes"], dict):
            raise QAValidationError("QA evidence_hashes must be an object")
        return cls(
            id=payload["id"],
            status=status,
            exit_code=payload["exit_code"],
            duration_ms=payload["duration_ms"],
            stdout_excerpt=payload["stdout_excerpt"],
            stderr_excerpt=payload["stderr_excerpt"],
            output_truncated=payload["output_truncated"],
            failure_reason=failure_reason,
            evidence_hashes=dict(payload["evidence_hashes"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": str(self.status),
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "stdout_excerpt": self.stdout_excerpt,
            "stderr_excerpt": self.stderr_excerpt,
            "output_truncated": self.output_truncated,
            "failure_reason": self.failure_reason,
            "evidence_hashes": self.evidence_hashes,
        }


@dataclass(frozen=True)
class QAExecutionResult:
    checks: tuple[QACheckResult, ...]


@dataclass(frozen=True)
class QAArtifact:
    version: int
    status: QAStatus
    summary: str
    technical_baseline: TechnicalBaseline
    review_gate: ReviewGate | None
    workspace_fingerprint: str | None
    checks: tuple[QACheckResult, ...]
    missing_decisions: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != 1:
            raise QAValidationError("QA artifact version must be 1")
        if not self.summary.strip():
            raise QAValidationError("QA artifact summary must not be empty")
        if self.workspace_fingerprint is not None and re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            self.workspace_fingerprint,
        ) is None:
            raise QAValidationError("QA artifact fingerprint is invalid")
        if self.status in (QAStatus.PASSED, QAStatus.FAILED):
            if not self.checks or self.missing_decisions:
                raise QAValidationError(
                    "An executed QA artifact requires checks and no missing "
                    "decisions"
                )
            if (
                self.review_gate is None
                or self.review_gate.status is not ReviewStatus.APPROVED
                or self.workspace_fingerprint is None
            ):
                raise QAValidationError(
                    "Executed QA requires an approved gate and fingerprint"
                )
        check_ids = [check.id for check in self.checks]
        if len(set(check_ids)) != len(check_ids):
            raise QAValidationError("QA artifact check ids must be unique")
        if self.status is QAStatus.PASSED and any(
            check.status is not QACheckStatus.PASSED for check in self.checks
        ):
            raise QAValidationError("Passed QA requires every check to pass")
        if self.status is QAStatus.FAILED and not any(
            check.status in (QACheckStatus.FAILED, QACheckStatus.ERROR)
            for check in self.checks
        ):
            raise QAValidationError("Failed QA requires a failed check")
        if self.status is QAStatus.BLOCKED and self.checks:
            if (
                self.review_gate is None
                or self.review_gate.status is not ReviewStatus.APPROVED
                or self.workspace_fingerprint is None
                or any(
                    check.status is not QACheckStatus.NOT_RUN
                    for check in self.checks
                )
            ):
                raise QAValidationError(
                    "Blocked QA checks must be approved and not run"
                )
        if self.status is QAStatus.NEEDS_INPUT and not self.missing_decisions:
            raise QAValidationError(
                "Needs-input QA requires missing decisions"
            )

    @classmethod
    def from_json(cls, raw_output: str) -> "QAArtifact":
        try:
            payload = json.loads(raw_output, parse_constant=_reject_constant)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise QAValidationError("QA artifact must be strict JSON") from error
        required = {
            "version",
            "status",
            "summary",
            "technical_baseline",
            "review_gate",
            "workspace_fingerprint",
            "checks",
            "missing_decisions",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            raise QAValidationError("QA artifact has invalid fields")
        try:
            status = QAStatus(payload["status"])
            baseline = TechnicalBaseline.from_dict(
                payload["technical_baseline"]
            )
        except (TypeError, ValueError) as error:
            raise QAValidationError("QA artifact has invalid domain values") from error
        gate_payload = payload["review_gate"]
        gate = None
        if gate_payload is not None:
            if not isinstance(gate_payload, dict) or set(gate_payload) != {
                "task_id",
                "result_id",
                "status",
            }:
                raise QAValidationError("QA review_gate has invalid fields")
            try:
                gate = ReviewGate(
                    task_id=UUID(gate_payload["task_id"]),
                    result_id=UUID(gate_payload["result_id"]),
                    status=ReviewStatus(gate_payload["status"]),
                )
            except (TypeError, ValueError) as error:
                raise QAValidationError("QA review_gate is invalid") from error
        raw_checks = payload["checks"]
        if not isinstance(raw_checks, list):
            raise QAValidationError("QA artifact checks must be an array")
        missing = _string_list(payload["missing_decisions"], "missing_decisions")
        if not isinstance(payload["summary"], str):
            raise QAValidationError("QA artifact summary must be text")
        return cls(
            version=payload["version"],
            status=status,
            summary=payload["summary"],
            technical_baseline=baseline,
            review_gate=gate,
            workspace_fingerprint=payload["workspace_fingerprint"],
            checks=tuple(
                QACheckResult.from_dict(check, index)
                for index, check in enumerate(raw_checks)
            ),
            missing_decisions=tuple(missing),
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": self.version,
                "status": str(self.status),
                "summary": self.summary,
                "technical_baseline": self.technical_baseline.to_dict(),
                "review_gate": (
                    self.review_gate.to_dict()
                    if self.review_gate is not None
                    else None
                ),
                "workspace_fingerprint": self.workspace_fingerprint,
                "checks": [check.to_dict() for check in self.checks],
                "missing_decisions": list(self.missing_decisions),
            },
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


FORBIDDEN_COMMAND_FRAGMENTS = (
    "&&",
    "||",
    "|",
    ";",
    ">",
    "<",
    "`",
    "$(",
    "\n",
    "\r",
    "\x00",
)

FORBIDDEN_DEPENDENCY_ACTIONS = frozenset(
    {"install", "add", "update", "upgrade", "bootstrap"}
)


def validate_authorized_check(
    check: QACheck,
    snapshot: ProjectSnapshot,
    policy: QAExecutionPolicy,
) -> dict[str, str]:
    command = check.command
    if (
        "/" in command.executable
        or "\\" in command.executable
        or not command.executable.strip()
    ):
        raise QAValidationError("QA executable must be a bare name")
    command_values = (
        command.executable,
        *command.arguments,
    )
    if any(
        fragment in value
        for value in command_values
        for fragment in FORBIDDEN_COMMAND_FRAGMENTS
    ):
        raise QAValidationError(
            f"QA check {check.id} contains forbidden shell syntax"
        )
    for argument in command.arguments:
        normalized = argument.replace("\\", "/")
        if (
            normalized.startswith("/")
            or normalized == ".."
            or normalized.startswith("../")
            or "/../" in normalized
            or normalized.endswith("/..")
            or re.match(r"^[A-Za-z]:/", normalized)
        ):
            raise QAValidationError(
                f"QA check {check.id} contains an unsafe argument path"
            )
        if argument.casefold() in FORBIDDEN_DEPENDENCY_ACTIONS:
            raise QAValidationError(
                f"QA check {check.id} requests dependency installation"
            )
    rule = policy.rule_for(command.executable)
    if rule is None:
        raise QAValidationError(
            f"QA executable is not allowed: {command.executable}"
        )
    if command.arguments not in rule.allowed_arguments:
        raise QAValidationError(
            f"QA arguments are not allowed for check: {check.id}"
        )
    if command.working_directory not in rule.allowed_working_directories:
        raise QAValidationError(
            f"QA working directory is not allowed for check: {check.id}"
        )
    if check.timeout_seconds > rule.max_timeout_seconds:
        raise QAValidationError(
            f"QA timeout exceeds policy for check: {check.id}"
        )
    available_files = {
        workspace_file.relative_path: workspace_file.content
        for workspace_file in snapshot.files
    }
    missing_evidence = sorted(
        set(check.evidence_paths).difference(available_files)
    )
    if missing_evidence:
        raise QAValidationError(
            f"QA check {check.id} lacks workspace evidence: "
            + ", ".join(missing_evidence)
        )
    if command.working_directory != "." and not any(
        path == command.working_directory
        or path.startswith(f"{command.working_directory}/")
        for path in available_files
    ):
        raise QAValidationError(
            f"QA working directory does not exist: "
            f"{command.working_directory}"
        )
    return {
        path: "sha256:"
        + hashlib.sha256(available_files[path].encode("utf-8")).hexdigest()
        for path in check.evidence_paths
    }


def _string_list(payload: Any, field_name: str) -> list[str]:
    if not isinstance(payload, list) or not all(
        isinstance(item, str) and item.strip() for item in payload
    ):
        raise QAValidationError(
            f"QA plan {field_name} must be an array of non-empty strings"
        )
    if len(set(payload)) != len(payload):
        raise QAValidationError(
            f"QA plan {field_name} must not contain duplicates"
        )
    return payload


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-JSON numeric constant: {value}")
