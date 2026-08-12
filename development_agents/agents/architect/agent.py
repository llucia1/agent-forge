from core.engines import AgentEngine, EngineResult
from core.infrastructure.repositories.project_repository import ProjectRepository
from core.infrastructure.repositories.task_repository import TaskRepository
from core.memory import ContextProvider
from core.models.project import Project
from core.models.task import Task


class ProjectNotFoundError(LookupError):
    """Raised when a task references a project that does not exist."""


class ArchitectAgent:
    def __init__(
        self,
        task_repository: TaskRepository,
        project_repository: ProjectRepository,
        engine: AgentEngine,
        context_provider: ContextProvider,
    ):
        self.task_repository = task_repository
        self.project_repository = project_repository
        self.engine = engine
        self.context_provider = context_provider

    def handle(self, task: Task) -> None:
        self._update_status(task, "in_progress")

        try:
            project = self.project_repository.find_by_id(task.project_id)

            if project is None:
                raise ProjectNotFoundError(
                    f"Project not found: {task.project_id}"
                )

            self.process(task, project)
            self._update_status(task, "completed")
        except Exception:
            try:
                self._update_status(task, "failed")
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

    def _update_status(self, task: Task, status: str) -> None:
        self.task_repository.update_status(task.id, status)
        task.status = status
