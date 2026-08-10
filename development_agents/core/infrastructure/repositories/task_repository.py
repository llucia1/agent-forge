import os
import psycopg

from core.models.task import Task


class TaskRepository:
    def create(self, task: Task) -> None:
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
                    INSERT INTO tasks (
                        id,
                        project_id,
                        title,
                        description,
                        agent,
                        status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        task.id,
                        task.project_id,
                        task.title,
                        task.description,
                        task.agent,
                        task.status,
                    ),
                )