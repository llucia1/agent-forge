from abc import ABC, abstractmethod
from uuid import UUID

from core.models.architecture import ArchitectureArtifact
from core.models.backend_generation import WorkspaceFile


class WorkspaceError(RuntimeError):
    """Base error for project workspace operations."""


class WorkspaceBoundaryError(WorkspaceError):
    """Raised when a workspace would escape its configured root."""


class ProjectWorkspaceInitializer(ABC):
    @abstractmethod
    def initialize(self, project_id: UUID) -> None:
        """Create or resolve the workspace owned by one project."""


class ArchitectureArtifactWriter(ABC):
    @abstractmethod
    def write(
        self,
        project_id: UUID,
        artifact: ArchitectureArtifact,
    ) -> None:
        """Persist one validated architecture artifact for a project."""


class ProjectCodeWorkspace(ABC):
    @abstractmethod
    def read_architecture(
        self,
        project_id: UUID,
    ) -> ArchitectureArtifact | None:
        """Read the validated architecture artifact when it exists."""

    @abstractmethod
    def read_files(self, project_id: UUID) -> list[WorkspaceFile]:
        """Read relevant source and text files owned by one project."""

    @abstractmethod
    def write_files(
        self,
        project_id: UUID,
        files: list[WorkspaceFile],
    ) -> None:
        """Atomically write project files and create their directories."""
