from abc import ABC, abstractmethod

from core.models.qa import QACheck, QAExecutionResult
from core.models.workspace import ProjectSnapshot


class ProjectQAExecutor(ABC):
    @abstractmethod
    def execute(
        self,
        snapshot: ProjectSnapshot,
        checks: tuple[QACheck, ...],
        expected_fingerprint: str,
    ) -> QAExecutionResult:
        """Execute authorized checks against an isolated snapshot."""
