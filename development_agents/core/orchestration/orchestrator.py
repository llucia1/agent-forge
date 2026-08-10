from core.models.project import Project
from core.models.task import Task


class Orchestrator:
    def create_project(self, name: str, description: str) -> Project:
        return Project(
            name=name,
            description=description,
        )

    def create_task(
        self,
        project: Project,
        title: str,
        description: str,
        agent: str,
    ) -> Task:
        return Task(
            project_id=project.id,
            title=title,
            description=description,
            agent=agent,
        )