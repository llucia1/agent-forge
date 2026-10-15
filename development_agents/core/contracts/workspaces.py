from abc import ABC, abstractmethod
from uuid import UUID

from core.models.architecture import ArchitectureArtifact
from core.models.specification import (
    FeatureSpecification,
    ProjectSpecification,
)
from core.models.workspace import WorkspaceFile


class WorkspaceError(RuntimeError):
    """Base error for project workspace operations."""


class WorkspaceBoundaryError(WorkspaceError):
    """Raised when a workspace would escape its configured root."""


class ProjectWorkspaceInitializer(ABC):
    @abstractmethod
    def initialize(self, project_id: UUID) -> None:
        """Create or resolve the workspace owned by one project."""


class ProjectWorkspaceRemover(ABC):
    @abstractmethod
    def remove(self, project_id: UUID) -> None:
        """Remove a project workspace during creation compensation."""


class ArchitectureArtifactWriter(ABC):
    @abstractmethod
    def write(
        self,
        project_id: UUID,
        artifact: ArchitectureArtifact,
    ) -> None:
        """Persist one validated architecture artifact for a project."""


class SpecificationReader(ABC):
    @abstractmethod
    def read_project_specification(
        self,
        project_id: UUID,
    ) -> ProjectSpecification | None:
        """Read project.md when it exists."""

    @abstractmethod
    def read_specification(
        self,
        project_id: UUID,
        spec_path: str,
    ) -> FeatureSpecification | None:
        """Read one validated feature specification when it exists."""


class SpecificationWriter(ABC):
    @abstractmethod
    def write_project_specification(
        self,
        project_id: UUID,
        specification: ProjectSpecification,
    ) -> None:
        """Persist project.md for one project."""

    @abstractmethod
    def write_specification(
        self,
        project_id: UUID,
        spec_path: str,
        specification: FeatureSpecification,
    ) -> None:
        """Persist one validated specs/*.md contract."""


class ProjectCodeReader(SpecificationReader):
    @abstractmethod
    def read_architecture(
        self,
        project_id: UUID,
    ) -> ArchitectureArtifact | None:
        """Read the validated architecture artifact when it exists."""

    @abstractmethod
    def read_files(self, project_id: UUID) -> list[WorkspaceFile]:
        """Read relevant source and text files owned by one project."""


class ProjectCodeWorkspace(ProjectCodeReader):
    @abstractmethod
    def write_files(
        self,
        project_id: UUID,
        files: list[WorkspaceFile],
    ) -> None:
        """Atomically write project files and create their directories."""
