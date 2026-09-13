import json
import unittest
from unittest.mock import Mock, call
from uuid import UUID

from agents.frontend.agent import (
    FrontendAgent,
    FrontendProjectNotFoundError,
)
from core.contracts.context import ContextProvider
from core.contracts.engines import AgentEngine, EngineResult
from core.contracts.repositories import ProjectReader, TaskStatusWriter
from core.contracts.results import TaskResultWriter
from core.contracts.workspaces import ProjectCodeWorkspace
from core.models.architecture import ArchitectureArtifact
from core.models.frontend_generation import (
    FrontendGenerationValidationError,
)
from core.models.project import Project
from core.models.task import AgentRole, Task, TaskStatus
from core.models.task_result import TaskExecutionResult
from core.models.workspace import WorkspaceFile


class FrontendAgentTests(unittest.TestCase):
    def setUp(self):
        self.task = Task(
            id=UUID("7f627b91-f5a2-48b8-889d-849bcb35475f"),
            project_id=UUID("93de5ea5-729a-4c5e-8dc3-443165ed516b"),
            title="Implement frontend",
            description="Generate the frontend code",
            agent=AgentRole.FRONTEND,
        )
        self.project = Project(
            id=self.task.project_id,
            name="Order UI",
            description="Order management interface",
            backend_stack={"language": "Python"},
            backend_architecture={"style": "ports"},
            frontend_stack={
                "language": "TypeScript",
                "ui": "ProjectUI",
            },
            frontend_architecture={"style": "component-based"},
            infrastructure={"container": "Docker"},
            technical_constraints=[
                "Keep UI responsibilities separate",
                "Application code depends on declared interfaces",
            ],
        )
        self.architecture = self._architecture()
        self.generation_output = self._generation_output()
        self.engine_result = EngineResult(
            output=json.dumps(self.generation_output),
            metadata={"tokens": 318},
            provider="litellm",
            model_alias="project-code-model",
        )
        self.task_status_writer = Mock(spec=TaskStatusWriter)
        self.task_result_writer = Mock(spec=TaskResultWriter)
        self.project_reader = Mock(spec=ProjectReader)
        self.project_reader.find_by_id.return_value = self.project
        self.workspace = Mock(spec=ProjectCodeWorkspace)
        self.workspace.read_architecture.return_value = self.architecture
        self.existing_files = [
            WorkspaceFile(
                "frontend/src/components/existing.tsx",
                "export const Existing = () => null;\n",
            ),
            WorkspaceFile("backend/app.py", "print('backend')\n"),
            WorkspaceFile(
                "frontend/node_modules/package/index.js",
                "ignored\n",
            ),
        ]
        self.workspace.read_files.return_value = self.existing_files
        self.engine = Mock(spec=AgentEngine)
        self.engine.run.return_value = self.engine_result
        self.context_provider = Mock(spec=ContextProvider)
        self.base_context = {
            "project_memory": {"summary": "AgentForge context"},
            "previous_decisions": [],
            "feedback": [],
            "rag_context": None,
        }
        self.context_provider.build.return_value = self.base_context
        self.agent = FrontendAgent(
            task_status_writer=self.task_status_writer,
            task_result_writer=self.task_result_writer,
            project_reader=self.project_reader,
            workspace=self.workspace,
            engine=self.engine,
            context_provider=self.context_provider,
        )

    def _architecture(self, **overrides):
        payload = {
            "version": 1,
            "status": "complete",
            "missing_decisions": [],
            "backend_stack": self.project.backend_stack,
            "backend_architecture": self.project.backend_architecture,
            "frontend_stack": self.project.frontend_stack,
            "frontend_architecture": self.project.frontend_architecture,
            "infrastructure": self.project.infrastructure,
            "technical_constraints": self.project.technical_constraints,
            "modules": [{"name": "orders-ui"}],
            "interfaces": [{"name": "OrdersApi"}],
            "apis": [{"method": "GET", "path": "/orders"}],
            "persistence": {},
            "execution_plan": [{"step": "Implement orders interface"}],
        }
        payload.update(overrides)
        return ArchitectureArtifact.from_json(json.dumps(payload))

    def _generation_output(self, **overrides):
        payload = {
            "version": 1,
            "status": "complete",
            "missing_decisions": [],
            "frontend_stack": self.project.frontend_stack,
            "frontend_architecture": self.project.frontend_architecture,
            "infrastructure": self.project.infrastructure,
            "technical_constraints": self.project.technical_constraints,
            "files": [
                {
                    "path": "frontend/package.json",
                    "content": (
                        '{"scripts":{"build":"project-build"}}\n'
                    ),
                },
                {
                    "path": "frontend/src/components/order-list.tsx",
                    "content": "export const OrderList = () => null;\n",
                },
            ],
            "summary": "Implemented the order interface",
        }
        payload.update(overrides)
        return payload

    def test_persists_then_writes_multiple_files_then_completes(self):
        events = []
        self.task_status_writer.update_status.side_effect = (
            lambda task_id, status: events.append(str(status))
        )
        self.workspace.read_architecture.side_effect = (
            lambda project_id: events.append("architecture")
            or self.architecture
        )
        self.workspace.read_files.side_effect = (
            lambda project_id: events.append("read_files")
            or self.existing_files
        )
        self.context_provider.build.side_effect = (
            lambda task, project: events.append("context")
            or self.base_context
        )
        self.engine.run.side_effect = (
            lambda **kwargs: events.append("engine") or self.engine_result
        )
        self.task_result_writer.write.side_effect = (
            lambda result: events.append("result")
        )
        self.workspace.write_files.side_effect = (
            lambda project_id, files: events.append("files")
        )

        self.agent.handle(self.task)

        self.assertEqual(
            events,
            [
                "in_progress",
                "architecture",
                "read_files",
                "context",
                "engine",
                "result",
                "files",
                "completed",
            ],
        )
        persisted = self.task_result_writer.write.call_args.args[0]
        self.assertIsInstance(persisted, TaskExecutionResult)
        self.assertEqual(persisted.task_id, self.task.id)
        self.assertEqual(persisted.project_id, self.task.project_id)
        self.assertEqual(persisted.agent, "frontend")
        self.assertEqual(persisted.provider, "litellm")
        self.assertEqual(persisted.model_alias, "project-code-model")
        self.assertEqual(persisted.metadata, {"tokens": 318})
        self.assertEqual(json.loads(persisted.output), self.generation_output)
        self.assertEqual(
            len(self.workspace.write_files.call_args.args[1]),
            2,
        )
        self.assertEqual(self.task.status, TaskStatus.COMPLETED)

    def test_context_derives_rules_and_only_includes_frontend_files(self):
        self.agent.handle(self.task)

        context = self.engine.run.call_args.kwargs["context"]
        implementation = context["frontend_implementation"]
        self.assertEqual(
            implementation["architecture_artifact"],
            self.architecture.to_dict(),
        )
        self.assertEqual(
            implementation["existing_files"],
            [
                {
                    "path": "frontend/src/components/existing.tsx",
                    "content": "export const Existing = () => null;\n",
                }
            ],
        )
        requirements = {
            item["source"]: item["requirement"]
            for item in implementation["mandatory_rules"]
        }
        self.assertEqual(
            json.loads(requirements["frontend_stack"]),
            self.project.frontend_stack,
        )
        self.assertEqual(
            json.loads(requirements["frontend_architecture"]),
            self.project.frontend_architecture,
        )
        for field_name in (
            "frontend_stack",
            "frontend_architecture",
            "infrastructure",
            "technical_constraints",
        ):
            self.assertEqual(
                context["output_contract"]["properties"][field_name][
                    "const"
                ],
                getattr(self.project, field_name),
            )

    def test_does_not_add_technologies_from_other_projects(self):
        self.agent.handle(self.task)

        serialized_context = json.dumps(
            self.engine.run.call_args.kwargs["context"]
        )
        for forbidden in ("React", "Vue", "Angular", "Svelte"):
            self.assertNotIn(forbidden, serialized_context)

    def test_missing_architecture_persists_needs_input_without_engine(self):
        self.workspace.read_architecture.return_value = None

        self.agent.handle(self.task)

        self.workspace.read_files.assert_not_called()
        self.context_provider.build.assert_not_called()
        self.engine.run.assert_not_called()
        self.workspace.write_files.assert_not_called()
        persisted = self.task_result_writer.write.call_args.args[0]
        payload = json.loads(persisted.output)
        self.assertEqual(persisted.provider, "agentforge")
        self.assertIsNone(persisted.model_alias)
        self.assertEqual(payload["status"], "needs_input")
        self.assertEqual(
            payload["missing_decisions"],
            ["architecture_artifact"],
        )
        self.assertEqual(self.task.status, TaskStatus.NEEDS_INPUT)

    def test_needs_input_architecture_does_not_generate_code(self):
        self.workspace.read_architecture.return_value = self._architecture(
            status="needs_input",
            missing_decisions=["navigation behavior"],
            modules=[],
            interfaces=[],
            apis=[],
            persistence={},
            execution_plan=[],
        )

        self.agent.handle(self.task)

        self.workspace.read_files.assert_not_called()
        self.engine.run.assert_not_called()
        self.workspace.write_files.assert_not_called()
        persisted = self.task_result_writer.write.call_args.args[0]
        self.assertEqual(
            json.loads(persisted.output)["missing_decisions"],
            ["navigation behavior"],
        )
        self.assertEqual(self.task.status, TaskStatus.NEEDS_INPUT)

    def test_empty_required_decision_does_not_invoke_engine(self):
        self.project.frontend_architecture = {}
        self.workspace.read_architecture.return_value = self._architecture()

        self.agent.handle(self.task)

        self.engine.run.assert_not_called()
        self.workspace.write_files.assert_not_called()
        payload = json.loads(
            self.task_result_writer.write.call_args.args[0].output
        )
        self.assertEqual(
            payload["missing_decisions"],
            ["frontend_architecture"],
        )
        self.assertEqual(self.task.status, TaskStatus.NEEDS_INPUT)

    def test_empty_technical_constraints_remains_complete(self):
        self.project.technical_constraints = []
        self.workspace.read_architecture.return_value = self._architecture()
        output = self._generation_output(technical_constraints=[])
        self.engine.run.return_value = EngineResult(
            output=json.dumps(output),
            metadata={},
            provider="test",
        )

        self.agent.handle(self.task)

        self.engine.run.assert_called_once()
        self.workspace.write_files.assert_called_once()
        self.assertEqual(self.task.status, TaskStatus.COMPLETED)

    def test_rejects_stale_architecture_before_engine_and_persistence(self):
        self.workspace.read_architecture.return_value = self._architecture(
            infrastructure={"cloud": "UnapprovedCloud"}
        )

        with self.assertRaisesRegex(
            FrontendGenerationValidationError,
            "infrastructure",
        ):
            self.agent.handle(self.task)

        self.workspace.read_files.assert_not_called()
        self.engine.run.assert_not_called()
        self.task_result_writer.write.assert_not_called()
        self.workspace.write_files.assert_not_called()
        self.assertEqual(self.task.status, TaskStatus.FAILED)

    def test_rejects_engine_output_that_substitutes_project_stack(self):
        output = self._generation_output(
            frontend_stack={"language": "OtherLanguage"}
        )
        self.engine.run.return_value = EngineResult(
            output=json.dumps(output),
            metadata={},
            provider="test",
        )

        with self.assertRaisesRegex(
            FrontendGenerationValidationError,
            "frontend_stack",
        ):
            self.agent.handle(self.task)

        self.task_result_writer.write.assert_not_called()
        self.workspace.write_files.assert_not_called()
        self.assertEqual(self.task.status, TaskStatus.FAILED)

    def test_engine_needs_input_is_persisted_without_writing_files(self):
        output = self._generation_output(
            status="needs_input",
            missing_decisions=["route authorization behavior"],
            files=[],
            summary="Route authorization behavior is required",
        )
        self.engine.run.return_value = EngineResult(
            output=json.dumps(output),
            metadata={"tokens": 88},
            provider="litellm",
            model_alias="project-code-model",
        )

        self.agent.handle(self.task)

        self.task_result_writer.write.assert_called_once()
        self.workspace.write_files.assert_not_called()
        self.assertEqual(
            self.task_status_writer.update_status.call_args_list,
            [
                call(self.task.id, TaskStatus.IN_PROGRESS),
                call(self.task.id, TaskStatus.NEEDS_INPUT),
            ],
        )

    def test_result_persistence_failure_prevents_workspace_write(self):
        self.task_result_writer.write.side_effect = RuntimeError(
            "database unavailable"
        )

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            self.agent.handle(self.task)

        self.workspace.write_files.assert_not_called()
        self.assertEqual(self.task.status, TaskStatus.FAILED)

    def test_workspace_failure_occurs_after_result_and_marks_failed(self):
        events = []
        self.task_result_writer.write.side_effect = (
            lambda result: events.append("result")
        )

        def fail_write(project_id, files):
            events.append("files")
            raise RuntimeError("disk unavailable")

        self.workspace.write_files.side_effect = fail_write

        with self.assertRaisesRegex(RuntimeError, "disk unavailable"):
            self.agent.handle(self.task)

        self.assertEqual(events, ["result", "files"])
        self.assertEqual(self.task.status, TaskStatus.FAILED)

    def test_invalid_engine_path_fails_before_persistence(self):
        output = self._generation_output()
        output["files"][0]["path"] = "backend/src/app.ts"
        self.engine.run.return_value = EngineResult(
            output=json.dumps(output),
            metadata={},
            provider="test",
        )

        with self.assertRaises(FrontendGenerationValidationError):
            self.agent.handle(self.task)

        self.task_result_writer.write.assert_not_called()
        self.workspace.write_files.assert_not_called()
        self.assertEqual(self.task.status, TaskStatus.FAILED)

    def test_unknown_project_fails_without_engine_or_workspace_reads(self):
        self.project_reader.find_by_id.return_value = None

        with self.assertRaisesRegex(
            FrontendProjectNotFoundError,
            str(self.task.project_id),
        ):
            self.agent.handle(self.task)

        self.workspace.read_architecture.assert_not_called()
        self.workspace.read_files.assert_not_called()
        self.engine.run.assert_not_called()
        self.task_result_writer.write.assert_not_called()
        self.assertEqual(self.task.status, TaskStatus.FAILED)

    def test_in_progress_failure_attempts_failed_and_propagates(self):
        self.task_status_writer.update_status.side_effect = [
            RuntimeError("status unavailable"),
            None,
        ]

        with self.assertRaisesRegex(RuntimeError, "status unavailable"):
            self.agent.handle(self.task)

        self.assertEqual(
            self.task_status_writer.update_status.call_args_list,
            [
                call(self.task.id, TaskStatus.IN_PROGRESS),
                call(self.task.id, TaskStatus.FAILED),
            ],
        )
        self.assertEqual(self.task.status, TaskStatus.FAILED)


if __name__ == "__main__":
    unittest.main()
