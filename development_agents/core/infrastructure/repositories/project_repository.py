from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from core.contracts.configuration import DatabaseSettings
from core.contracts.repositories import ProjectCreator, ProjectReader
from core.models.project import Project


class PostgresProjectRepository(ProjectCreator, ProjectReader):
    def __init__(self, settings: DatabaseSettings):
        self.settings = settings

    def create(self, project: Project) -> None:
        with psycopg.connect(
            host=self.settings.host,
            port=self.settings.port,
            dbname=self.settings.database,
            user=self.settings.user,
            password=self.settings.password,
        ) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO projects (
                        id,
                        name,
                        description,
                        status,
                        backend_stack,
                        backend_architecture,
                        frontend_stack,
                        frontend_architecture,
                        infrastructure,
                        technical_constraints
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        project.id,
                        project.name,
                        project.description,
                        project.status,
                        Jsonb(project.backend_stack),
                        Jsonb(project.backend_architecture),
                        Jsonb(project.frontend_stack),
                        Jsonb(project.frontend_architecture),
                        Jsonb(project.infrastructure),
                        Jsonb(project.technical_constraints),
                    ),
                )

    def find_by_id(self, project_id: UUID) -> Project | None:
        with psycopg.connect(
            host=self.settings.host,
            port=self.settings.port,
            dbname=self.settings.database,
            user=self.settings.user,
            password=self.settings.password,
        ) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        id,
                        name,
                        description,
                        status,
                        backend_stack,
                        backend_architecture,
                        frontend_stack,
                        frontend_architecture,
                        infrastructure,
                        technical_constraints
                    FROM projects
                    WHERE id = %s
                    """,
                    (project_id,),
                )
                row = cursor.fetchone()

        if row is None:
            return None

        return Project(
            id=row[0],
            name=row[1],
            description=row[2],
            status=row[3],
            backend_stack=row[4],
            backend_architecture=row[5],
            frontend_stack=row[6],
            frontend_architecture=row[7],
            infrastructure=row[8],
            technical_constraints=row[9],
        )
