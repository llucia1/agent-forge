import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from core.models.workspace import (
    WorkspaceFile,
    WorkspacePathValidationError,
)


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
    implementation: dict[str, Any]
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

        implementation = _validate_implementation_manifest(
            payload["implementation"],
            set(paths),
        )

        if status is BackendGenerationStatus.COMPLETE:
            if not implementation["modules"]:
                raise BackendGenerationValidationError(
                    "A complete backend output must implement modules"
                )
        elif (
            not missing_decisions
            or files
            or any(implementation.values())
        ):
            raise BackendGenerationValidationError(
                "A needs_input backend output requires missing decisions "
                "and cannot contain files or implementation coverage"
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
            implementation=implementation,
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
            "implementation",
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
                "implementation": {
                    "type": "object",
                    "required": [
                        "modules",
                        "interfaces",
                        "apis",
                        "persistence_stores",
                        "dependencies",
                        "file_modules",
                    ],
                    "properties": {
                        field_name: {
                            "type": "array",
                            "items": {"type": "string"},
                            "uniqueItems": True,
                        }
                        for field_name in (
                            "modules",
                            "interfaces",
                            "apis",
                            "persistence_stores",
                        )
                    }
                    | {
                        "dependencies": {
                            "type": "array",
                            "items": {"type": "string"},
                            "uniqueItems": True,
                        },
                        "file_modules": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                        },
                    },
                    "additionalProperties": False,
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
            "implementation": self.implementation,
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


def _validate_implementation_manifest(
    raw_implementation: Any,
    file_paths: set[str],
) -> dict[str, Any]:
    coverage_fields = (
        "modules",
        "interfaces",
        "apis",
        "persistence_stores",
    )
    required = {*coverage_fields, "dependencies", "file_modules"}
    if (
        not isinstance(raw_implementation, dict)
        or set(raw_implementation) != required
    ):
        raise BackendGenerationValidationError(
            "Backend implementation manifest has invalid fields"
        )

    implementation = {}
    for field_name in coverage_fields:
        entries = raw_implementation[field_name]
        if (
            not isinstance(entries, list)
            or not all(
                isinstance(entry, str) and entry.strip()
                for entry in entries
            )
            or len(set(entries)) != len(entries)
        ):
            raise BackendGenerationValidationError(
                f"Backend implementation {field_name} must contain unique "
                "non-empty names"
            )
        implementation[field_name] = list(entries)

    dependencies = raw_implementation["dependencies"]
    if (
        not isinstance(dependencies, list)
        or not all(
            isinstance(dependency, str) and dependency.strip()
            for dependency in dependencies
        )
        or len(set(dependencies)) != len(dependencies)
    ):
        raise BackendGenerationValidationError(
            "Backend implementation dependencies must be unique non-empty "
            "strings"
        )
    implementation["dependencies"] = list(dependencies)

    file_modules = raw_implementation["file_modules"]
    if not isinstance(file_modules, dict):
        raise BackendGenerationValidationError(
            "Backend implementation file_modules must be an object"
        )
    if not file_modules and len(implementation["modules"]) == 1:
        module = implementation["modules"][0]
        file_modules = {
            path: module
            for path in sorted(file_paths)
        }
    missing_paths = sorted(file_paths.difference(file_modules))
    unknown_paths = sorted(set(file_modules).difference(file_paths))
    invalid_modules = sorted(
        path
        for path, module in file_modules.items()
        if not isinstance(module, str)
        or module not in implementation["modules"]
    )
    if missing_paths or unknown_paths or invalid_modules:
        raise BackendGenerationValidationError(
            "Backend implementation file_modules must map every generated "
            "file to an implemented module; "
            f"missing_paths={missing_paths}, unknown_paths={unknown_paths}, "
            f"invalid_modules={invalid_modules}"
        )
    implementation["file_modules"] = dict(file_modules)
    return implementation
