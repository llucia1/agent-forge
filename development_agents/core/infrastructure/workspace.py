import os
import tempfile
from pathlib import Path
from uuid import UUID

from core.contracts.workspaces import (
    ArchitectureArtifactWriter,
    ProjectCodeWorkspace,
    ProjectWorkspaceInitializer,
    WorkspaceBoundaryError,
    WorkspaceError,
)
from core.models.architecture import ArchitectureArtifact
from core.models.backend_generation import (
    WorkspaceFile,
    validate_workspace_path,
)


EXCLUDED_DIRECTORIES = frozenset(
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

TEXT_FILE_NAMES = frozenset(
    {
        ".env.example",
        "dockerfile",
        "gemfile",
        "license",
        "makefile",
        "procfile",
        "readme",
    }
)

TEXT_SUFFIXES = frozenset(
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


class FilesystemProjectWorkspace(
    ProjectWorkspaceInitializer,
    ArchitectureArtifactWriter,
    ProjectCodeWorkspace,
):
    def __init__(self, root: Path):
        if not root.is_absolute():
            raise WorkspaceBoundaryError(
                "Project workspace root must be an absolute path"
            )
        if root.is_symlink():
            raise WorkspaceBoundaryError(
                "Project workspace root must not be a symbolic link"
            )

        try:
            resolved_root = root.resolve(strict=True)
        except OSError as error:
            raise WorkspaceError(
                f"Project workspace root is unavailable: {root}"
            ) from error

        if not resolved_root.is_dir():
            raise WorkspaceError(
                f"Project workspace root is not a directory: {root}"
            )

        self.root = resolved_root

    def initialize(self, project_id: UUID) -> None:
        self._resolve(project_id)

    def write(
        self,
        project_id: UUID,
        artifact: ArchitectureArtifact,
    ) -> None:
        workspace = self._resolve(project_id)
        target = workspace / "architecture.json"
        self._atomic_write(
            target,
            artifact.to_json(),
            f"architecture artifact for {project_id}",
        )

    def read_architecture(
        self,
        project_id: UUID,
    ) -> ArchitectureArtifact | None:
        workspace = self._resolve(project_id)
        architecture_path = workspace / "architecture.json"
        if not architecture_path.exists():
            return None
        if architecture_path.is_symlink():
            raise WorkspaceBoundaryError(
                f"Architecture artifact must not be a symbolic link: "
                f"{project_id}"
            )

        resolved = self._resolve_existing_file(
            workspace,
            architecture_path,
        )
        try:
            content = resolved.read_text(encoding="utf-8")
        except OSError as error:
            raise WorkspaceError(
                f"Could not read architecture artifact for {project_id}"
            ) from error

        return ArchitectureArtifact.from_json(content)

    def read_files(self, project_id: UUID) -> list[WorkspaceFile]:
        workspace = self._resolve(project_id)
        files = []

        try:
            for current_root, directory_names, file_names in os.walk(
                workspace,
                topdown=True,
                followlinks=False,
            ):
                current = Path(current_root)
                directory_names[:] = sorted(
                    directory_name
                    for directory_name in directory_names
                    if self._include_directory(
                        current / directory_name,
                        directory_name,
                    )
                )

                for file_name in sorted(file_names):
                    candidate = current / file_name
                    workspace_file = self._read_relevant_file(
                        workspace,
                        candidate,
                    )
                    if workspace_file is not None:
                        files.append(workspace_file)
        except OSError as error:
            raise WorkspaceError(
                f"Could not read project files for {project_id}"
            ) from error

        return sorted(
            files,
            key=lambda workspace_file: workspace_file.relative_path,
        )

    def write_files(
        self,
        project_id: UUID,
        files: list[WorkspaceFile],
    ) -> None:
        workspace = self._resolve(project_id)
        targets = []
        seen_paths = set()

        for workspace_file in files:
            validate_workspace_path(workspace_file.relative_path)
            if workspace_file.relative_path == "architecture.json":
                raise WorkspaceBoundaryError(
                    "Backend files cannot overwrite architecture.json"
                )
            if workspace_file.relative_path in seen_paths:
                raise WorkspaceBoundaryError(
                    "Backend files contain duplicate paths"
                )
            seen_paths.add(workspace_file.relative_path)
            target = workspace.joinpath(
                *workspace_file.relative_path.split("/")
            )
            self._validate_target(workspace, target)
            targets.append((workspace_file, target))

        for workspace_file, target in targets:
            safe_target = self._create_parent_directories(
                workspace,
                workspace_file.relative_path,
            )
            self._atomic_write(
                safe_target,
                workspace_file.content,
                f"backend file {workspace_file.relative_path}",
            )

    @staticmethod
    def _include_directory(path: Path, name: str) -> bool:
        normalized_name = name.casefold()
        return (
            normalized_name not in EXCLUDED_DIRECTORIES
            and not normalized_name.endswith("_cache")
            and not path.is_symlink()
        )

    def _read_relevant_file(
        self,
        workspace: Path,
        candidate: Path,
    ) -> WorkspaceFile | None:
        if candidate.name == "architecture.json" or candidate.is_symlink():
            return None
        if not self._is_text_file(candidate):
            return None

        resolved = self._resolve_existing_file(workspace, candidate)
        content_bytes = resolved.read_bytes()
        if b"\x00" in content_bytes:
            return None
        try:
            content = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return None

        return WorkspaceFile(
            relative_path=resolved.relative_to(workspace).as_posix(),
            content=content,
        )

    @staticmethod
    def _is_text_file(path: Path) -> bool:
        normalized_name = path.name.casefold()
        if normalized_name in TEXT_FILE_NAMES:
            return True
        return any(
            normalized_name.endswith(suffix)
            for suffix in TEXT_SUFFIXES
        )

    @staticmethod
    def _resolve_existing_file(workspace: Path, candidate: Path) -> Path:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as error:
            raise WorkspaceError(
                f"Could not resolve workspace file: {candidate}"
            ) from error
        if not resolved.is_relative_to(workspace) or not resolved.is_file():
            raise WorkspaceBoundaryError(
                f"Workspace file escapes its project: {candidate}"
            )
        return resolved

    @staticmethod
    def _validate_target(workspace: Path, target: Path) -> None:
        current = workspace
        relative_parts = target.relative_to(workspace).parts
        for part in relative_parts[:-1]:
            current = current / part
            if current.is_symlink():
                raise WorkspaceBoundaryError(
                    f"Workspace target uses a symbolic link: {target}"
                )
            if current.exists():
                resolved = current.resolve(strict=True)
                if (
                    not resolved.is_relative_to(workspace)
                    or not resolved.is_dir()
                ):
                    raise WorkspaceBoundaryError(
                        f"Workspace target escapes its project: {target}"
                    )

        if target.is_symlink():
            raise WorkspaceBoundaryError(
                f"Workspace target must not be a symbolic link: {target}"
            )
        if target.exists() and not target.is_file():
            raise WorkspaceBoundaryError(
                f"Workspace target is not a file: {target}"
            )

    @staticmethod
    def _create_parent_directories(
        workspace: Path,
        relative_path: str,
    ) -> Path:
        parts = relative_path.split("/")
        current = workspace
        for part in parts[:-1]:
            candidate = current / part
            try:
                candidate.mkdir(mode=0o775, parents=False, exist_ok=True)
            except OSError as error:
                raise WorkspaceError(
                    f"Could not create workspace directory: {candidate}"
                ) from error
            if candidate.is_symlink():
                raise WorkspaceBoundaryError(
                    f"Workspace directory is a symbolic link: {candidate}"
                )
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(workspace) or not resolved.is_dir():
                raise WorkspaceBoundaryError(
                    f"Workspace directory escapes its project: {candidate}"
                )
            resolved.chmod(0o775)
            current = resolved

        return current / parts[-1]

    @staticmethod
    def _atomic_write(target: Path, content: str, label: str) -> None:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                os.fchmod(temporary_file.fileno(), 0o664)
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())

            os.replace(temporary_path, target)
        except OSError as error:
            raise WorkspaceError(f"Could not write {label}") from error
        finally:
            if temporary_path is not None and temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    pass

    def _resolve(self, project_id: UUID) -> Path:
        if not isinstance(project_id, UUID):
            raise WorkspaceBoundaryError(
                "Project workspace identifier must be a UUID"
            )
        candidate = self.root / str(project_id)

        try:
            candidate.mkdir(mode=0o775, parents=False, exist_ok=True)
        except OSError as error:
            raise WorkspaceError(
                f"Could not initialize project workspace for {project_id}"
            ) from error

        if candidate.is_symlink():
            raise WorkspaceBoundaryError(
                f"Project workspace must not be a symbolic link: {project_id}"
            )

        try:
            resolved = candidate.resolve(strict=True)
        except OSError as error:
            raise WorkspaceError(
                f"Could not resolve project workspace for {project_id}"
            ) from error

        if resolved.parent != self.root or not resolved.is_dir():
            raise WorkspaceBoundaryError(
                f"Project workspace escapes its configured root: {project_id}"
            )

        try:
            resolved.chmod(0o775)
        except OSError as error:
            raise WorkspaceError(
                f"Could not set project workspace permissions for {project_id}"
            ) from error

        return resolved
