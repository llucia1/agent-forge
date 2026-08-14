import unittest
from unittest.mock import patch
from uuid import UUID

from core.contracts.configuration import DatabaseSettings
from core.contracts.repositories import TaskCreator, TaskStatusWriter
from core.infrastructure.repositories.task_repository import (
    PostgresTaskRepository,
)
from core.models.task import AgentRole, Task, TaskStatus


class TaskRepositoryTests(unittest.TestCase):
    settings = DatabaseSettings(
        host="postgres",
        port="5432",
        database="agent_forge",
        user="agent_forge",
        password="database-key",
    )

    @patch("core.infrastructure.repositories.task_repository.psycopg.connect")
    def test_create_persists_task_with_stable_string_protocol(self, connect):
        task = Task(
            project_id=UUID("93de5ea5-729a-4c5e-8dc3-443165ed516b"),
            title="Define architecture",
            description="Define the application architecture",
            agent=AgentRole.ARCHITECT,
        )
        connection = connect.return_value.__enter__.return_value
        cursor = connection.cursor.return_value.__enter__.return_value

        PostgresTaskRepository(self.settings).create(task)

        statement, parameters = cursor.execute.call_args.args
        self.assertEqual(
            " ".join(statement.split()),
            (
                "INSERT INTO tasks ( id, project_id, title, description, "
                "agent, status ) VALUES (%s, %s, %s, %s, %s, %s)"
            ),
        )
        self.assertEqual(
            parameters,
            (
                task.id,
                task.project_id,
                task.title,
                task.description,
                "architect",
                "pending",
            ),
        )

    @patch("core.infrastructure.repositories.task_repository.psycopg.connect")
    def test_update_status_updates_task_by_id(self, connect):
        task_id = UUID("2cb5fe26-74a0-48f1-a989-aaf9b41b343b")
        connection = connect.return_value.__enter__.return_value
        cursor = connection.cursor.return_value.__enter__.return_value

        repository = PostgresTaskRepository(self.settings)
        self.assertIsInstance(repository, TaskCreator)
        self.assertIsInstance(repository, TaskStatusWriter)
        repository.update_status(task_id, TaskStatus.IN_PROGRESS)

        statement, parameters = cursor.execute.call_args.args
        self.assertEqual(
            " ".join(statement.split()),
            "UPDATE tasks SET status = %s WHERE id = %s",
        )
        self.assertEqual(parameters, ("in_progress", task_id))

    @patch("core.infrastructure.repositories.task_repository.psycopg.connect")
    def test_update_status_persists_resumable_needs_input(self, connect):
        task_id = UUID("2cb5fe26-74a0-48f1-a989-aaf9b41b343b")
        connection = connect.return_value.__enter__.return_value
        cursor = connection.cursor.return_value.__enter__.return_value

        PostgresTaskRepository(self.settings).update_status(
            task_id,
            TaskStatus.NEEDS_INPUT,
        )

        _, parameters = cursor.execute.call_args.args
        self.assertEqual(parameters, ("needs_input", task_id))


if __name__ == "__main__":
    unittest.main()
