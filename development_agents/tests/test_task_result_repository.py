import unittest
from datetime import datetime, timezone
from unittest.mock import call, patch
from uuid import UUID

from core.contracts.configuration import DatabaseSettings
from core.contracts.results import TaskResultWriter
from core.infrastructure.repositories.task_result_repository import (
    PostgresTaskResultRepository,
)
from core.models.task_result import TaskExecutionResult


class TaskResultRepositoryTests(unittest.TestCase):
    settings = DatabaseSettings(
        host="postgres",
        port="5432",
        database="agent_forge",
        user="agent_forge",
        password="database-key",
    )

    @patch(
        "core.infrastructure.repositories.task_result_repository.Jsonb",
        side_effect=lambda value: value,
    )
    @patch(
        "core.infrastructure.repositories.task_result_repository.psycopg.connect"
    )
    def test_writes_complete_task_execution_result(self, connect, jsonb):
        result = TaskExecutionResult(
            id=UUID("5bdb9d83-8872-4c33-ab6d-4ab56f10f261"),
            task_id=UUID("2cb5fe26-74a0-48f1-a989-aaf9b41b343b"),
            project_id=UUID("93de5ea5-729a-4c5e-8dc3-443165ed516b"),
            agent="architect",
            provider="litellm",
            model_alias="architecture-primary",
            output="Architecture output",
            metadata={"usage": {"total_tokens": 12}},
            created_at=datetime(2026, 8, 12, 10, 30, tzinfo=timezone.utc),
        )
        connection = connect.return_value.__enter__.return_value
        cursor = connection.cursor.return_value.__enter__.return_value
        repository = PostgresTaskResultRepository(self.settings)

        repository.write(result)

        self.assertIsInstance(repository, TaskResultWriter)
        statement, parameters = cursor.execute.call_args.args
        self.assertEqual(
            " ".join(statement.split()),
            (
                "INSERT INTO task_results ( id, task_id, project_id, agent, "
                "provider, model_alias, output, metadata, created_at ) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
            ),
        )
        self.assertEqual(
            parameters,
            (
                result.id,
                result.task_id,
                result.project_id,
                "architect",
                "litellm",
                "architecture-primary",
                "Architecture output",
                result.metadata,
                result.created_at,
            ),
        )
        self.assertEqual(jsonb.call_args_list, [call(result.metadata)])
