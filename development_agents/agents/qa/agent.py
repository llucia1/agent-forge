from core.contracts.messaging import TaskHandler
from core.contracts.qa import ProjectQAExecutor
from core.contracts.repositories import ProjectReader, TaskStatusWriter
from core.contracts.results import (
    LatestTaskExecutionReader,
    TaskResultWriter,
)
from core.contracts.workspaces import ProjectCodeReader
from core.models.architecture import (
    ArchitectureArtifact,
    ArchitectureStatus,
)
from core.models.project import Project
from core.models.qa import (
    QAArtifact,
    QACheck,
    QACheckResult,
    QACheckStatus,
    QAExecutionPolicy,
    QAStatus,
    QAValidationError,
    ReviewGate,
    validate_authorized_check,
)
from core.models.review import (
    ReviewArtifact,
    ReviewStatus,
    ReviewValidationError,
    TechnicalBaseline,
)
from core.models.task import AgentRole, Task, TaskStatus
from core.models.task_result import AgentTaskExecution, TaskExecutionResult
from core.models.workspace import ProjectSnapshot


class QAProjectNotFoundError(LookupError):
    """Raised when a QA task references an unknown project."""


ARCHITECTURE_AUTHORITATIVE_FIELDS = (
    "backend_stack",
    "backend_architecture",
    "frontend_stack",
    "frontend_architecture",
    "infrastructure",
    "technical_constraints",
)


class QAAgent(TaskHandler):
    def __init__(
        self,
        task_status_writer: TaskStatusWriter,
        task_result_writer: TaskResultWriter,
        task_execution_reader: LatestTaskExecutionReader,
        project_reader: ProjectReader,
        workspace_reader: ProjectCodeReader,
        executor: ProjectQAExecutor,
        execution_policy: QAExecutionPolicy,
        authorized_checks: tuple[QACheck, ...] = (),
    ):
        self.task_status_writer = task_status_writer
        self.task_result_writer = task_result_writer
        self.task_execution_reader = task_execution_reader
        self.project_reader = project_reader
        self.workspace_reader = workspace_reader
        self.executor = executor
        self.execution_policy = execution_policy
        self.authorized_checks = authorized_checks

    def handle(self, task: Task) -> None:
        try:
            self._update_status(task, TaskStatus.IN_PROGRESS)
            project = self.project_reader.find_by_id(task.project_id)
            if project is None:
                raise QAProjectNotFoundError(
                    f"Project not found: {task.project_id}"
                )

            architecture = self.workspace_reader.read_architecture(
                task.project_id
            )
            if architecture is None:
                self._persist_needs_input(
                    task,
                    project,
                    None,
                    None,
                    ("architecture_artifact",),
                )
                return
            try:
                self._validate_architecture(project, architecture)
            except QAValidationError:
                self._persist_needs_input(
                    task,
                    project,
                    None,
                    None,
                    ("project_architecture_consistency",),
                )
                return
            if architecture.status is ArchitectureStatus.NEEDS_INPUT:
                self._persist_needs_input(
                    task,
                    project,
                    None,
                    None,
                    tuple(architecture.missing_decisions),
                )
                return

            latest_review = self.task_execution_reader.find_latest(
                task.project_id,
                AgentRole.REVIEWER,
            )
            if (
                latest_review is None
                or latest_review.task_status is not TaskStatus.COMPLETED
                or latest_review.result is None
                or not self._is_consistent_review_execution(
                    task,
                    latest_review,
                )
            ):
                self._persist_needs_input(
                    task,
                    project,
                    None,
                    None,
                    ("completed_reviewer_result",),
                )
                return

            try:
                review = ReviewArtifact.from_json(
                    latest_review.result.output
                )
            except ReviewValidationError:
                self._persist_needs_input(
                    task,
                    project,
                    None,
                    None,
                    ("valid_reviewer_result",),
                )
                return
            gate = ReviewGate(
                task_id=latest_review.task_id,
                result_id=latest_review.result.id,
                status=review.status,
            )
            if review.technical_baseline != self._baseline(project):
                self._persist_needs_input(
                    task,
                    project,
                    gate,
                    None,
                    ("reviewer_project_consistency",),
                )
                return
            if review.status is ReviewStatus.CHANGES_REQUIRED:
                self._persist_artifact(
                    task,
                    self._simple_artifact(
                        project,
                        QAStatus.BLOCKED,
                        "QA is blocked because Reviewer requires changes",
                        gate,
                        None,
                    ),
                )
                self._update_status(task, TaskStatus.COMPLETED)
                return
            if review.status is ReviewStatus.NEEDS_INPUT:
                self._persist_needs_input(
                    task,
                    project,
                    gate,
                    None,
                    ("approved_review",),
                )
                return

            workspace_files = self.workspace_reader.read_files(
                task.project_id
            )
            snapshot = ProjectSnapshot.create(architecture, workspace_files)
            reviewed_fingerprint = latest_review.result.metadata.get(
                "workspace_fingerprint"
            )
            if reviewed_fingerprint != snapshot.fingerprint:
                self._persist_needs_input(
                    task,
                    project,
                    gate,
                    snapshot.fingerprint,
                    ("reviewed_workspace_provenance",),
                )
                return
            if not self.authorized_checks:
                self._persist_needs_input(
                    task,
                    project,
                    gate,
                    snapshot.fingerprint,
                    ("authorized_qa_checks",),
                )
                return

            check_ids = [check.id for check in self.authorized_checks]
            if len(set(check_ids)) != len(check_ids):
                self._persist_needs_input(
                    task,
                    project,
                    gate,
                    snapshot.fingerprint,
                    ("authorized_qa_checks",),
                )
                return
            try:
                expected_evidence = {
                    check.id: validate_authorized_check(
                        check,
                        snapshot,
                        self.execution_policy,
                    )
                    for check in self.authorized_checks
                }
            except QAValidationError:
                self._persist_needs_input(
                    task,
                    project,
                    gate,
                    snapshot.fingerprint,
                    ("authorized_qa_checks",),
                )
                return

            execution = self.executor.execute(
                snapshot,
                self.authorized_checks,
                reviewed_fingerprint,
            )
            if not self._execution_is_trustworthy(
                execution.checks,
                expected_evidence,
            ):
                execution_checks = self._untrusted_execution_checks(
                    expected_evidence
                )
            else:
                execution_checks = execution.checks
            artifact = self._execution_artifact(
                project,
                gate,
                snapshot.fingerprint,
                execution_checks,
            )
            self._persist_artifact(task, artifact)
            self._update_status(task, TaskStatus.COMPLETED)
        except Exception:
            try:
                self._update_status(task, TaskStatus.FAILED)
            except Exception:
                pass
            raise

    def _persist_needs_input(
        self,
        task: Task,
        project: Project,
        gate: ReviewGate | None,
        fingerprint: str | None,
        missing_decisions: tuple[str, ...],
    ) -> None:
        artifact = QAArtifact(
            version=1,
            status=QAStatus.NEEDS_INPUT,
            summary="QA requires additional authorized inputs",
            technical_baseline=self._baseline(project),
            review_gate=gate,
            workspace_fingerprint=fingerprint,
            checks=(),
            missing_decisions=missing_decisions,
        )
        self._persist_artifact(task, artifact)
        self._update_status(task, TaskStatus.NEEDS_INPUT)

    def _persist_artifact(
        self,
        task: Task,
        artifact: QAArtifact,
    ) -> None:
        self.task_result_writer.write(
            TaskExecutionResult(
                task_id=task.id,
                project_id=task.project_id,
                agent=str(task.agent),
                provider="agentforge",
                model_alias=None,
                output=artifact.to_json(),
                metadata={},
            )
        )

    @classmethod
    def _simple_artifact(
        cls,
        project: Project,
        status: QAStatus,
        summary: str,
        gate: ReviewGate | None,
        fingerprint: str | None,
    ) -> QAArtifact:
        return QAArtifact(
            version=1,
            status=status,
            summary=summary,
            technical_baseline=cls._baseline(project),
            review_gate=gate,
            workspace_fingerprint=fingerprint,
            checks=(),
            missing_decisions=(),
        )

    @classmethod
    def _execution_artifact(
        cls,
        project: Project,
        gate: ReviewGate,
        fingerprint: str,
        checks: tuple[QACheckResult, ...],
    ) -> QAArtifact:
        if checks and all(
            check.status is QACheckStatus.PASSED for check in checks
        ):
            status = QAStatus.PASSED
            summary = "All authorized QA checks passed"
        elif any(
            check.status in (QACheckStatus.FAILED, QACheckStatus.ERROR)
            for check in checks
        ):
            status = QAStatus.FAILED
            summary = "One or more authorized QA checks failed"
        else:
            status = QAStatus.BLOCKED
            summary = "Authorized QA checks could not be executed"
        return QAArtifact(
            version=1,
            status=status,
            summary=summary,
            technical_baseline=cls._baseline(project),
            review_gate=gate,
            workspace_fingerprint=fingerprint,
            checks=tuple(checks),
            missing_decisions=(),
        )

    @staticmethod
    def _is_consistent_review_execution(
        task: Task,
        execution: AgentTaskExecution,
    ) -> bool:
        result = execution.result
        return (
            execution.project_id == task.project_id
            and execution.agent is AgentRole.REVIEWER
            and result is not None
            and result.task_id == execution.task_id
            and result.project_id == task.project_id
            and result.agent == str(AgentRole.REVIEWER)
        )

    def _execution_is_trustworthy(
        self,
        checks: tuple[QACheckResult, ...],
        expected_evidence: dict[str, dict[str, str]],
    ) -> bool:
        if not isinstance(checks, tuple):
            return False
        expected_ids = tuple(check.id for check in self.authorized_checks)
        if tuple(
            check.id if isinstance(check, QACheckResult) else None
            for check in checks
        ) != expected_ids:
            return False
        return all(
            check.evidence_hashes == expected_evidence[check.id]
            for check in checks
        )

    def _untrusted_execution_checks(
        self,
        expected_evidence: dict[str, dict[str, str]],
    ) -> tuple[QACheckResult, ...]:
        return tuple(
            QACheckResult(
                id=check.id,
                status=QACheckStatus.NOT_RUN,
                exit_code=None,
                duration_ms=0,
                stdout_excerpt="",
                stderr_excerpt="",
                output_truncated=False,
                failure_reason="untrusted_execution_result",
                evidence_hashes=expected_evidence[check.id],
            )
            for check in self.authorized_checks
        )

    @staticmethod
    def _baseline(project: Project) -> TechnicalBaseline:
        return TechnicalBaseline(
            backend_stack=dict(project.backend_stack),
            backend_architecture=dict(project.backend_architecture),
            frontend_stack=dict(project.frontend_stack),
            frontend_architecture=dict(project.frontend_architecture),
            infrastructure=dict(project.infrastructure),
            technical_constraints=list(project.technical_constraints),
        )

    @staticmethod
    def _validate_architecture(
        project: Project,
        architecture: ArchitectureArtifact,
    ) -> None:
        mismatches = [
            field_name
            for field_name in ARCHITECTURE_AUTHORITATIVE_FIELDS
            if getattr(project, field_name) != getattr(
                architecture,
                field_name,
            )
        ]
        if mismatches:
            raise QAValidationError(
                "Architecture contradicts Project fields: "
                + ", ".join(mismatches)
            )

    def _update_status(self, task: Task, status: TaskStatus) -> None:
        self.task_status_writer.update_status(task.id, status)
        task.status = status
