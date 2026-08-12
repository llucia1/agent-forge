from core.contracts.context import ContextProvider
from core.contracts.engines import AgentEngine, EngineResult
from core.contracts.messaging import TaskHandler
from core.contracts.repositories import ProjectReader, TaskStatusWriter
from core.models.project import Project
from core.models.task import Task, TaskStatus


class ProjectNotFoundError(LookupError):
    """Raised when a task references a project that does not exist."""


class ArchitectAgent(TaskHandler):
    def __init__(
        self,
        task_status_writer: TaskStatusWriter,
        project_reader: ProjectReader,
        engine: AgentEngine,
        context_provider: ContextProvider,
    ):
        self.task_status_writer = task_status_writer
        self.project_reader = project_reader
        self.engine = engine
        self.context_provider = context_provider

    def handle(self, task: Task) -> None:
        self._update_status(task, TaskStatus.IN_PROGRESS)

        try:
            project = self.project_reader.find_by_id(task.project_id)

            if project is None:
                raise ProjectNotFoundError(
                    f"Project not found: {task.project_id}"
                )

            self.process(task, project)
            self._update_status(task, TaskStatus.COMPLETED)
        except Exception:
            try:
                self._update_status(task, TaskStatus.FAILED)
            except Exception:
                pass

            raise

    def process(self, task: Task, project: Project) -> EngineResult:
        context = self.context_provider.build(task, project)
        return self.engine.run(
            task=task,
            project=project,
            context=context,
        )

    def _update_status(self, task: Task, status: TaskStatus) -> None:
        self.task_status_writer.update_status(task.id, status)
        task.status = status
