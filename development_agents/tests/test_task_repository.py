import unittest
from unittest.mock import patch
from uuid import UUID

from core.infrastructure.repositories.task_repository import TaskRepository


class TaskRepositoryTests(unittest.TestCase):
    @patch("core.infrastructure.repositories.task_repository.psycopg.connect")
    def test_update_status_updates_task_by_id(self, connect):
        task_id = UUID("2cb5fe26-74a0-48f1-a989-aaf9b41b343b")
        connection = connect.return_value.__enter__.return_value
        cursor = connection.cursor.return_value.__enter__.return_value

        TaskRepository().update_status(task_id, "in_progress")

        statement, parameters = cursor.execute.call_args.args
        self.assertEqual(
            " ".join(statement.split()),
            "UPDATE tasks SET status = %s WHERE id = %s",
        )
        self.assertEqual(parameters, ("in_progress", task_id))


if __name__ == "__main__":
    unittest.main()
