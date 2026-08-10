from core.models.project import Project
from core.models.task import Task
from core.infrastructure.repositories.project_repository import ProjectRepository
from core.infrastructure.repositories.task_repository import TaskRepository


class Orchestrator:
    def __init__(self):
        self.project_repository = ProjectRepository()
        self.task_repository = TaskRepository()

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

        return task