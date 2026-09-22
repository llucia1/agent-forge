import psycopg
from psycopg.types.json import Jsonb
from uuid import UUID

from core.contracts.configuration import DatabaseSettings
from core.contracts.results import LatestTaskExecutionReader, TaskResultWriter
from core.models.task import AgentRole, TaskStatus
from core.models.task_result import AgentTaskExecution, TaskExecutionResult


class PostgresTaskResultRepository(
    TaskResultWriter,
    LatestTaskExecutionReader,
):
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

    def find_latest(
        self,
        project_id: UUID,
        agent: AgentRole,
    ) -> AgentTaskExecution | None:
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
                        task.id,
                        task.project_id,
                        task.agent,
                        task.status,
                        result.id,
                        result.agent,
                        result.provider,
                        result.model_alias,
                        result.output,
                        result.metadata,
                        result.created_at
                    FROM tasks AS task
                    LEFT JOIN LATERAL (
                        SELECT *
                        FROM task_results
                        WHERE task_id = task.id
                        ORDER BY created_at DESC
                        LIMIT 1
                    ) AS result ON TRUE
                    WHERE task.project_id = %s AND task.agent = %s
                    ORDER BY task.created_at DESC
                    LIMIT 1
                    """,
                    (project_id, str(agent)),
                )
                row = cursor.fetchone()

        if row is None:
            return None

        result = None
        if row[4] is not None:
            result = TaskExecutionResult(
                id=row[4],
                task_id=row[0],
                project_id=row[1],
                agent=row[5],
                provider=row[6],
                model_alias=row[7],
                output=row[8],
                metadata=row[9],
                created_at=row[10],
            )
        return AgentTaskExecution(
            task_id=row[0],
            project_id=row[1],
            agent=AgentRole(row[2]),
            task_status=TaskStatus(row[3]),
            result=result,
        )
