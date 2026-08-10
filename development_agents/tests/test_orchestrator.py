import unittest
from unittest.mock import Mock, call

from core.models.project import Project
from core.orchestration.orchestrator import Orchestrator


class OrchestratorTaskPublishingTests(unittest.TestCase):
    def test_create_task_persists_before_publishing(self):
        operations = Mock()
        task_repository = Mock()
        task_publisher = Mock()
        task_repository.create.side_effect = operations.persist
        task_publisher.publish.side_effect = operations.publish
        orchestrator = Orchestrator(
            project_repository=Mock(),
            task_repository=task_repository,
            task_publisher=task_publisher,
        )

        task = orchestrator.create_task(
            project=Project(name="AgentForge", description="Test project"),
            title="Design architecture",
            description="Define the service boundaries",
            agent="architect",
        )

        self.assertEqual(
            operations.mock_calls,
            [call.persist(task), call.publish(task)],
        )
        self.assertIs(task_repository.create.call_args.args[0], task)
        self.assertIs(task_publisher.publish.call_args.args[0], task)

    def test_create_task_does_not_publish_when_persistence_fails(self):
        task_repository = Mock()
        task_repository.create.side_effect = RuntimeError("database unavailable")
        task_publisher = Mock()
        orchestrator = Orchestrator(
            project_repository=Mock(),
            task_repository=task_repository,
            task_publisher=task_publisher,
        )

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            orchestrator.create_task(
                project=Project(
                    name="AgentForge",
                    description="Test project",
                ),
                title="Design architecture",
                description="Define the service boundaries",
                agent="architect",
            )

        task_publisher.publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
