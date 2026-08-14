import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any


class WorkspacePathValidationError(ValueError):
    """Raised when a logical workspace path is unsafe."""


def validate_workspace_path(relative_path: str) -> None:
    if not isinstance(relative_path, str) or not relative_path:
        raise WorkspacePathValidationError(
            "Workspace path must be a non-empty string"
        )
    if relative_path != relative_path.strip():
        raise WorkspacePathValidationError(
            "Workspace path must not have surrounding whitespace"
        )
    if "\x00" in relative_path or "\\" in relative_path:
        raise WorkspacePathValidationError(
            "Workspace path contains forbidden characters"
        )

    raw_parts = relative_path.split("/")
    if any(part in ("", ".", "..") for part in raw_parts):
        raise WorkspacePathValidationError(
            "Workspace path must be canonical and relative"
        )

    path = PurePosixPath(relative_path)
    if path.is_absolute() or str(path) != relative_path:
        raise WorkspacePathValidationError(
            "Workspace path must be canonical and relative"
        )


@dataclass(frozen=True)
class WorkspaceFile:
    relative_path: str
    content: str

    def __post_init__(self) -> None:
        validate_workspace_path(self.relative_path)
        if not isinstance(self.content, str):
            raise TypeError("Workspace file content must be text")


class BackendGenerationValidationError(ValueError):
    """Raised when a backend engine output violates its contract."""


class BackendGenerationStatus(StrEnum):
    COMPLETE = "complete"
    NEEDS_INPUT = "needs_input"


@dataclass(frozen=True)
class BackendGenerationArtifact:
    version: int
    status: BackendGenerationStatus
    missing_decisions: list[str]
    backend_stack: dict[str, Any]
    backend_architecture: dict[str, Any]
    infrastructure: dict[str, Any]
    technical_constraints: list[str]
    files: list[WorkspaceFile]
    summary: str

    @classmethod
    def from_json(cls, raw_output: str) -> "BackendGenerationArtifact":
        try:
            payload = json.loads(
                raw_output,
                parse_constant=_reject_non_json_constant,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise BackendGenerationValidationError(
                "Backend output must be strict JSON"
            ) from error

        if not isinstance(payload, dict):
            raise BackendGenerationValidationError(
                "Backend output must be a JSON object"
            )

        required_fields = set(cls.required_fields())
        missing_fields = sorted(required_fields.difference(payload))
        if missing_fields:
            raise BackendGenerationValidationError(
                "Backend output is missing required fields: "
                + ", ".join(missing_fields)
            )

        unknown_fields = sorted(set(payload).difference(required_fields))
        if unknown_fields:
            raise BackendGenerationValidationError(
                "Backend output contains unknown fields: "
                + ", ".join(unknown_fields)
            )

        if type(payload["version"]) is not int or payload["version"] != 1:
            raise BackendGenerationValidationError(
                "Backend output version must be the integer 1"
            )

        try:
            status = BackendGenerationStatus(payload["status"])
        except (TypeError, ValueError) as error:
            raise BackendGenerationValidationError(
                "Backend output status must be complete or needs_input"
            ) from error

        missing_decisions = payload["missing_decisions"]
        if not isinstance(missing_decisions, list) or not all(
            isinstance(item, str) and item.strip()
            for item in missing_decisions
        ):
            raise BackendGenerationValidationError(
                "Backend field missing_decisions must be an array of "
                "non-empty strings"
            )
        if len(set(missing_decisions)) != len(missing_decisions):
            raise BackendGenerationValidationError(
                "Backend field missing_decisions must not contain duplicates"
            )

        object_fields = (
            "backend_stack",
            "backend_architecture",
            "infrastructure",
        )
        for field_name in object_fields:
            if not isinstance(payload[field_name], dict):
                raise BackendGenerationValidationError(
                    f"Backend field {field_name} must be an object"
                )

        constraints = payload["technical_constraints"]
        if not isinstance(constraints, list) or not all(
            isinstance(item, str) for item in constraints
        ):
            raise BackendGenerationValidationError(
                "Backend field technical_constraints must be "
                "an array of strings"
            )

        summary = payload["summary"]
        if not isinstance(summary, str) or not summary.strip():
            raise BackendGenerationValidationError(
                "Backend field summary must be a non-empty string"
            )

        raw_files = payload["files"]
        if not isinstance(raw_files, list):
            raise BackendGenerationValidationError(
                "Backend field files must be an array"
            )

        files = []
        for index, raw_file in enumerate(raw_files):
            if not isinstance(raw_file, dict):
                raise BackendGenerationValidationError(
                    f"Backend file at index {index} must be an object"
                )
            if set(raw_file) != {"path", "content"}:
                raise BackendGenerationValidationError(
                    f"Backend file at index {index} must contain only "
                    "path and content"
                )
            if not isinstance(raw_file["content"], str) or not raw_file[
                "content"
            ].strip():
                raise BackendGenerationValidationError(
                    f"Backend file at index {index} must contain text"
                )
            try:
                workspace_file = WorkspaceFile(
                    relative_path=raw_file["path"],
                    content=raw_file["content"],
                )
            except (TypeError, WorkspacePathValidationError) as error:
                raise BackendGenerationValidationError(
                    f"Backend file at index {index} has an unsafe path"
                ) from error
            if workspace_file.relative_path == "architecture.json":
                raise BackendGenerationValidationError(
                    "Backend output cannot overwrite architecture.json"
                )
            files.append(workspace_file)

        paths = [workspace_file.relative_path for workspace_file in files]
        if len(set(paths)) != len(paths):
            raise BackendGenerationValidationError(
                "Backend output contains duplicate file paths"
            )

        if status is BackendGenerationStatus.COMPLETE:
            if missing_decisions:
                raise BackendGenerationValidationError(
                    "A complete backend output cannot have missing decisions"
                )
            if not files:
                raise BackendGenerationValidationError(
                    "A complete backend output must contain files"
                )
        elif not missing_decisions or files:
            raise BackendGenerationValidationError(
                "A needs_input backend output requires missing decisions "
                "and cannot contain files"
            )

        return cls(
            version=payload["version"],
            status=status,
            missing_decisions=missing_decisions,
            backend_stack=payload["backend_stack"],
            backend_architecture=payload["backend_architecture"],
            infrastructure=payload["infrastructure"],
            technical_constraints=constraints,
            files=files,
            summary=summary,
        )

    @staticmethod
    def required_fields() -> tuple[str, ...]:
        return (
            "version",
            "status",
            "missing_decisions",
            "backend_stack",
            "backend_architecture",
            "infrastructure",
            "technical_constraints",
            "files",
            "summary",
        )

    @classmethod
    def output_contract(cls) -> dict[str, Any]:
        return {
            "instruction": (
                "Return only one strict JSON object. Generate real source "
                "files with relative POSIX paths and complete text content. "
                "Do not use Markdown or code fences."
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
                "backend_stack": {"type": "object"},
                "backend_architecture": {"type": "object"},
                "infrastructure": {"type": "object"},
                "technical_constraints": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["path", "content"],
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
                "summary": {"type": "string"},
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
            "infrastructure": self.infrastructure,
            "technical_constraints": self.technical_constraints,
            "files": [
                {
                    "path": workspace_file.relative_path,
                    "content": workspace_file.content,
                }
                for workspace_file in self.files
            ],
            "summary": self.summary,
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
