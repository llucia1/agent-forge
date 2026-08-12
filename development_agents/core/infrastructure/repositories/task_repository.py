from uuid import UUID

import psycopg

from core.contracts.configuration import DatabaseSettings
from core.contracts.repositories import TaskCreator, TaskStatusWriter
from core.models.task import Task, TaskStatus


class PostgresTaskRepository(TaskCreator, TaskStatusWriter):
    def __init__(self, settings: DatabaseSettings):
        self.settings = settings

    def create(self, task: Task) -> None:
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
                        str(task.agent),
                        str(task.status),
                    ),
                )

    def update_status(self, task_id: UUID, status: TaskStatus) -> None:
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
                    UPDATE tasks
                    SET status = %s
                    WHERE id = %s
                    """,
                    (str(status), task_id),
                )
