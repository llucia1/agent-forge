import json
from typing import Any

from core.contracts.context import ContextProvider
from core.contracts.engines import AgentEngine, EngineResult
from core.contracts.messaging import TaskHandler
from core.contracts.repositories import ProjectReader, TaskStatusWriter
from core.contracts.results import TaskResultWriter
from core.contracts.workspaces import ProjectCodeWorkspace
from core.models.architecture import ArchitectureArtifact, ArchitectureStatus
from core.models.backend_generation import (
    BackendGenerationArtifact,
    BackendGenerationStatus,
    BackendGenerationValidationError,
    WorkspaceFile,
)
from core.models.project import Project
from core.models.task import Task, TaskStatus
from core.models.task_result import TaskExecutionResult


class BackendProjectNotFoundError(LookupError):
    """Raised when a backend task references an unknown project."""


ARCHITECTURE_AUTHORITATIVE_FIELDS = (
    "backend_stack",
    "backend_architecture",
    "frontend_stack",
    "frontend_architecture",
    "infrastructure",
    "technical_constraints",
)

BACKEND_AUTHORITATIVE_FIELDS = (
    "backend_stack",
    "backend_architecture",
    "infrastructure",
    "technical_constraints",
)

REQUIRED_BACKEND_FIELDS = (
    "backend_stack",
    "backend_architecture",
    "infrastructure",
)


class BackendAgent(TaskHandler):
    def __init__(
        self,
        task_status_writer: TaskStatusWriter,
        task_result_writer: TaskResultWriter,
        project_reader: ProjectReader,
        workspace: ProjectCodeWorkspace,
        engine: AgentEngine,
        context_provider: ContextProvider,
    ):
        self.task_status_writer = task_status_writer
        self.task_result_writer = task_result_writer
        self.project_reader = project_reader
        self.workspace = workspace
        self.engine = engine
        self.context_provider = context_provider

    def handle(self, task: Task) -> None:
        self._update_status(task, TaskStatus.IN_PROGRESS)

        try:
            project = self.project_reader.find_by_id(task.project_id)
            if project is None:
                raise BackendProjectNotFoundError(
                    f"Project not found: {task.project_id}"
                )

            architecture = self.workspace.read_architecture(task.project_id)
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
                for field_name in REQUIRED_BACKEND_FIELDS
                if not getattr(project, field_name)
            ]
            if missing_decisions:
                self._persist_needs_input(
                    task,
                    project,
                    missing_decisions,
                )
                return

            existing_files = self.workspace.read_files(task.project_id)
            engine_result = self.process(
                task,
                project,
                architecture,
                existing_files,
            )
            generation = BackendGenerationArtifact.from_json(
                engine_result.output
            )
            self._validate_backend_authority(project, generation)
            self._persist_result(task, engine_result, generation)

            if generation.status is BackendGenerationStatus.NEEDS_INPUT:
                self._update_status(task, TaskStatus.NEEDS_INPUT)
                return

            self.workspace.write_files(task.project_id, generation.files)
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
        existing_files: list[WorkspaceFile],
    ) -> EngineResult:
        context = {
            **self.context_provider.build(task, project),
            "output_contract": self._output_contract(project),
            "backend_implementation": self._implementation_context(
                project,
                architecture,
                existing_files,
            ),
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
        generation = BackendGenerationArtifact(
            version=1,
            status=BackendGenerationStatus.NEEDS_INPUT,
            missing_decisions=missing_decisions,
            backend_stack=dict(project.backend_stack),
            backend_architecture=dict(project.backend_architecture),
            infrastructure=dict(project.infrastructure),
            technical_constraints=list(project.technical_constraints),
            files=[],
            summary="Backend generation requires architecture decisions",
        )
        result = EngineResult(
            output=generation.to_json(),
            metadata={"missing_decisions": missing_decisions},
            provider="agentforge",
            model_alias=None,
        )
        self._persist_result(task, result, generation)
        self._update_status(task, TaskStatus.NEEDS_INPUT)

    def _persist_result(
        self,
        task: Task,
        engine_result: EngineResult,
        generation: BackendGenerationArtifact,
    ) -> None:
        self.task_result_writer.write(
            TaskExecutionResult(
                task_id=task.id,
                project_id=task.project_id,
                agent=str(task.agent),
                provider=engine_result.provider,
                model_alias=engine_result.model_alias,
                output=generation.to_json(),
                metadata=dict(engine_result.metadata),
            )
        )

    @staticmethod
    def _authoritative_backend(project: Project) -> dict[str, Any]:
        return {
            field_name: getattr(project, field_name)
            for field_name in BACKEND_AUTHORITATIVE_FIELDS
        }

    @classmethod
    def _output_contract(cls, project: Project) -> dict[str, Any]:
        contract = BackendGenerationArtifact.output_contract()
        authoritative = cls._authoritative_backend(project)
        properties = contract["properties"]
        for field_name, value in authoritative.items():
            field_contract = dict(properties[field_name])
            field_contract["const"] = value
            properties[field_name] = field_contract

        contract["instruction"] = (
            f"{contract['instruction']} The Project and validated "
            "architecture artifact are authoritative. Copy all technical "
            "fields exactly. Use only their languages, frameworks, databases, "
            "infrastructure and architecture rules. Implement those rules in "
            "real file structure, responsibilities and dependency direction, "
            "not only in comments. If a required decision is absent, return "
            "needs_input with no files."
        )
        return contract

    @classmethod
    def _implementation_context(
        cls,
        project: Project,
        architecture: ArchitectureArtifact,
        existing_files: list[WorkspaceFile],
    ) -> dict[str, Any]:
        mandatory_rules = [
            {
                "source": "backend_stack",
                "requirement": json.dumps(
                    project.backend_stack,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            },
            {
                "source": "backend_architecture",
                "requirement": json.dumps(
                    project.backend_architecture,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            },
            {
                "source": "infrastructure",
                "requirement": json.dumps(
                    project.infrastructure,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            },
        ]
        mandatory_rules.extend(
            {
                "source": f"technical_constraints[{index}]",
                "requirement": constraint,
            }
            for index, constraint in enumerate(
                project.technical_constraints
            )
        )
        return {
            "mandatory_rules": mandatory_rules,
            "architecture_artifact": architecture.to_dict(),
            "existing_files": [
                {
                    "path": workspace_file.relative_path,
                    "content": workspace_file.content,
                }
                for workspace_file in existing_files
            ],
        }

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
            raise BackendGenerationValidationError(
                "Architecture artifact contradicts Project fields: "
                + ", ".join(mismatches)
            )

    @classmethod
    def _validate_backend_authority(
        cls,
        project: Project,
        generation: BackendGenerationArtifact,
    ) -> None:
        mismatches = [
            field_name
            for field_name, expected in cls._authoritative_backend(
                project
            ).items()
            if getattr(generation, field_name) != expected
        ]
        if mismatches:
            raise BackendGenerationValidationError(
                "Backend output contradicts authoritative Project fields: "
                + ", ".join(mismatches)
            )

    def _update_status(self, task: Task, status: TaskStatus) -> None:
        self.task_status_writer.update_status(task.id, status)
        task.status = status
