import json
import unittest
from unittest.mock import Mock, call
from uuid import UUID

from agents.backend.agent import (
    BackendAgent,
    BackendProjectNotFoundError,
)
from core.contracts.context import ContextProvider
from core.contracts.engines import AgentEngine, EngineResult
from core.contracts.repositories import ProjectReader, TaskStatusWriter
from core.contracts.results import TaskResultWriter
from core.contracts.workspaces import ProjectCodeWorkspace
from core.models.architecture import ArchitectureArtifact
from core.models.backend_generation import (
    BackendGenerationValidationError,
)
from core.models.project import Project
from core.models.task import AgentRole, Task, TaskStatus
from core.models.task_result import TaskExecutionResult
from core.models.workspace import WorkspaceFile
from tests.architecture_fixtures import usable_architecture_sections


class BackendAgentTests(unittest.TestCase):
    def setUp(self):
        self.task = Task(
            id=UUID("2cb5fe26-74a0-48f1-a989-aaf9b41b343b"),
            project_id=UUID("93de5ea5-729a-4c5e-8dc3-443165ed516b"),
            title="Implement backend",
            description="Generate the backend code",
            agent=AgentRole.BACKEND,
        )
        self.project = Project(
            id=self.task.project_id,
            name="Order Service",
            description="Order management",
            backend_stack={
                "language": "Python",
                "framework": "FastAPI",
                "libraries": ["fastapi"],
                "persistence_library": "psycopg",
            },
            backend_architecture={
                "style": "Hexagonal",
                "patterns": ["DDD", "CQRS"],
            },
            frontend_stack={"framework": "React"},
            frontend_architecture={"style": "components"},
            infrastructure={"database": "PostgreSQL"},
            technical_constraints=[
                "Apply all five SOLID principles",
                "The domain must not depend on frameworks",
            ],
        )
        self.architecture = self._architecture()
        self.generation_output = self._generation_output()
        self.engine_result = EngineResult(
            output=json.dumps(self.generation_output),
            metadata={"tokens": 412},
            provider="litellm",
            model_alias="qwen-coder",
        )
        self.task_status_writer = Mock(spec=TaskStatusWriter)
        self.task_result_writer = Mock(spec=TaskResultWriter)
        self.project_reader = Mock(spec=ProjectReader)
        self.project_reader.find_by_id.return_value = self.project
        self.workspace = Mock(spec=ProjectCodeWorkspace)
        self.workspace.read_architecture.return_value = self.architecture
        self.existing_files = [
            WorkspaceFile("backend/README.md", "Existing backend\n")
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
        self.agent = BackendAgent(
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
            **usable_architecture_sections(
                persistence_technology="PostgreSQL"
            ),
        }
        payload.update(overrides)
        return ArchitectureArtifact.from_json(json.dumps(payload))

    def _generation_output(self, **overrides):
        payload = {
            "version": 1,
            "status": "complete",
            "missing_decisions": [],
            "backend_stack": self.project.backend_stack,
            "backend_architecture": self.project.backend_architecture,
            "infrastructure": self.project.infrastructure,
            "technical_constraints": self.project.technical_constraints,
            "files": [
                {
                    "path": "backend/domain/project_record.py",
                    "content": (
                        "from dataclasses import dataclass\n\n"
                        "@dataclass(frozen=True)\n"
                        "class ProjectRecord:\n"
                        "    project_id: str\n"
                        "    status: str\n"
                    ),
                },
                {
                    "path": "backend/application/project_reader.py",
                    "content": (
                        "from typing import Protocol\n\n"
                        "from backend.domain.project_record import "
                        "ProjectRecord\n\n"
                        "class ProjectReader(Protocol):\n"
                        "    def read_project(self, project_id: str) -> "
                        "ProjectRecord:\n"
                        "        return ProjectRecord(project_id, "
                        "'declared')\n"
                    ),
                },
                {
                    "path": (
                        "backend/infrastructure/postgres/"
                        "project_reader.py"
                    ),
                    "content": (
                        "import psycopg\n\n"
                        "from backend.domain.project_record import "
                        "ProjectRecord\n\n"
                        "class PostgresProjectReader:\n"
                        "    def __init__(self, connection: "
                        "psycopg.Connection):\n"
                        "        self.connection = connection\n\n"
                        "    def read_project(self, project_id: str) -> "
                        "ProjectRecord:\n"
                        "        with self.connection.cursor() as cursor:\n"
                        "            cursor.execute('SELECT status FROM "
                        "projects WHERE id = %s', (project_id,))\n"
                        "            row = cursor.fetchone()\n"
                        "        if row is None:\n"
                        "            raise LookupError(project_id)\n"
                        "        return ProjectRecord(project_id, row[0])\n"
                    ),
                },
                {
                    "path": "backend/api.py",
                    "content": (
                        "from fastapi import FastAPI\n\n"
                        "app = FastAPI()\n\n"
                        "@app.get('/projects/{project_id}')\n"
                        "def get_project(project_id: str) -> dict[str, str]:\n"
                        "    return {'project_id': project_id, "
                        "'status': 'available'}\n"
                    ),
                },
            ],
            "implementation": {
                "modules": ["backend"],
                "interfaces": ["ProjectReader"],
                "apis": ["project-api"],
                "persistence_stores": ["primary"],
                "dependencies": ["fastapi", "psycopg"],
                "file_modules": {
                    "backend/domain/project_record.py": "backend",
                    "backend/application/project_reader.py": "backend",
                    (
                        "backend/infrastructure/postgres/project_reader.py"
                    ): "backend",
                    "backend/api.py": "backend",
                },
            },
            "summary": "Implemented order creation",
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
        self.assertEqual(persisted.agent, "backend")
        self.assertEqual(persisted.provider, "litellm")
        self.assertEqual(persisted.model_alias, "qwen-coder")
        self.assertEqual(persisted.metadata, {"tokens": 412})
        self.assertEqual(
            json.loads(persisted.output),
            self.generation_output,
        )
        written_files = self.workspace.write_files.call_args.args[1]
        self.assertEqual(len(written_files), 4)
        self.assertEqual(self.task.status, TaskStatus.COMPLETED)

    def test_retries_once_with_validation_feedback_before_persisting(self):
        invalid_output = json.loads(json.dumps(self.generation_output))
        invalid_output["files"][0]["path"] = "../outside.py"
        self.engine.run.side_effect = [
            EngineResult(
                output=json.dumps(invalid_output),
                metadata={"attempt": 1},
                provider="litellm",
                model_alias="qwen-coder",
            ),
            self.engine_result,
        ]

        self.agent.handle(self.task)

        self.assertEqual(self.engine.run.call_count, 2)
        retry_context = self.engine.run.call_args.kwargs["context"]
        feedback = retry_context["backend_validation_feedback"]
        self.assertEqual(feedback["attempt"], 1)
        self.assertIn("unsafe path", feedback["validation_error"])
        self.assertEqual(
            json.loads(feedback["previous_output"]),
            invalid_output,
        )
        self.task_result_writer.write.assert_called_once()
        self.workspace.write_files.assert_called_once()
        self.assertEqual(self.task.status, TaskStatus.COMPLETED)

    def test_engine_context_derives_every_rule_from_the_project(self):
        self.agent.handle(self.task)

        context = self.engine.run.call_args.kwargs["context"]
        implementation = context["backend_implementation"]
        self.assertEqual(
            implementation["architecture_artifact"],
            self.architecture.to_dict(),
        )
        self.assertEqual(
            implementation["existing_files"],
            [
                {
                    "path": "backend/README.md",
                    "content": "Existing backend\n",
                }
            ],
        )
        self.assertEqual(
            implementation["module_packages"],
            {"backend": "backend/backend"},
        )
        self.assertEqual(len(implementation["python_generation_rules"]), 6)
        self.assertIn(
            "dataclasses",
            implementation["python_generation_rules"][0],
        )
        self.assertIn(
            "persistence library",
            implementation["python_generation_rules"][1],
        )
        self.assertIn(
            "never psycopg2",
            implementation["python_generation_rules"][2],
        )
        self.assertIn(
            "FastAPI route parameters",
            implementation["python_generation_rules"][4],
        )
        self.assertIn(
            "PEP 604",
            implementation["python_generation_rules"][5],
        )
        requirements = {
            item["source"]: item["requirement"]
            for item in implementation["mandatory_rules"]
        }
        self.assertEqual(
            json.loads(requirements["backend_stack"]),
            self.project.backend_stack,
        )
        self.assertEqual(
            json.loads(requirements["backend_architecture"]),
            self.project.backend_architecture,
        )
        self.assertEqual(
            requirements["technical_constraints[0]"],
            "Apply all five SOLID principles",
        )
        for field_name in (
            "backend_stack",
            "backend_architecture",
            "infrastructure",
            "technical_constraints",
        ):
            self.assertEqual(
                context["output_contract"]["properties"][field_name][
                    "const"
                ],
                getattr(self.project, field_name),
            )
        contract = context["output_contract"]["properties"]
        self.assertIn(
            "exact dotted form",
            context["output_contract"]["instruction"],
        )
        implementation_contract = contract["implementation"]["properties"]
        self.assertEqual(
            implementation_contract["modules"]["const"],
            ["backend"],
        )
        self.assertEqual(
            implementation_contract["interfaces"]["const"],
            ["ProjectReader"],
        )
        self.assertEqual(
            implementation_contract["apis"]["const"],
            ["project-api"],
        )
        self.assertEqual(
            implementation_contract["persistence_stores"]["const"],
            ["primary"],
        )
        self.assertEqual(
            implementation_contract["dependencies"]["const"],
            ["fastapi", "psycopg"],
        )
        self.assertEqual(
            implementation_contract["file_modules"],
            {"type": "object", "const": {}},
        )
        self.assertEqual(
            contract["files"]["items"]["properties"]["path"]["pattern"],
            r"^(?:backend/backend)/.+$",
        )

    def test_does_not_add_architectural_patterns_from_another_project(self):
        self.project.backend_architecture = {"style": "layered"}
        self.project.technical_constraints = ["Keep functions small"]
        self.architecture = self._architecture()
        self.workspace.read_architecture.return_value = self.architecture
        self.generation_output = self._generation_output()
        self.engine.run.return_value = EngineResult(
            output=json.dumps(self.generation_output),
            metadata={},
            provider="test",
        )

        self.agent.handle(self.task)

        serialized_context = json.dumps(
            self.engine.run.call_args.kwargs["context"]
        )
        for forbidden in ("Hexagonal", "DDD", "CQRS", "SOLID"):
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
            missing_decisions=["API authentication strategy"],
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
            ["API authentication strategy"],
        )
        self.assertEqual(self.task.status, TaskStatus.NEEDS_INPUT)

    def test_empty_required_backend_decision_does_not_invoke_engine(self):
        self.project.infrastructure = {}
        self.workspace.read_architecture.return_value = self._architecture()

        self.agent.handle(self.task)

        self.engine.run.assert_not_called()
        self.workspace.write_files.assert_not_called()
        payload = json.loads(
            self.task_result_writer.write.call_args.args[0].output
        )
        self.assertEqual(payload["missing_decisions"], ["infrastructure"])
        self.assertEqual(self.task.status, TaskStatus.NEEDS_INPUT)

    def test_rejects_stale_architecture_before_engine_and_persistence(self):
        self.workspace.read_architecture.return_value = self._architecture(
            infrastructure={"cloud": "AWS"}
        )

        with self.assertRaisesRegex(
            BackendGenerationValidationError,
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
            backend_stack={"language": "Go", "framework": "Gin"}
        )
        self.engine.run.return_value = EngineResult(
            output=json.dumps(output),
            metadata={},
            provider="test",
        )

        with self.assertRaisesRegex(
            BackendGenerationValidationError,
            "backend_stack",
        ):
            self.agent.handle(self.task)

        self.task_result_writer.write.assert_not_called()
        self.workspace.write_files.assert_not_called()
        self.assertEqual(self.task.status, TaskStatus.FAILED)

    def test_engine_needs_input_is_persisted_without_writing_files(self):
        output = self._generation_output(
            status="needs_input",
            missing_decisions=["Order identifier format"],
            files=[],
            implementation={
                "modules": [],
                "interfaces": [],
                "apis": [],
                "persistence_stores": [],
                "dependencies": [],
                "file_modules": {},
            },
            summary="Order identifier decision is required",
        )
        self.engine.run.return_value = EngineResult(
            output=json.dumps(output),
            metadata={"tokens": 100},
            provider="litellm",
            model_alias="qwen-coder",
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

    def test_missing_persistence_library_requires_input_before_engine(self):
        self.project.backend_stack.pop("persistence_library")
        self.architecture = self._architecture()
        self.workspace.read_architecture.return_value = self.architecture

        self.agent.handle(self.task)

        self.engine.run.assert_not_called()
        self.workspace.write_files.assert_not_called()
        persisted = self.task_result_writer.write.call_args.args[0]
        self.assertEqual(
            json.loads(persisted.output)["missing_decisions"],
            ["backend_persistence_library"],
        )
        self.assertEqual(self.task.status, TaskStatus.NEEDS_INPUT)

    def test_rejects_incomplete_or_invalid_python_before_persistence(self):
        invalid_contents = (
            "class ProjectRecord:\n    pass\n",
            "class ProjectRecord(:\n",
            "from missing.module import ProjectRecord\n",
            "missing_reference()\n",
        )
        for content in invalid_contents:
            with self.subTest(content=content):
                output = self._generation_output()
                output["files"][0]["content"] = content
                self.engine.run.return_value = EngineResult(
                    output=json.dumps(output),
                    metadata={},
                    provider="test",
                )

                with self.assertRaises(BackendGenerationValidationError):
                    self.agent.handle(self.task)

                self.task_result_writer.write.assert_not_called()
                self.workspace.write_files.assert_not_called()
                self.task_status_writer.reset_mock()
                self.task.status = TaskStatus.PENDING

    def test_rejects_incompatible_internal_constructor_before_persistence(
        self,
    ):
        output = self._generation_output()
        output["files"][0]["content"] = (
            "class ProjectRecord:\n"
            "    project_id: str\n"
            "    status: str\n"
        )
        self.engine.run.return_value = EngineResult(
            output=json.dumps(output),
            metadata={},
            provider="test",
        )

        with self.assertRaisesRegex(
            BackendGenerationValidationError,
            "incompatible internal constructor ProjectRecord",
        ):
            self.agent.handle(self.task)

        self.task_result_writer.write.assert_not_called()
        self.workspace.write_files.assert_not_called()
        self.assertEqual(self.task.status, TaskStatus.FAILED)

    def test_rejects_plain_service_class_as_fastapi_route_parameter(self):
        output = self._generation_output()
        output["files"][-1]["content"] = (
            "from fastapi import FastAPI\n\n"
            "app = FastAPI()\n\n"
            "class ProjectReader:\n"
            "    def read_project(self, project_id: str) -> str:\n"
            "        return project_id\n\n"
            "@app.get('/projects/{project_id}')\n"
            "def get_project(project_id: str, reader: ProjectReader = None) "
            "-> dict[str, str]:\n"
            "    return {'project_id': reader.read_project(project_id), "
            "'status': 'available'}\n"
        )
        self.engine.run.return_value = EngineResult(
            output=json.dumps(output),
            metadata={},
            provider="test",
        )

        with self.assertRaisesRegex(
            BackendGenerationValidationError,
            "FastAPI route parameter reader",
        ):
            self.agent.handle(self.task)

        self.assertEqual(self.engine.run.call_count, 3)
        self.task_result_writer.write.assert_not_called()
        self.workspace.write_files.assert_not_called()
        self.assertEqual(self.task.status, TaskStatus.FAILED)

    def test_rejects_manifest_or_api_that_contradicts_architecture(self):
        outputs = []
        wrong_module = self._generation_output()
        wrong_module["implementation"]["modules"][0] = "other"
        wrong_module["implementation"]["file_modules"] = {
            path: "other"
            for path in wrong_module["implementation"]["file_modules"]
        }
        outputs.append(wrong_module)

        wrong_api = self._generation_output()
        wrong_api["files"][-1]["content"] = wrong_api["files"][-1][
            "content"
        ].replace("/projects/{project_id}", "/other")
        outputs.append(wrong_api)

        undeclared_dependency = self._generation_output()
        undeclared_dependency["files"][-1]["content"] = (
            "import requests\n"
            + undeclared_dependency["files"][-1]["content"]
        )
        undeclared_dependency["implementation"]["dependencies"].append(
            "requests"
        )
        outputs.append(undeclared_dependency)

        for output in outputs:
            with self.subTest(output=output):
                self.engine.run.return_value = EngineResult(
                    output=json.dumps(output),
                    metadata={},
                    provider="test",
                )

                with self.assertRaises(BackendGenerationValidationError):
                    self.agent.handle(self.task)

                self.task_result_writer.write.assert_not_called()
                self.workspace.write_files.assert_not_called()
                self.task_status_writer.reset_mock()
                self.task.status = TaskStatus.PENDING

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

    def test_unsafe_engine_path_fails_before_persistence(self):
        output = self._generation_output()
        output["files"][0]["path"] = "../../outside.py"
        self.engine.run.return_value = EngineResult(
            output=json.dumps(output),
            metadata={},
            provider="test",
        )

        with self.assertRaises(BackendGenerationValidationError):
            self.agent.handle(self.task)

        self.task_result_writer.write.assert_not_called()
        self.workspace.write_files.assert_not_called()
        self.assertEqual(self.task.status, TaskStatus.FAILED)

    def test_unknown_project_fails_without_engine_or_workspace_reads(self):
        self.project_reader.find_by_id.return_value = None

        with self.assertRaisesRegex(
            BackendProjectNotFoundError,
            str(self.task.project_id),
        ):
            self.agent.handle(self.task)

        self.workspace.read_architecture.assert_not_called()
        self.workspace.read_files.assert_not_called()
        self.engine.run.assert_not_called()
        self.task_result_writer.write.assert_not_called()
        self.assertEqual(self.task.status, TaskStatus.FAILED)


if __name__ == "__main__":
    unittest.main()
