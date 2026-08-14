import unittest
from datetime import timezone
from uuid import UUID

from core.models.task_result import TaskExecutionResult


class TaskExecutionResultTests(unittest.TestCase):
    def test_creates_neutral_persistable_execution_result(self):
        result = TaskExecutionResult(
            task_id=UUID("2cb5fe26-74a0-48f1-a989-aaf9b41b343b"),
            project_id=UUID("93de5ea5-729a-4c5e-8dc3-443165ed516b"),
            agent="architect",
            provider="litellm",
            model_alias="architecture-primary",
            output="Architecture output",
            metadata={"usage": {"total_tokens": 12}},
        )

        self.assertIsInstance(result.id, UUID)
        self.assertEqual(result.created_at.tzinfo, timezone.utc)
        self.assertEqual(result.agent, "architect")
        self.assertEqual(result.provider, "litellm")
        self.assertEqual(result.model_alias, "architecture-primary")
