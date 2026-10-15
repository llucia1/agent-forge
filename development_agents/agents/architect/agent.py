from typing import Any

from core.contracts.context import ContextProvider
from core.contracts.engines import AgentEngine, EngineResult
from core.contracts.messaging import TaskHandler
from core.contracts.repositories import ProjectReader, TaskStatusWriter
from core.contracts.results import TaskResultWriter
from core.contracts.workspaces import (
    ArchitectureArtifactWriter,
    SpecificationReader,
)
from core.models.architecture import (
    ArchitectureArtifact,
    ArchitectureArtifactValidationError,
    ArchitectureStatus,
)
from core.models.project import Project
from core.models.task import Task, TaskStatus
from core.models.task_result import TaskExecutionResult


class ProjectNotFoundError(LookupError):
    """Raised when a task references a project that does not exist."""


ESSENTIAL_TECHNICAL_FIELDS = (
    "backend_stack",
    "backend_architecture",
    "frontend_stack",
    "frontend_architecture",
    "infrastructure",
)

AUTHORITATIVE_TECHNICAL_FIELDS = (
    *ESSENTIAL_TECHNICAL_FIELDS,
    "technical_constraints",
)

PERSISTENCE_DECISION_KEYS = frozenset(
    {
        "database",
        "databases",
        "datastore",
        "datastores",
        "persistence",
        "storage",
    }
)
MAX_ARCHITECTURE_ATTEMPTS = 3


class ArchitectAgent(TaskHandler):
    def __init__(
        self,
        task_status_writer: TaskStatusWriter,
        task_result_writer: TaskResultWriter,
        architecture_artifact_writer: ArchitectureArtifactWriter,
        project_reader: ProjectReader,
        engine: AgentEngine,
        context_provider: ContextProvider,
        specification_reader: SpecificationReader | None = None,
    ):
        self.task_status_writer = task_status_writer
        self.task_result_writer = task_result_writer
        self.architecture_artifact_writer = architecture_artifact_writer
        self.project_reader = project_reader
        self.engine = engine
        self.context_provider = context_provider
        self.specification_reader = specification_reader

    def handle(self, task: Task) -> None:
        self._update_status(task, TaskStatus.IN_PROGRESS)

        try:
            project = self.project_reader.find_by_id(task.project_id)

            if project is None:
                raise ProjectNotFoundError(
                    f"Project not found: {task.project_id}"
                )

            functional_contract, functional_missing = (
                self._functional_contract(task)
            )
            missing_decisions = [
                *functional_missing,
                *self._missing_decisions(project),
            ]
            if missing_decisions:
                artifact = self._needs_input_artifact(
                    project,
                    missing_decisions,
                )
                result = EngineResult(
                    output=artifact.to_json(),
                    metadata={"missing_decisions": missing_decisions},
                    provider="agentforge",
                    model_alias=None,
                )
                self._persist(task, result, artifact)
                self._update_status(task, TaskStatus.NEEDS_INPUT)
                return

            validation_feedback = None
            for attempt in range(1, MAX_ARCHITECTURE_ATTEMPTS + 1):
                engine_result = self.process(
                    task,
                    project,
                    functional_contract,
                    validation_feedback,
                )
                try:
                    artifact = ArchitectureArtifact.from_json(
                        engine_result.output
                    )
                    self._validate_project_authority(project, artifact)
                    break
                except ArchitectureArtifactValidationError as error:
                    if attempt == MAX_ARCHITECTURE_ATTEMPTS:
                        raise
                    validation_feedback = {
                        "attempt": attempt,
                        "validation_error": str(error),
                        "previous_output": engine_result.output,
                        "instruction": (
                            "Return the complete corrected architecture. "
                            "Preserve Project and functional specification "
                            "authority exactly and change only what is needed "
                            "to satisfy the validation error."
                        ),
                    }
            self._persist(task, engine_result, artifact)
            final_status = (
                TaskStatus.NEEDS_INPUT
                if artifact.status is ArchitectureStatus.NEEDS_INPUT
                else TaskStatus.COMPLETED
            )
            self._update_status(task, final_status)
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
        functional_contract: dict[str, Any] | None = None,
        validation_feedback: dict[str, Any] | None = None,
    ) -> EngineResult:
        context = {
            **self.context_provider.build(task, project),
            "output_contract": self._output_contract(project),
        }
        if functional_contract is not None:
            context["functional_contract"] = functional_contract
        if validation_feedback is not None:
            context["architecture_validation_feedback"] = (
                validation_feedback
            )
        return self.engine.run(
            task=task,
            project=project,
            context=context,
        )

    def _functional_contract(
        self,
        task: Task,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        if task.spec_path is None:
            return None, []
        if self.specification_reader is None:
            return None, ["specification_reader"]
        project_spec = self.specification_reader.read_project_specification(
            task.project_id
        )
        feature_spec = self.specification_reader.read_specification(
            task.project_id,
            task.spec_path,
        )
        missing = []
        if project_spec is None:
            missing.append("project_specification")
        if feature_spec is None:
            missing.append("feature_specification")
        if missing:
            return None, missing
        return {
            "project_markdown": project_spec.markdown,
            "spec_path": task.spec_path,
            "spec_markdown": feature_spec.markdown,
            "instruction": (
                "Use the functional specifications to decide what must be "
                "supported; architecture.json remains authoritative for how."
            ),
        }, []

    def _persist(
        self,
        task: Task,
        engine_result: EngineResult,
        artifact: ArchitectureArtifact,
    ) -> None:
        self.task_result_writer.write(
            TaskExecutionResult(
                task_id=task.id,
                project_id=task.project_id,
                agent=str(task.agent),
                provider=engine_result.provider,
                model_alias=engine_result.model_alias,
                output=artifact.to_json(),
                metadata=dict(engine_result.metadata),
            )
        )
        self.architecture_artifact_writer.write(
            task.project_id,
            artifact,
        )

    @staticmethod
    def _authoritative_definition(project: Project) -> dict[str, Any]:
        return {
            field_name: getattr(project, field_name)
            for field_name in AUTHORITATIVE_TECHNICAL_FIELDS
        }

    @staticmethod
    def _missing_decisions(project: Project) -> list[str]:
        return [
            field_name
            for field_name in ESSENTIAL_TECHNICAL_FIELDS
            if not getattr(project, field_name)
        ]

    @classmethod
    def _needs_input_artifact(
        cls,
        project: Project,
        missing_decisions: list[str],
    ) -> ArchitectureArtifact:
        authoritative = cls._authoritative_definition(project)
        return ArchitectureArtifact(
            version=1,
            status=ArchitectureStatus.NEEDS_INPUT,
            missing_decisions=list(missing_decisions),
            backend_stack=dict(authoritative["backend_stack"]),
            backend_architecture=dict(
                authoritative["backend_architecture"]
            ),
            frontend_stack=dict(authoritative["frontend_stack"]),
            frontend_architecture=dict(
                authoritative["frontend_architecture"]
            ),
            infrastructure=dict(authoritative["infrastructure"]),
            technical_constraints=list(
                authoritative["technical_constraints"]
            ),
            modules=[],
            interfaces=[],
            apis=[],
            persistence={},
            execution_plan=[],
        )

    @classmethod
    def _output_contract(cls, project: Project) -> dict[str, Any]:
        contract = ArchitectureArtifact.output_contract()
        authoritative = cls._authoritative_definition(project)
        properties = contract["properties"]
        for field_name, value in authoritative.items():
            field_contract = dict(properties[field_name])
            field_contract["const"] = value
            properties[field_name] = field_contract

        contract["authoritative_technical_definition"] = authoritative
        contract["authoritative_persistence_technologies"] = (
            cls._persistence_technologies(project)
        )
        contract["instruction"] = (
            f"{contract['instruction']} Copy every authoritative technical "
            "field exactly. Do not add, replace or infer technologies, "
            "frameworks, databases, cloud providers, infrastructure or "
            "architecture styles. A complete architecture must define every "
            "module responsibility and dependency, every cross-module "
            "interface operation, every API protocol and operation contract, "
            "and an ordered implementation plan referencing declared modules. "
            "Persistence stores must use exactly the authoritative persistence "
            "technologies. Use status needs_input with a non-empty missing_"
            "decisions list and no definitive design when another technical "
            "decision is required."
        )
        return contract

    @classmethod
    def _validate_project_authority(
        cls,
        project: Project,
        artifact: ArchitectureArtifact,
    ) -> None:
        mismatches = [
            field_name
            for field_name, expected in cls._authoritative_definition(
                project
            ).items()
            if getattr(artifact, field_name) != expected
        ]
        if mismatches:
            raise ArchitectureArtifactValidationError(
                "Architecture output contradicts authoritative Project "
                "fields: " + ", ".join(mismatches)
            )
        if artifact.status is ArchitectureStatus.COMPLETE:
            cls._validate_persistence_authority(project, artifact)

    @staticmethod
    def _persistence_technologies(project: Project) -> list[str]:
        technologies = []

        def collect(value: Any) -> None:
            if isinstance(value, str) and value.strip():
                technologies.append(value.strip())
            elif isinstance(value, dict):
                for nested_value in value.values():
                    collect(nested_value)
            elif isinstance(value, list):
                for nested_value in value:
                    collect(nested_value)

        for definition in (project.backend_stack, project.infrastructure):
            for key, value in definition.items():
                if key.casefold() in PERSISTENCE_DECISION_KEYS:
                    collect(value)

        return list(dict.fromkeys(technologies))

    @classmethod
    def _validate_persistence_authority(
        cls,
        project: Project,
        artifact: ArchitectureArtifact,
    ) -> None:
        expected = {
            technology.casefold()
            for technology in cls._persistence_technologies(project)
        }
        stores = artifact.persistence.get("stores", [])
        actual = {
            store["technology"].casefold()
            for store in stores
        }
        if expected != actual:
            raise ArchitectureArtifactValidationError(
                "Architecture persistence contradicts authoritative Project "
                "technologies"
            )

    def _update_status(self, task: Task, status: TaskStatus) -> None:
        self.task_status_writer.update_status(task.id, status)
        task.status = status
