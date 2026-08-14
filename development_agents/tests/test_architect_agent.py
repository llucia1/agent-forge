import json
import unittest
from unittest.mock import Mock, call, patch
from uuid import UUID

from agents.architect.agent import ArchitectAgent, ProjectNotFoundError
from core.contracts.context import ContextProvider
from core.contracts.engines import AgentEngine, EngineResult
from core.contracts.repositories import ProjectReader, TaskStatusWriter
from core.contracts.results import TaskResultWriter
from core.contracts.workspaces import ArchitectureArtifactWriter
from core.models.architecture import (
    ArchitectureArtifact,
    ArchitectureArtifactValidationError,
)
from core.models.project import Project
from core.models.task import AgentRole, Task, TaskStatus
from core.models.task_result import TaskExecutionResult


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
        self.task_result_writer = Mock(spec=TaskResultWriter)
        self.architecture_artifact_writer = Mock(
            spec=ArchitectureArtifactWriter
        )
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
        self.architecture_output = {
            "version": 1,
            "status": "complete",
            "missing_decisions": [],
            "backend_stack": {"language": "Python"},
            "backend_architecture": {"style": "layered"},
            "frontend_stack": {"framework": "React"},
            "frontend_architecture": {"pattern": "component-based"},
            "infrastructure": {"runtime": "Docker Compose"},
            "technical_constraints": ["Use Python 3.12"],
            "modules": [{"name": "architecture"}],
            "interfaces": [{"name": "TaskResultWriter"}],
            "apis": [{"name": "tasks"}],
            "persistence": {"database": "PostgreSQL"},
            "execution_plan": [{"step": "Define contracts"}],
        }
        self.engine_result = EngineResult(
            output=json.dumps(self.architecture_output),
            metadata={"stub": True},
            provider="test",
            model_alias="architecture-primary",
        )
        self.engine.run.return_value = self.engine_result
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
            self.task_result_writer,
            self.architecture_artifact_writer,
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
            or self.engine_result
        )
        self.task_result_writer.write.side_effect = lambda result: (
            events.append("result")
        )
        self.architecture_artifact_writer.write.side_effect = (
            lambda project_id, artifact: events.append("artifact")
        )

        self.assertEqual(self.task.status, "pending")

        self.agent.handle(self.task)

        self.assertEqual(
            events,
            [
                "in_progress",
                "context",
                "engine",
                "result",
                "artifact",
                "completed",
            ],
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
            context={
                **self.context,
                "output_contract": self.agent._output_contract(self.project),
            },
        )
        self.task_result_writer.write.assert_called_once()
        persisted = self.task_result_writer.write.call_args.args[0]
        self.assertIsInstance(persisted, TaskExecutionResult)
        self.assertEqual(persisted.task_id, self.task.id)
        self.assertEqual(persisted.project_id, self.task.project_id)
        self.assertEqual(persisted.agent, "architect")
        self.assertEqual(persisted.provider, "test")
        self.assertEqual(persisted.model_alias, "architecture-primary")
        expected_artifact = ArchitectureArtifact.from_json(
            self.engine_result.output
        )
        self.assertEqual(persisted.output, expected_artifact.to_json())
        self.assertEqual(json.loads(persisted.output)["version"], 1)
        self.assertEqual(persisted.metadata, {"stub": True})
        self.architecture_artifact_writer.write.assert_called_once_with(
            self.task.project_id,
            expected_artifact,
        )
        self.assertEqual(self.task.status, "completed")

    def test_empty_technical_constraints_do_not_require_input(self):
        self.project.technical_constraints = []
        self.architecture_output["technical_constraints"] = []
        self.engine_result = EngineResult(
            output=json.dumps(self.architecture_output),
            metadata={"stub": True},
            provider="test",
            model_alias="architecture-primary",
        )
        self.engine.run.return_value = self.engine_result

        self.agent.handle(self.task)

        self.engine.run.assert_called_once()
        persisted = self.task_result_writer.write.call_args.args[0]
        self.assertEqual(
            json.loads(persisted.output)["technical_constraints"],
            [],
        )
        self.assertEqual(self.task.status, "completed")

    def test_missing_essential_fields_persist_needs_input_without_engine(self):
        self.project.backend_stack = {}
        self.project.frontend_architecture = {}
        events = []
        self.task_status_writer.update_status.side_effect = (
            lambda task_id, status: events.append(status)
        )
        self.task_result_writer.write.side_effect = (
            lambda result: events.append("result")
        )
        self.architecture_artifact_writer.write.side_effect = (
            lambda project_id, artifact: events.append("artifact")
        )

        self.agent.handle(self.task)

        self.context_provider.build.assert_not_called()
        self.engine.run.assert_not_called()
        self.assertEqual(
            events,
            ["in_progress", "result", "artifact", "needs_input"],
        )
        persisted = self.task_result_writer.write.call_args.args[0]
        payload = json.loads(persisted.output)
        self.assertEqual(persisted.provider, "agentforge")
        self.assertIsNone(persisted.model_alias)
        self.assertEqual(
            persisted.metadata,
            {
                "missing_decisions": [
                    "backend_stack",
                    "frontend_architecture",
                ]
            },
        )
        self.assertEqual(payload["status"], "needs_input")
        self.assertEqual(
            payload["missing_decisions"],
            ["backend_stack", "frontend_architecture"],
        )
        self.assertEqual(payload["technical_constraints"], ["Use Python 3.12"])
        self.assertEqual(payload["modules"], [])
        self.assertEqual(payload["interfaces"], [])
        self.assertEqual(payload["apis"], [])
        self.assertEqual(payload["persistence"], {})
        self.assertEqual(payload["execution_plan"], [])
        self.assertEqual(self.task.status, "needs_input")

    def test_engine_can_return_non_definitive_needs_input(self):
        needs_input = {
            **self.architecture_output,
            "status": "needs_input",
            "missing_decisions": ["API authentication strategy"],
            "modules": [],
            "interfaces": [],
            "apis": [],
            "persistence": {},
            "execution_plan": [],
        }
        self.engine.run.return_value = EngineResult(
            output=json.dumps(needs_input),
            metadata={"stub": True},
            provider="test",
            model_alias="architecture-primary",
        )

        self.agent.handle(self.task)

        self.engine.run.assert_called_once()
        self.task_result_writer.write.assert_called_once()
        self.architecture_artifact_writer.write.assert_called_once()
        self.assertEqual(
            self.task_status_writer.update_status.call_args_list,
            [
                call(self.task.id, TaskStatus.IN_PROGRESS),
                call(self.task.id, TaskStatus.NEEDS_INPUT),
            ],
        )
        self.assertEqual(self.task.status, "needs_input")

    def test_rejects_each_project_authority_mismatch(self):
        mismatches = {
            "backend_stack": {"language": "Go"},
            "backend_architecture": {"style": "microservices"},
            "frontend_stack": {"framework": "Vue"},
            "frontend_architecture": {"pattern": "MVC"},
            "infrastructure": {"cloud": "AWS"},
            "technical_constraints": ["Use Java 25"],
        }

        for field_name, replacement in mismatches.items():
            with self.subTest(field=field_name):
                payload = {**self.architecture_output, field_name: replacement}
                artifact = ArchitectureArtifact.from_json(
                    json.dumps(payload)
                )

                with self.assertRaisesRegex(
                    ArchitectureArtifactValidationError,
                    field_name,
                ):
                    self.agent._validate_project_authority(
                        self.project,
                        artifact,
                    )

    def test_project_authority_mismatch_fails_before_persistence(self):
        self.architecture_output["infrastructure"] = {"cloud": "AWS"}
        self.engine.run.return_value = EngineResult(
            output=json.dumps(self.architecture_output),
            metadata={},
            provider="test",
            model_alias="architecture-primary",
        )

        with self.assertRaises(ArchitectureArtifactValidationError):
            self.agent.handle(self.task)

        self.task_result_writer.write.assert_not_called()
        self.architecture_artifact_writer.write.assert_not_called()
        self.assertEqual(
            self.task_status_writer.update_status.call_args_list,
            [
                call(self.task.id, TaskStatus.IN_PROGRESS),
                call(self.task.id, TaskStatus.FAILED),
            ],
        )

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
        self.task_result_writer.write.assert_not_called()
        self.architecture_artifact_writer.write.assert_not_called()
        self.assertEqual(
            self.task_status_writer.update_status.call_args_list,
            [
                call(self.task.id, TaskStatus.IN_PROGRESS),
                call(self.task.id, TaskStatus.FAILED),
            ],
        )
        self.assertEqual(self.task.status, "failed")

    def test_marks_failed_and_propagates_result_persistence_error(self):
        persistence_error = RuntimeError("result persistence failed")
        self.task_result_writer.write.side_effect = persistence_error

        with self.assertRaises(RuntimeError) as raised:
            self.agent.handle(self.task)

        self.assertIs(raised.exception, persistence_error)
        self.engine.run.assert_called_once()
        self.task_result_writer.write.assert_called_once()
        self.architecture_artifact_writer.write.assert_not_called()
        self.assertEqual(
            self.task_status_writer.update_status.call_args_list,
            [
                call(self.task.id, TaskStatus.IN_PROGRESS),
                call(self.task.id, TaskStatus.FAILED),
            ],
        )
        self.assertEqual(self.task.status, "failed")

    def test_rejects_invalid_architecture_before_any_persistence(self):
        self.engine_result = EngineResult(
            output="```json\n{}\n```",
            metadata={},
            provider="test",
            model_alias="architecture-primary",
        )
        self.engine.run.return_value = self.engine_result

        with self.assertRaises(ArchitectureArtifactValidationError):
            self.agent.handle(self.task)

        self.task_result_writer.write.assert_not_called()
        self.architecture_artifact_writer.write.assert_not_called()
        self.assertEqual(
            self.task_status_writer.update_status.call_args_list,
            [
                call(self.task.id, TaskStatus.IN_PROGRESS),
                call(self.task.id, TaskStatus.FAILED),
            ],
        )
        self.assertEqual(self.task.status, "failed")

    def test_marks_failed_when_architecture_artifact_write_fails(self):
        workspace_error = RuntimeError("workspace unavailable")
        self.architecture_artifact_writer.write.side_effect = workspace_error

        with self.assertRaises(RuntimeError) as raised:
            self.agent.handle(self.task)

        self.assertIs(raised.exception, workspace_error)
        self.task_result_writer.write.assert_called_once()
        self.architecture_artifact_writer.write.assert_called_once()
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
        self.task_result_writer.write.assert_not_called()
        self.architecture_artifact_writer.write.assert_not_called()
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
        self.architecture_artifact_writer.write.assert_not_called()
        self.assertEqual(self.task.status, "in_progress")


if __name__ == "__main__":
    unittest.main()
