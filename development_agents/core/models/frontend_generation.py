import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from core.models.workspace import (
    WorkspaceFile,
    WorkspacePathValidationError,
    is_editable_workspace_path,
)


class FrontendGenerationValidationError(ValueError):
    """Raised when a frontend engine output violates its contract."""


class FrontendGenerationStatus(StrEnum):
    COMPLETE = "complete"
    NEEDS_INPUT = "needs_input"


@dataclass(frozen=True)
class FrontendGenerationArtifact:
    version: int
    status: FrontendGenerationStatus
    missing_decisions: list[str]
    frontend_stack: dict[str, Any]
    frontend_architecture: dict[str, Any]
    infrastructure: dict[str, Any]
    technical_constraints: list[str]
    files: list[WorkspaceFile]
    summary: str

    @classmethod
    def from_json(cls, raw_output: str) -> "FrontendGenerationArtifact":
        try:
            payload = json.loads(
                raw_output,
                parse_constant=_reject_non_json_constant,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise FrontendGenerationValidationError(
                "Frontend output must be strict JSON"
            ) from error

        if not isinstance(payload, dict):
            raise FrontendGenerationValidationError(
                "Frontend output must be a JSON object"
            )

        required_fields = set(cls.required_fields())
        missing_fields = sorted(required_fields.difference(payload))
        if missing_fields:
            raise FrontendGenerationValidationError(
                "Frontend output is missing required fields: "
                + ", ".join(missing_fields)
            )

        unknown_fields = sorted(set(payload).difference(required_fields))
        if unknown_fields:
            raise FrontendGenerationValidationError(
                "Frontend output contains unknown fields: "
                + ", ".join(unknown_fields)
            )

        if type(payload["version"]) is not int or payload["version"] != 1:
            raise FrontendGenerationValidationError(
                "Frontend output version must be the integer 1"
            )

        try:
            status = FrontendGenerationStatus(payload["status"])
        except (TypeError, ValueError) as error:
            raise FrontendGenerationValidationError(
                "Frontend output status must be complete or needs_input"
            ) from error

        missing_decisions = payload["missing_decisions"]
        if not isinstance(missing_decisions, list) or not all(
            isinstance(item, str) and item.strip()
            for item in missing_decisions
        ):
            raise FrontendGenerationValidationError(
                "Frontend field missing_decisions must be an array of "
                "non-empty strings"
            )
        if len(set(missing_decisions)) != len(missing_decisions):
            raise FrontendGenerationValidationError(
                "Frontend field missing_decisions must not contain duplicates"
            )

        object_fields = (
            "frontend_stack",
            "frontend_architecture",
            "infrastructure",
        )
        for field_name in object_fields:
            if not isinstance(payload[field_name], dict):
                raise FrontendGenerationValidationError(
                    f"Frontend field {field_name} must be an object"
                )

        constraints = payload["technical_constraints"]
        if not isinstance(constraints, list) or not all(
            isinstance(item, str) for item in constraints
        ):
            raise FrontendGenerationValidationError(
                "Frontend field technical_constraints must be "
                "an array of strings"
            )

        summary = payload["summary"]
        if not isinstance(summary, str) or not summary.strip():
            raise FrontendGenerationValidationError(
                "Frontend field summary must be a non-empty string"
            )

        raw_files = payload["files"]
        if not isinstance(raw_files, list):
            raise FrontendGenerationValidationError(
                "Frontend field files must be an array"
            )

        files = []
        for index, raw_file in enumerate(raw_files):
            workspace_file = cls._parse_file(raw_file, index)
            files.append(workspace_file)

        paths = [workspace_file.relative_path for workspace_file in files]
        if len(set(paths)) != len(paths):
            raise FrontendGenerationValidationError(
                "Frontend output contains duplicate file paths"
            )

        if status is FrontendGenerationStatus.COMPLETE:
            if missing_decisions:
                raise FrontendGenerationValidationError(
                    "A complete frontend output cannot have missing decisions"
                )
            if not files:
                raise FrontendGenerationValidationError(
                    "A complete frontend output must contain files"
                )
        elif not missing_decisions or files:
            raise FrontendGenerationValidationError(
                "A needs_input frontend output requires missing decisions "
                "and cannot contain files"
            )

        return cls(
            version=payload["version"],
            status=status,
            missing_decisions=missing_decisions,
            frontend_stack=payload["frontend_stack"],
            frontend_architecture=payload["frontend_architecture"],
            infrastructure=payload["infrastructure"],
            technical_constraints=constraints,
            files=files,
            summary=summary,
        )

    @staticmethod
    def _parse_file(raw_file: Any, index: int) -> WorkspaceFile:
        if not isinstance(raw_file, dict):
            raise FrontendGenerationValidationError(
                f"Frontend file at index {index} must be an object"
            )
        if set(raw_file) != {"path", "content"}:
            raise FrontendGenerationValidationError(
                f"Frontend file at index {index} must contain only "
                "path and content"
            )
        if not isinstance(raw_file["content"], str) or not raw_file[
            "content"
        ].strip():
            raise FrontendGenerationValidationError(
                f"Frontend file at index {index} must contain text"
            )

        try:
            workspace_file = WorkspaceFile(
                relative_path=raw_file["path"],
                content=raw_file["content"],
            )
            editable = is_editable_workspace_path(
                workspace_file.relative_path
            )
        except (TypeError, WorkspacePathValidationError) as error:
            raise FrontendGenerationValidationError(
                f"Frontend file at index {index} has an unsafe path"
            ) from error

        if not workspace_file.relative_path.startswith("frontend/"):
            raise FrontendGenerationValidationError(
                "Frontend output files must be under frontend/"
            )
        if workspace_file.relative_path.endswith("/architecture.json"):
            raise FrontendGenerationValidationError(
                "Frontend output cannot write architecture.json"
            )
        if not editable:
            raise FrontendGenerationValidationError(
                f"Frontend file at index {index} is not an editable "
                "text or source file"
            )
        return workspace_file

    @staticmethod
    def required_fields() -> tuple[str, ...]:
        return (
            "version",
            "status",
            "missing_decisions",
            "frontend_stack",
            "frontend_architecture",
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
                "and editable text files under frontend/ using relative "
                "POSIX paths and complete text content. Do not use Markdown "
                "or code fences. Do not write dependency, cache, build or "
                "generated-artifact directories."
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
                "frontend_stack": {"type": "object"},
                "frontend_architecture": {"type": "object"},
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
                            "path": {
                                "type": "string",
                                "pattern": "^frontend/",
                            },
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
            "frontend_stack": self.frontend_stack,
            "frontend_architecture": self.frontend_architecture,
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
