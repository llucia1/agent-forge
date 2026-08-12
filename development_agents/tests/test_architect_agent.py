import unittest
from unittest.mock import Mock, call, patch
from uuid import UUID

from agents.architect.agent import ArchitectAgent, ProjectNotFoundError
from core.contracts.context import ContextProvider
from core.contracts.engines import AgentEngine, EngineResult
from core.contracts.repositories import ProjectReader, TaskStatusWriter
from core.models.project import Project
from core.models.task import AgentRole, Task, TaskStatus


class ArchitectAgentTests(unittest.TestCase):
    def setUp(self):
        self.task = Task(
            id=UUID("2cb5fe26-74a0-48f1-a989-aaf9b41b343b"),
            project_id=UUID("93de5ea5-729a-4c5e-8dc3-443165ed516b"),
            title="Define architecture",
            description="Define the application architecture",
            agent=AgentRole.ARCHITECT,
        )
        self.task_status_writer = Mock(spec=TaskStatusWriter)
        self.project = Project(
            id=self.task.project_id,
            name="AgentForge",
            description="Agent platform",
            backend_stack={"language": "Python"},
            backend_architecture={"style": "layered"},
            frontend_stack={"framework": "React"},
            frontend_architecture={"pattern": "component-based"},
            infrastructure={"runtime": "Docker Compose"},
            technical_constraints=["Use Python 3.12"],
        )
        self.project_reader = Mock(spec=ProjectReader)
        self.project_reader.find_by_id.return_value = self.project
        self.engine = Mock(spec=AgentEngine)
        self.engine.run.return_value = EngineResult(
            output="Architecture stub",
            metadata={"stub": True},
            provider="test",
        )
        self.context = {
            "project_memory": {"summary": "Known project context"},
            "previous_decisions": ["Use PostgreSQL"],
            "feedback": ["Keep modules small"],
            "rag_context": None,
        }
        self.context_provider = Mock(spec=ContextProvider)
        self.context_provider.build.return_value = self.context
        self.agent = ArchitectAgent(
            self.task_status_writer,
            self.project_reader,
            self.engine,
            self.context_provider,
        )

    def test_transitions_from_pending_to_in_progress_to_completed(self):
        events = []
        self.task_status_writer.update_status.side_effect = (
            lambda task_id, status: events.append(status)
        )

        self.context_provider.build.side_effect = lambda task, project: (
            events.append("context") or self.context
        )
        self.engine.run.side_effect = lambda **kwargs: (
            events.append("engine")
            or EngineResult("Architecture stub", {}, "test")
        )

        self.assertEqual(self.task.status, "pending")

        self.agent.handle(self.task)

        self.assertEqual(
            events,
            ["in_progress", "context", "engine", "completed"],
        )
        self.assertEqual(
            self.task_status_writer.update_status.call_args_list,
            [
                call(self.task.id, TaskStatus.IN_PROGRESS),
                call(self.task.id, TaskStatus.COMPLETED),
            ],
        )
        self.project_reader.find_by_id.assert_called_once_with(
            self.task.project_id
        )
        self.context_provider.build.assert_called_once_with(
            self.task,
            self.project,
        )
        self.engine.run.assert_called_once_with(
            task=self.task,
            project=self.project,
            context=self.context,
        )
        self.assertEqual(self.task.status, "completed")

    def test_marks_task_as_failed_when_project_does_not_exist(self):
        self.project_reader.find_by_id.return_value = None

        with patch.object(self.agent, "process") as process:
            with self.assertRaisesRegex(
                ProjectNotFoundError,
                f"Project not found: {self.task.project_id}",
            ):
                self.agent.handle(self.task)

        self.project_reader.find_by_id.assert_called_once_with(
            self.task.project_id
        )
        process.assert_not_called()
        self.context_provider.build.assert_not_called()
        self.engine.run.assert_not_called()
        self.assertEqual(
            self.task_status_writer.update_status.call_args_list,
            [
                call(self.task.id, TaskStatus.IN_PROGRESS),
                call(self.task.id, TaskStatus.FAILED),
            ],
        )
        self.assertEqual(self.task.status, "failed")

    def test_marks_task_as_failed_and_propagates_processing_error(self):
        processing_error = RuntimeError("processing failed")

        self.engine.run.side_effect = processing_error

        with self.assertRaises(RuntimeError) as raised:
            self.agent.handle(self.task)

        self.assertIs(raised.exception, processing_error)
        self.assertEqual(
            self.task_status_writer.update_status.call_args_list,
            [
                call(self.task.id, TaskStatus.IN_PROGRESS),
                call(self.task.id, TaskStatus.FAILED),
            ],
        )
        self.assertEqual(self.task.status, "failed")

    def test_preserves_processing_error_when_marking_failed_also_fails(self):
        processing_error = RuntimeError("processing failed")
        persistence_error = RuntimeError("database unavailable")

        def update_status(task_id, status):
            if status == "failed":
                raise persistence_error

        self.task_status_writer.update_status.side_effect = update_status

        self.engine.run.side_effect = processing_error

        with self.assertRaises(RuntimeError) as raised:
            self.agent.handle(self.task)

        self.assertIs(raised.exception, processing_error)
        self.assertEqual(
            self.task_status_writer.update_status.call_args_list,
            [
                call(self.task.id, TaskStatus.IN_PROGRESS),
                call(self.task.id, TaskStatus.FAILED),
            ],
        )
        self.assertEqual(self.task.status, "in_progress")


if __name__ == "__main__":
    unittest.main()
