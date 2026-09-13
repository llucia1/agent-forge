from typing import Any

from core.contracts.context import ContextProvider
from core.contracts.engines import AgentEngine, EngineResult
from core.contracts.messaging import TaskHandler
from core.contracts.repositories import ProjectReader, TaskStatusWriter
from core.contracts.results import TaskResultWriter
from core.contracts.workspaces import ProjectCodeReader
from core.models.architecture import ArchitectureArtifact, ArchitectureStatus
from core.models.project import Project
from core.models.review import (
    ReviewArtifact,
    ReviewStatus,
    ReviewValidationError,
    TechnicalBaseline,
)
from core.models.task import Task, TaskStatus
from core.models.task_result import TaskExecutionResult
from core.models.workspace import WorkspaceFile


class ReviewProjectNotFoundError(LookupError):
    """Raised when a review task references an unknown project."""


ARCHITECTURE_AUTHORITATIVE_FIELDS = (
    "backend_stack",
    "backend_architecture",
    "frontend_stack",
    "frontend_architecture",
    "infrastructure",
    "technical_constraints",
)

REQUIRED_TECHNICAL_FIELDS = (
    "backend_stack",
    "backend_architecture",
    "frontend_stack",
    "frontend_architecture",
    "infrastructure",
)


class ReviewerAgent(TaskHandler):
    def __init__(
        self,
        task_status_writer: TaskStatusWriter,
        task_result_writer: TaskResultWriter,
        project_reader: ProjectReader,
        workspace_reader: ProjectCodeReader,
        engine: AgentEngine,
        context_provider: ContextProvider,
    ):
        self.task_status_writer = task_status_writer
        self.task_result_writer = task_result_writer
        self.project_reader = project_reader
        self.workspace_reader = workspace_reader
        self.engine = engine
        self.context_provider = context_provider

    def handle(self, task: Task) -> None:
        try:
            self._update_status(task, TaskStatus.IN_PROGRESS)
            project = self.project_reader.find_by_id(task.project_id)
            if project is None:
                raise ReviewProjectNotFoundError(
                    f"Project not found: {task.project_id}"
                )

            architecture = self.workspace_reader.read_architecture(
                task.project_id
            )
            if architecture is None:
                self._persist_needs_input(
                    task,
                    project,
                    ["architecture_artifact"],
                )
                return

            self._validate_architecture_authority(project, architecture)
            if architecture.status is ArchitectureStatus.NEEDS_INPUT:
                self._persist_needs_input(
                    task,
                    project,
                    list(architecture.missing_decisions),
                )
                return

            missing_decisions = [
                field_name
                for field_name in REQUIRED_TECHNICAL_FIELDS
                if not getattr(project, field_name)
            ]
            if missing_decisions:
                self._persist_needs_input(
                    task,
                    project,
                    missing_decisions,
                )
                return

            workspace_files = self.workspace_reader.read_files(
                task.project_id
            )
            backend_files, frontend_files, shared_files = (
                self._classify_files(workspace_files)
            )
            missing_implementations = []
            if not backend_files:
                missing_implementations.append("backend_implementation")
            if not frontend_files:
                missing_implementations.append("frontend_implementation")
            if missing_implementations:
                self._persist_needs_input(
                    task,
                    project,
                    missing_implementations,
                )
                return

            rule_catalog = self._rule_catalog(
                project,
                architecture,
                backend_files,
                frontend_files,
            )
            engine_result = self.process(
                task,
                project,
                architecture,
                backend_files,
                frontend_files,
                shared_files,
                rule_catalog,
            )
            review = ReviewArtifact.from_json(engine_result.output)
            self._validate_review_authority(
                project,
                review,
                rule_catalog,
                workspace_files,
            )
            self._persist_result(task, engine_result, review)

            if review.status is ReviewStatus.NEEDS_INPUT:
                self._update_status(task, TaskStatus.NEEDS_INPUT)
                return

            self._update_status(task, TaskStatus.COMPLETED)
        except Exception:
            try:
                self._update_status(task, TaskStatus.FAILED)
            except Exception:
                pass
            raise

    def process(
        self,
        task: Task,
        project: Project,
        architecture: ArchitectureArtifact,
        backend_files: list[WorkspaceFile],
        frontend_files: list[WorkspaceFile],
        shared_files: list[WorkspaceFile],
        rule_catalog: list[dict[str, Any]],
    ) -> EngineResult:
        context = {
            **self.context_provider.build(task, project),
            "output_contract": self._output_contract(
                project,
                rule_catalog,
            ),
            "review": {
                "rule_catalog": rule_catalog,
                "architecture_artifact": architecture.to_dict(),
                "backend_files": self._serialize_files(backend_files),
                "frontend_files": self._serialize_files(frontend_files),
                "shared_files": self._serialize_files(shared_files),
            },
        }
        return self.engine.run(
            task=task,
            project=project,
            context=context,
        )

    def _persist_needs_input(
        self,
        task: Task,
        project: Project,
        missing_decisions: list[str],
    ) -> None:
        review = ReviewArtifact(
            version=1,
            status=ReviewStatus.NEEDS_INPUT,
            summary="Project review requires additional inputs",
            technical_baseline=self._technical_baseline(project),
            findings=[],
            checked_rules=[],
            missing_decisions=missing_decisions,
        )
        result = EngineResult(
            output=review.to_json(),
            metadata={"missing_decisions": missing_decisions},
            provider="agentforge",
            model_alias=None,
        )
        self._persist_result(task, result, review)
        self._update_status(task, TaskStatus.NEEDS_INPUT)

    def _persist_result(
        self,
        task: Task,
        engine_result: EngineResult,
        review: ReviewArtifact,
    ) -> None:
        self.task_result_writer.write(
            TaskExecutionResult(
                task_id=task.id,
                project_id=task.project_id,
                agent=str(task.agent),
                provider=engine_result.provider,
                model_alias=engine_result.model_alias,
                output=review.to_json(),
                metadata=dict(engine_result.metadata),
            )
        )

    @staticmethod
    def _technical_baseline(project: Project) -> TechnicalBaseline:
        return TechnicalBaseline(
            backend_stack=dict(project.backend_stack),
            backend_architecture=dict(project.backend_architecture),
            frontend_stack=dict(project.frontend_stack),
            frontend_architecture=dict(project.frontend_architecture),
            infrastructure=dict(project.infrastructure),
            technical_constraints=list(project.technical_constraints),
        )

    @classmethod
    def _output_contract(
        cls,
        project: Project,
        rule_catalog: list[dict[str, Any]],
    ) -> dict[str, Any]:
        contract = ReviewArtifact.output_contract()
        technical_baseline = cls._technical_baseline(project).to_dict()
        checked_rules = [rule["id"] for rule in rule_catalog]

        baseline_contract = dict(
            contract["properties"]["technical_baseline"]
        )
        baseline_contract["const"] = technical_baseline
        contract["properties"]["technical_baseline"] = baseline_contract

        checked_rules_contract = dict(
            contract["properties"]["checked_rules"]
        )
        checked_rules_contract["const"] = checked_rules
        contract["properties"]["checked_rules"] = (
            checked_rules_contract
        )
        contract["instruction"] = (
            f"{contract['instruction']} Check every catalog entry exactly "
            "once and copy its ids to checked_rules in the supplied order. "
            "Apply an architecture pattern or technical constraint only "
            "when its supplied value requires it. Verify actual structure, "
            "responsibilities, dependencies, interfaces, adapters and "
            "integration against those values. Critical and high findings "
            "require changes_required."
        )
        return contract

    @classmethod
    def _rule_catalog(
        cls,
        project: Project,
        architecture: ArchitectureArtifact,
        backend_files: list[WorkspaceFile],
        frontend_files: list[WorkspaceFile],
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": "project.backend_stack",
                "value": project.backend_stack,
            },
            {
                "id": "project.backend_architecture",
                "value": project.backend_architecture,
            },
            {
                "id": "project.frontend_stack",
                "value": project.frontend_stack,
            },
            {
                "id": "project.frontend_architecture",
                "value": project.frontend_architecture,
            },
            {
                "id": "project.infrastructure",
                "value": project.infrastructure,
            },
            {
                "id": "project.technical_constraints",
                "value": project.technical_constraints,
            },
            {
                "id": "architecture.modules",
                "value": architecture.modules,
            },
            {
                "id": "architecture.interfaces",
                "value": architecture.interfaces,
            },
            {
                "id": "architecture.apis",
                "value": architecture.apis,
            },
            {
                "id": "architecture.persistence",
                "value": architecture.persistence,
            },
            {
                "id": "architecture.execution_plan",
                "value": architecture.execution_plan,
            },
            {
                "id": "implementation.backend",
                "value": [
                    workspace_file.relative_path
                    for workspace_file in backend_files
                ],
            },
            {
                "id": "implementation.frontend",
                "value": [
                    workspace_file.relative_path
                    for workspace_file in frontend_files
                ],
            },
            {
                "id": "integration.backend_frontend",
                "value": {
                    "apis": architecture.apis,
                    "interfaces": architecture.interfaces,
                },
            },
        ]

    @staticmethod
    def _classify_files(
        workspace_files: list[WorkspaceFile],
    ) -> tuple[
        list[WorkspaceFile],
        list[WorkspaceFile],
        list[WorkspaceFile],
    ]:
        backend_files = []
        frontend_files = []
        shared_files = []
        for workspace_file in workspace_files:
            if workspace_file.relative_path.startswith("backend/"):
                backend_files.append(workspace_file)
            elif workspace_file.relative_path.startswith("frontend/"):
                frontend_files.append(workspace_file)
            else:
                shared_files.append(workspace_file)
        return backend_files, frontend_files, shared_files

    @staticmethod
    def _serialize_files(
        workspace_files: list[WorkspaceFile],
    ) -> list[dict[str, str]]:
        return [
            {
                "path": workspace_file.relative_path,
                "content": workspace_file.content,
            }
            for workspace_file in workspace_files
        ]

    @staticmethod
    def _validate_architecture_authority(
        project: Project,
        architecture: ArchitectureArtifact,
    ) -> None:
        mismatches = [
            field_name
            for field_name in ARCHITECTURE_AUTHORITATIVE_FIELDS
            if getattr(architecture, field_name) != getattr(
                project,
                field_name,
            )
        ]
        if mismatches:
            raise ReviewValidationError(
                "Architecture artifact contradicts Project fields: "
                + ", ".join(mismatches)
            )

    @classmethod
    def _validate_review_authority(
        cls,
        project: Project,
        review: ReviewArtifact,
        rule_catalog: list[dict[str, Any]],
        workspace_files: list[WorkspaceFile],
    ) -> None:
        if review.technical_baseline != cls._technical_baseline(project):
            raise ReviewValidationError(
                "Review technical_baseline contradicts Project"
            )

        expected_rules = [rule["id"] for rule in rule_catalog]
        if review.checked_rules != expected_rules:
            raise ReviewValidationError(
                "Review checked_rules does not match the rule catalog"
            )

        expected_rule_set = set(expected_rules)
        invalid_rules = sorted(
            {
                finding.rule
                for finding in review.findings
                if finding.rule not in expected_rule_set
            }
        )
        if invalid_rules:
            raise ReviewValidationError(
                "Review findings reference unknown rules: "
                + ", ".join(invalid_rules)
            )

        available_paths = {
            workspace_file.relative_path
            for workspace_file in workspace_files
        }
        invalid_paths = sorted(
            {
                finding.path
                for finding in review.findings
                if finding.path is not None
                and finding.path not in available_paths
            }
        )
        if invalid_paths:
            raise ReviewValidationError(
                "Review findings reference unread workspace paths: "
                + ", ".join(invalid_paths)
            )

    def _update_status(self, task: Task, status: TaskStatus) -> None:
        self.task_status_writer.update_status(task.id, status)
        task.status = status
