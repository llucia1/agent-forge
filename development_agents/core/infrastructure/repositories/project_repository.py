import os
import psycopg

from core.models.project import Project


class ProjectRepository:
    def create(self, project: Project) -> None:
        with psycopg.connect(
            host=os.getenv("POSTGRES_HOST"),
            port=os.getenv("POSTGRES_PORT"),
            dbname=os.getenv("POSTGRES_DB"),
            user=os.getenv("POSTGRES_USER"),
            password=os.getenv("POSTGRES_PASSWORD"),
        ) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO projects (id, name, description, status)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        project.id,
                        project.name,
                        project.description,
                        project.status,
                    ),
                )