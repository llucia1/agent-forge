from dataclasses import dataclass
from pathlib import PurePosixPath


EXCLUDED_WORKSPACE_DIRECTORIES = frozenset(
    {
        ".cache",
        ".git",
        ".gradle",
        ".mypy_cache",
        ".next",
        ".nox",
        ".nuxt",
        ".pytest_cache",
        ".ruff_cache",
        ".svelte-kit",
        ".terraform",
        ".tox",
        "__pycache__",
        "bin",
        "build",
        "cache",
        "caches",
        "coverage",
        "dist",
        "generated",
        "generated_artifacts",
        "htmlcov",
        "node_modules",
        "obj",
        "out",
        "target",
        "vendor",
    }
)

EDITABLE_TEXT_FILE_NAMES = frozenset(
    {
        ".env.example",
        ".gitignore",
        ".npmrc",
        ".nvmrc",
        ".prettierignore",
        ".prettierrc",
        "dockerfile",
        "gemfile",
        "license",
        "makefile",
        "procfile",
        "readme",
    }
)

EDITABLE_TEXT_SUFFIXES = frozenset(
    {
        ".bash",
        ".cfg",
        ".conf",
        ".cs",
        ".css",
        ".env.example",
        ".fish",
        ".go",
        ".gradle",
        ".graphql",
        ".html",
        ".ini",
        ".java",
        ".js",
        ".json",
        ".jsx",
        ".kt",
        ".kts",
        ".less",
        ".lock",
        ".md",
        ".php",
        ".properties",
        ".proto",
        ".py",
        ".pyi",
        ".rb",
        ".rs",
        ".sass",
        ".scala",
        ".scss",
        ".sh",
        ".sql",
        ".svelte",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".vue",
        ".xml",
        ".yaml",
        ".yml",
        ".zsh",
    }
)


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


def is_editable_workspace_path(relative_path: str) -> bool:
    validate_workspace_path(relative_path)
    parts = relative_path.split("/")
    directory_names = (part.casefold() for part in parts[:-1])
    if any(
        name in EXCLUDED_WORKSPACE_DIRECTORIES
        or name.endswith("_cache")
        for name in directory_names
    ):
        return False

    file_name = parts[-1].casefold()
    return file_name in EDITABLE_TEXT_FILE_NAMES or any(
        file_name.endswith(suffix) for suffix in EDITABLE_TEXT_SUFFIXES
    )


@dataclass(frozen=True)
class WorkspaceFile:
    relative_path: str
    content: str

    def __post_init__(self) -> None:
        validate_workspace_path(self.relative_path)
        if not isinstance(self.content, str):
            raise TypeError("Workspace file content must be text")
