import unittest
from unittest.mock import Mock, call

from core.contracts.messaging import TaskPublisher
from core.contracts.repositories import (
    ProjectCreator,
    ProjectDeleter,
    ProjectReader,
    TaskCreator,
    TaskDeleter,
)
from core.contracts.workspaces import (
    ProjectWorkspaceInitializer,
    ProjectWorkspaceRemover,
)
from core.models.project import Project
from core.models.task import AgentRole
from core.orchestration.orchestrator import Orchestrator


class OrchestratorTaskPublishingTests(unittest.TestCase):
    def test_create_project_persists_technical_definition(self):
        project_creator = Mock(spec=ProjectCreator)
        workspace_initializer = Mock(spec=ProjectWorkspaceInitializer)
        orchestrator = Orchestrator(
            project_creator=project_creator,
            task_creator=Mock(spec=TaskCreator),
            task_publisher=Mock(spec=TaskPublisher),
            workspace_initializer=workspace_initializer,
            project_reader=Mock(spec=ProjectReader),
            project_deleter=Mock(spec=ProjectDeleter),
            task_deleter=Mock(spec=TaskDeleter),
            workspace_remover=Mock(spec=ProjectWorkspaceRemover),
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
        workspace_initializer.initialize.assert_called_once_with(project.id)

    def test_create_project_persists_before_initializing_workspace(self):
        operations = Mock()
        project_creator = Mock(spec=ProjectCreator)
        workspace_initializer = Mock(spec=ProjectWorkspaceInitializer)
        project_creator.create.side_effect = operations.persist
        workspace_initializer.initialize.side_effect = operations.initialize
        orchestrator = Orchestrator(
            project_creator=project_creator,
            task_creator=Mock(spec=TaskCreator),
            task_publisher=Mock(spec=TaskPublisher),
            workspace_initializer=workspace_initializer,
            project_reader=Mock(spec=ProjectReader),
            project_deleter=Mock(spec=ProjectDeleter),
            task_deleter=Mock(spec=TaskDeleter),
            workspace_remover=Mock(spec=ProjectWorkspaceRemover),
        )

        project = orchestrator.create_project(
            name="AgentForge",
            description="Agent platform",
        )

        self.assertEqual(
            operations.mock_calls,
            [call.persist(project), call.initialize(project.id)],
        )

    def test_create_task_persists_before_publishing(self):
        operations = Mock()
        task_creator = Mock(spec=TaskCreator)
        task_publisher = Mock(spec=TaskPublisher)
        project = Project(name="AgentForge", description="Test project")
        project_reader = Mock(spec=ProjectReader)
        project_reader.find_by_id.return_value = project
        task_creator.create.side_effect = operations.persist
        task_publisher.publish.side_effect = operations.publish
        orchestrator = Orchestrator(
            project_creator=Mock(spec=ProjectCreator),
            task_creator=task_creator,
            task_publisher=task_publisher,
            workspace_initializer=Mock(spec=ProjectWorkspaceInitializer),
            project_reader=project_reader,
            project_deleter=Mock(spec=ProjectDeleter),
            task_deleter=Mock(spec=TaskDeleter),
            workspace_remover=Mock(spec=ProjectWorkspaceRemover),
        )

        task = orchestrator.create_task(
            project=project,
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
        project = Project(name="AgentForge", description="Test project")
        project_reader = Mock(spec=ProjectReader)
        project_reader.find_by_id.return_value = project
        orchestrator = Orchestrator(
            project_creator=Mock(spec=ProjectCreator),
            task_creator=task_creator,
            task_publisher=task_publisher,
            workspace_initializer=Mock(spec=ProjectWorkspaceInitializer),
            project_reader=project_reader,
            project_deleter=Mock(spec=ProjectDeleter),
            task_deleter=Mock(spec=TaskDeleter),
            workspace_remover=Mock(spec=ProjectWorkspaceRemover),
        )

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            orchestrator.create_task(
                project=project,
                title="Design architecture",
                description="Define the service boundaries",
                agent=AgentRole.ARCHITECT,
            )

        task_publisher.publish.assert_not_called()

    def test_workspace_failure_compensates_project_and_partial_workspace(self):
        project_deleter = Mock(spec=ProjectDeleter)
        workspace_initializer = Mock(spec=ProjectWorkspaceInitializer)
        workspace_initializer.initialize.side_effect = RuntimeError(
            "workspace unavailable"
        )
        workspace_remover = Mock(spec=ProjectWorkspaceRemover)
        orchestrator = Orchestrator(
            project_creator=Mock(spec=ProjectCreator),
            task_creator=Mock(spec=TaskCreator),
            task_publisher=Mock(spec=TaskPublisher),
            workspace_initializer=workspace_initializer,
            project_reader=Mock(spec=ProjectReader),
            project_deleter=project_deleter,
            task_deleter=Mock(spec=TaskDeleter),
            workspace_remover=workspace_remover,
        )

        with self.assertRaisesRegex(RuntimeError, "workspace unavailable"):
            orchestrator.create_project("AgentForge", "Agent platform")

        project_id = workspace_initializer.initialize.call_args.args[0]
        workspace_remover.remove.assert_called_once_with(project_id)
        project_deleter.delete.assert_called_once_with(project_id)

    def test_publication_failure_compensates_unpublished_task(self):
        project = Project(name="AgentForge", description="Test project")
        project_reader = Mock(spec=ProjectReader)
        project_reader.find_by_id.return_value = project
        task_publisher = Mock(spec=TaskPublisher)
        task_publisher.publish.side_effect = RuntimeError("broker unavailable")
        task_deleter = Mock(spec=TaskDeleter)
        orchestrator = Orchestrator(
            project_creator=Mock(spec=ProjectCreator),
            task_creator=Mock(spec=TaskCreator),
            task_publisher=task_publisher,
            workspace_initializer=Mock(spec=ProjectWorkspaceInitializer),
            project_reader=project_reader,
            project_deleter=Mock(spec=ProjectDeleter),
            task_deleter=task_deleter,
            workspace_remover=Mock(spec=ProjectWorkspaceRemover),
        )

        with self.assertRaisesRegex(RuntimeError, "broker unavailable"):
            orchestrator.create_task(
                project,
                "Design architecture",
                "Define service boundaries",
                AgentRole.ARCHITECT,
            )

        persisted_task = task_publisher.publish.call_args.args[0]
        task_deleter.delete.assert_called_once_with(persisted_task.id)

    def test_unknown_project_never_creates_or_publishes_task(self):
        project_reader = Mock(spec=ProjectReader)
        project_reader.find_by_id.return_value = None
        task_creator = Mock(spec=TaskCreator)
        task_publisher = Mock(spec=TaskPublisher)
        orchestrator = Orchestrator(
            project_creator=Mock(spec=ProjectCreator),
            task_creator=task_creator,
            task_publisher=task_publisher,
            workspace_initializer=Mock(spec=ProjectWorkspaceInitializer),
            project_reader=project_reader,
            project_deleter=Mock(spec=ProjectDeleter),
            task_deleter=Mock(spec=TaskDeleter),
            workspace_remover=Mock(spec=ProjectWorkspaceRemover),
        )

        with self.assertRaisesRegex(ValueError, "unknown project"):
            orchestrator.create_task(
                Project(name="Unknown", description="Not persisted"),
                "Task",
                "Must not be persisted",
                AgentRole.ARCHITECT,
            )

        task_creator.create.assert_not_called()
        task_publisher.publish.assert_not_called()

    def test_invalid_project_input_is_rejected_before_persistence(self):
        project_creator = Mock(spec=ProjectCreator)
        orchestrator = Orchestrator(
            project_creator=project_creator,
            task_creator=Mock(spec=TaskCreator),
            task_publisher=Mock(spec=TaskPublisher),
            workspace_initializer=Mock(spec=ProjectWorkspaceInitializer),
            project_reader=Mock(spec=ProjectReader),
            project_deleter=Mock(spec=ProjectDeleter),
            task_deleter=Mock(spec=TaskDeleter),
            workspace_remover=Mock(spec=ProjectWorkspaceRemover),
        )

        with self.assertRaisesRegex(ValueError, "Project name"):
            orchestrator.create_project(" ", "Description")

        project_creator.create.assert_not_called()

    def test_invalid_task_input_is_rejected_before_database_lookup(self):
        project_reader = Mock(spec=ProjectReader)
        task_creator = Mock(spec=TaskCreator)
        orchestrator = Orchestrator(
            project_creator=Mock(spec=ProjectCreator),
            task_creator=task_creator,
            task_publisher=Mock(spec=TaskPublisher),
            workspace_initializer=Mock(spec=ProjectWorkspaceInitializer),
            project_reader=project_reader,
            project_deleter=Mock(spec=ProjectDeleter),
            task_deleter=Mock(spec=TaskDeleter),
            workspace_remover=Mock(spec=ProjectWorkspaceRemover),
        )

        with self.assertRaisesRegex(ValueError, "Task title"):
            orchestrator.create_task(
                Project(name="Project", description="Description"),
                "",
                "Description",
                AgentRole.ARCHITECT,
            )

        project_reader.find_by_id.assert_not_called()
        task_creator.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
