import psycopg
from psycopg.types.json import Jsonb

from core.contracts.configuration import DatabaseSettings
from core.contracts.results import TaskResultWriter
from core.models.task_result import TaskExecutionResult


class PostgresTaskResultRepository(TaskResultWriter):
    def __init__(self, settings: DatabaseSettings):
        self.settings = settings

    def write(self, result: TaskExecutionResult) -> None:
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
                    INSERT INTO task_results (
                        id,
                        task_id,
                        project_id,
                        agent,
                        provider,
                        model_alias,
                        output,
                        metadata,
                        created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        result.id,
                        result.task_id,
                        result.project_id,
                        result.agent,
                        result.provider,
                        result.model_alias,
                        result.output,
                        Jsonb(result.metadata),
                        result.created_at,
                    ),
                )
