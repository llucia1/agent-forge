import unittest
from unittest.mock import Mock, call

from core.contracts.messaging import TaskPublisher
from core.contracts.repositories import ProjectCreator, TaskCreator
from core.models.project import Project
from core.models.task import AgentRole
from core.orchestration.orchestrator import Orchestrator


class OrchestratorTaskPublishingTests(unittest.TestCase):
    def test_create_project_persists_technical_definition(self):
        project_creator = Mock(spec=ProjectCreator)
        orchestrator = Orchestrator(
            project_creator=project_creator,
            task_creator=Mock(spec=TaskCreator),
            task_publisher=Mock(spec=TaskPublisher),
        )
        technical_definition = {
            "backend_stack": {"language": "Python"},
            "backend_architecture": {"style": "layered"},
            "frontend_stack": {"framework": "React"},
            "frontend_architecture": {"pattern": "component-based"},
            "infrastructure": {"runtime": "Docker Compose"},
            "technical_constraints": ["Use Python 3.12"],
        }

        project = orchestrator.create_project(
            name="AgentForge",
            description="Agent platform",
            **technical_definition,
        )

        self.assertIs(project_creator.create.call_args.args[0], project)
        self.assertEqual(project.backend_stack, {"language": "Python"})
        self.assertEqual(project.backend_architecture, {"style": "layered"})
        self.assertEqual(project.frontend_stack, {"framework": "React"})
        self.assertEqual(
            project.frontend_architecture,
            {"pattern": "component-based"},
        )
        self.assertEqual(
            project.infrastructure,
            {"runtime": "Docker Compose"},
        )
        self.assertEqual(
            project.technical_constraints,
            ["Use Python 3.12"],
        )

    def test_create_task_persists_before_publishing(self):
        operations = Mock()
        task_creator = Mock(spec=TaskCreator)
        task_publisher = Mock(spec=TaskPublisher)
        task_creator.create.side_effect = operations.persist
        task_publisher.publish.side_effect = operations.publish
        orchestrator = Orchestrator(
            project_creator=Mock(spec=ProjectCreator),
            task_creator=task_creator,
            task_publisher=task_publisher,
        )

        task = orchestrator.create_task(
            project=Project(name="AgentForge", description="Test project"),
            title="Design architecture",
            description="Define the service boundaries",
            agent=AgentRole.ARCHITECT,
        )

        self.assertEqual(
            operations.mock_calls,
            [call.persist(task), call.publish(task)],
        )
        self.assertIs(task_creator.create.call_args.args[0], task)
        self.assertIs(task_publisher.publish.call_args.args[0], task)

    def test_create_task_does_not_publish_when_persistence_fails(self):
        task_creator = Mock(spec=TaskCreator)
        task_creator.create.side_effect = RuntimeError("database unavailable")
        task_publisher = Mock(spec=TaskPublisher)
        orchestrator = Orchestrator(
            project_creator=Mock(spec=ProjectCreator),
            task_creator=task_creator,
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
                agent=AgentRole.ARCHITECT,
            )

        task_publisher.publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
