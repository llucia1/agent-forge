from core.models.project import Project
from core.models.task import Task
from core.infrastructure.rabbitmq import TaskPublisher
from core.infrastructure.repositories.project_repository import ProjectRepository
from core.infrastructure.repositories.task_repository import TaskRepository


class Orchestrator:
    def __init__(
        self,
        project_repository: ProjectRepository | None = None,
        task_repository: TaskRepository | None = None,
        task_publisher: TaskPublisher | None = None,
    ):
        self.project_repository = (
            project_repository
            if project_repository is not None
            else ProjectRepository()
        )
        self.task_repository = (
            task_repository
            if task_repository is not None
            else TaskRepository()
        )
        self.task_publisher = (
            task_publisher
            if task_publisher is not None
            else TaskPublisher()
        )

    def create_project(self, name: str, description: str) -> Project:
        project = Project(
            name=name,
            description=description,
        )

        self.project_repository.create(project)

        return project

    def create_task(
        self,
        project: Project,
        title: str,
        description: str,
        agent: str,
    ) -> Task:
        task = Task(
            project_id=project.id,
            title=title,
            description=description,
            agent=agent,
        )

        self.task_repository.create(task)
        self.task_publisher.publish(task)

        return task
