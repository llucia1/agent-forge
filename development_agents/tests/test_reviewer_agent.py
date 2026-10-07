import inspect
import json
import unittest
from unittest.mock import Mock, call
from uuid import UUID

from agents.reviewer.agent import (
    ReviewerAgent,
    ReviewProjectNotFoundError,
)
from core.contracts.context import ContextProvider
from core.contracts.engines import AgentEngine, EngineResult
from core.contracts.repositories import ProjectReader, TaskStatusWriter
from core.contracts.results import TaskResultWriter
from core.contracts.workspaces import ProjectCodeReader
from core.models.architecture import ArchitectureArtifact
from core.models.project import Project
from core.models.review import ReviewValidationError
from core.models.task import AgentRole, Task, TaskStatus
from core.models.task_result import TaskExecutionResult
from core.models.workspace import ProjectSnapshot, WorkspaceFile
from tests.architecture_fixtures import usable_architecture_sections


EXPECTED_RULES = [
    "project.backend_stack",
    "project.backend_architecture",
    "project.frontend_stack",
    "project.frontend_architecture",
    "project.infrastructure",
    "project.technical_constraints",
    "architecture.modules",
    "architecture.interfaces",
    "architecture.apis",
    "architecture.persistence",
    "architecture.execution_plan",
    "implementation.backend",
    "implementation.frontend",
    "integration.backend_frontend",
]


class ReviewerAgentTests(unittest.TestCase):
    def setUp(self):
        self.task = Task(
            id=UUID("e8d43f59-c694-4446-817d-76faa6e51fe9"),
            project_id=UUID("93de5ea5-729a-4c5e-8dc3-443165ed516b"),
            title="Review generated project",
            description="Review architecture, quality and integration",
            agent=AgentRole.REVIEWER,
        )
        self.project = Project(
            id=self.task.project_id,
            name="Generated Project",
            description="A project using its own technical decisions",
            backend_stack={"language": "ServerLang"},
            backend_architecture={"style": "ProjectServerStyle"},
            frontend_stack={"language": "ClientLang"},
            frontend_architecture={"style": "ProjectClientStyle"},
            infrastructure={"runtime": "ProjectRuntime"},
            technical_constraints=[
                "Project-defined dependency constraint",
            ],
        )
        self.architecture = self._architecture()
        self.workspace_files = [
            WorkspaceFile(
                "backend/src/service.code",
                "server implementation\n",
            ),
            WorkspaceFile(
                "frontend/src/view.code",
                "client implementation\n",
            ),
            WorkspaceFile(
                "project.conf",
                "shared configuration\n",
            ),
        ]
        self.review_output = self._review_output()
        self.engine_result = EngineResult(
            output=json.dumps(self.review_output),
            metadata={"tokens": 275},
            provider="litellm",
            model_alias="project-review-model",
        )
        self.task_status_writer = Mock(spec=TaskStatusWriter)
        self.task_result_writer = Mock(spec=TaskResultWriter)
        self.project_reader = Mock(spec=ProjectReader)
        self.project_reader.find_by_id.return_value = self.project
        self.workspace_reader = Mock(spec=ProjectCodeReader)
        self.workspace_reader.read_architecture.return_value = (
            self.architecture
        )
        self.workspace_reader.read_files.return_value = self.workspace_files
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
        self.agent = ReviewerAgent(
            task_status_writer=self.task_status_writer,
            task_result_writer=self.task_result_writer,
            project_reader=self.project_reader,
            workspace_reader=self.workspace_reader,
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
            **usable_architecture_sections(),
        }
        payload.update(overrides)
        return ArchitectureArtifact.from_json(json.dumps(payload))

    def _baseline(self):
        return {
            "backend_stack": self.project.backend_stack,
            "backend_architecture": self.project.backend_architecture,
            "frontend_stack": self.project.frontend_stack,
            "frontend_architecture": self.project.frontend_architecture,
            "infrastructure": self.project.infrastructure,
            "technical_constraints": self.project.technical_constraints,
        }

    def _review_output(self, **overrides):
        payload = {
            "version": 1,
            "status": "changes_required",
            "summary": "One project rule needs correction",
            "technical_baseline": self._baseline(),
            "findings": [
                {
                    "id": "REV-001",
                    "severity": "high",
                    "scope": "backend",
                    "path": "backend/src/service.code",
                    "rule": "project.backend_architecture",
                    "message": "The declared structure is not implemented",
                    "suggested_action": (
                        "Align the file responsibilities with the Project"
                    ),
                }
            ],
            "checked_rules": list(EXPECTED_RULES),
            "missing_decisions": [],
        }
        payload.update(overrides)
        return payload

    def test_receives_only_the_read_only_workspace_contract(self):
        signature = inspect.signature(ReviewerAgent.__init__)

        self.assertIs(
            signature.parameters["workspace_reader"].annotation,
            ProjectCodeReader,
        )
        self.assertNotIn("workspace", signature.parameters)
        self.assertFalse(hasattr(self.workspace_reader, "write_files"))

    def test_persists_review_then_completes_without_writing_files(self):
        events = []
        self.task_status_writer.update_status.side_effect = (
            lambda task_id, status: events.append(str(status))
        )
        self.workspace_reader.read_architecture.side_effect = (
            lambda project_id: events.append("architecture")
            or self.architecture
        )
        self.workspace_reader.read_files.side_effect = (
            lambda project_id: events.append("read_files")
            or self.workspace_files
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
                "completed",
            ],
        )
        persisted = self.task_result_writer.write.call_args.args[0]
        self.assertIsInstance(persisted, TaskExecutionResult)
        self.assertEqual(persisted.task_id, self.task.id)
        self.assertEqual(persisted.project_id, self.task.project_id)
        self.assertEqual(persisted.agent, "reviewer")
        self.assertEqual(persisted.provider, "litellm")
        self.assertEqual(persisted.model_alias, "project-review-model")
        self.assertEqual(
            persisted.metadata,
            {
                "tokens": 275,
                "workspace_fingerprint": ProjectSnapshot.create(
                    self.architecture,
                    self.workspace_files,
                ).fingerprint,
            },
        )
        self.assertEqual(json.loads(persisted.output), self.review_output)
        self.assertEqual(self.task.status, TaskStatus.COMPLETED)

    def test_approved_review_also_completes(self):
        output = self._review_output(
            status="approved",
            findings=[],
            summary="The generated project follows its declared rules",
        )
        self.engine.run.return_value = EngineResult(
            output=json.dumps(output),
            metadata={},
            provider="test",
        )

        self.agent.handle(self.task)

        self.assertEqual(self.task.status, TaskStatus.COMPLETED)

    def test_context_uses_exact_catalog_and_classifies_read_files(self):
        self.agent.handle(self.task)

        context = self.engine.run.call_args.kwargs["context"]
        review_context = context["review"]
        self.assertEqual(
            [rule["id"] for rule in review_context["rule_catalog"]],
            EXPECTED_RULES,
        )
        self.assertEqual(
            review_context["backend_files"],
            [
                {
                    "path": "backend/src/service.code",
                    "content": "server implementation\n",
                }
            ],
        )
        self.assertEqual(
            review_context["frontend_files"],
            [
                {
                    "path": "frontend/src/view.code",
                    "content": "client implementation\n",
                }
            ],
        )
        self.assertEqual(
            review_context["shared_files"],
            [
                {
                    "path": "project.conf",
                    "content": "shared configuration\n",
                }
            ],
        )
        contract = context["output_contract"]
        self.assertEqual(
            contract["properties"]["technical_baseline"]["const"],
            self._baseline(),
        )
        self.assertEqual(
            contract["properties"]["checked_rules"]["const"],
            EXPECTED_RULES,
        )

    def test_does_not_add_unconfigured_patterns_or_technologies(self):
        self.agent.handle(self.task)

        serialized_context = json.dumps(
            self.engine.run.call_args.kwargs["context"]
        )
        for forbidden in (
            "Hexagonal",
            "DDD",
            "CQRS",
            "SOLID",
            "React",
            "Vue",
            "Angular",
            "Svelte",
        ):
            self.assertNotIn(forbidden, serialized_context)

    def test_missing_architecture_is_deterministic_needs_input(self):
        self.workspace_reader.read_architecture.return_value = None

        self.agent.handle(self.task)

        self.workspace_reader.read_files.assert_not_called()
        self.context_provider.build.assert_not_called()
        self.engine.run.assert_not_called()
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

    def test_needs_input_architecture_does_not_invoke_engine(self):
        self.workspace_reader.read_architecture.return_value = (
            self._architecture(
                status="needs_input",
                missing_decisions=["project integration decision"],
                modules=[],
                interfaces=[],
                apis=[],
                persistence={},
                execution_plan=[],
            )
        )

        self.agent.handle(self.task)

        self.workspace_reader.read_files.assert_not_called()
        self.engine.run.assert_not_called()
        payload = json.loads(
            self.task_result_writer.write.call_args.args[0].output
        )
        self.assertEqual(
            payload["missing_decisions"],
            ["project integration decision"],
        )
        self.assertEqual(self.task.status, TaskStatus.NEEDS_INPUT)

    def test_missing_project_decision_does_not_invoke_engine(self):
        self.project.infrastructure = {}
        self.workspace_reader.read_architecture.return_value = (
            self._architecture()
        )

        self.agent.handle(self.task)

        self.workspace_reader.read_files.assert_not_called()
        self.engine.run.assert_not_called()
        payload = json.loads(
            self.task_result_writer.write.call_args.args[0].output
        )
        self.assertEqual(payload["missing_decisions"], ["infrastructure"])
        self.assertEqual(self.task.status, TaskStatus.NEEDS_INPUT)

    def test_empty_constraints_are_authoritative_and_not_missing(self):
        self.project.technical_constraints = []
        self.workspace_reader.read_architecture.return_value = (
            self._architecture()
        )
        output = self._review_output()
        output["technical_baseline"]["technical_constraints"] = []
        self.engine.run.return_value = EngineResult(
            output=json.dumps(output),
            metadata={},
            provider="test",
        )

        self.agent.handle(self.task)

        self.engine.run.assert_called_once()
        self.assertEqual(self.task.status, TaskStatus.COMPLETED)

    def test_missing_backend_or_frontend_code_is_needs_input(self):
        cases = (
            (
                [self.workspace_files[1]],
                ["backend_implementation"],
            ),
            (
                [self.workspace_files[0]],
                ["frontend_implementation"],
            ),
        )
        for files, expected in cases:
            with self.subTest(expected=expected):
                self.setUp()
                self.workspace_reader.read_files.return_value = files

                self.agent.handle(self.task)

                self.engine.run.assert_not_called()
                payload = json.loads(
                    self.task_result_writer.write.call_args.args[0].output
                )
                self.assertEqual(payload["missing_decisions"], expected)
                self.assertEqual(self.task.status, TaskStatus.NEEDS_INPUT)

    def test_project_architecture_contradiction_fails_before_engine(self):
        self.workspace_reader.read_architecture.return_value = (
            self._architecture(
                backend_architecture={"style": "ContradictoryStyle"}
            )
        )

        with self.assertRaisesRegex(
            ReviewValidationError,
            "backend_architecture",
        ):
            self.agent.handle(self.task)

        self.workspace_reader.read_files.assert_not_called()
        self.engine.run.assert_not_called()
        self.task_result_writer.write.assert_not_called()
        self.assertEqual(self.task.status, TaskStatus.FAILED)

    def test_rejects_baseline_that_contradicts_project(self):
        output = self._review_output()
        output["technical_baseline"]["frontend_stack"] = {
            "language": "UnapprovedLanguage"
        }
        self.engine.run.return_value = EngineResult(
            output=json.dumps(output),
            metadata={},
            provider="test",
        )

        with self.assertRaisesRegex(
            ReviewValidationError,
            "technical_baseline",
        ):
            self.agent.handle(self.task)

        self.task_result_writer.write.assert_not_called()
        self.assertEqual(self.task.status, TaskStatus.FAILED)

    def test_rejects_checked_rules_that_do_not_match_catalog(self):
        output = self._review_output(
            checked_rules=EXPECTED_RULES[:-1]
        )
        self.engine.run.return_value = EngineResult(
            output=json.dumps(output),
            metadata={},
            provider="test",
        )

        with self.assertRaisesRegex(
            ReviewValidationError,
            "checked_rules",
        ):
            self.agent.handle(self.task)

        self.task_result_writer.write.assert_not_called()
        self.assertEqual(self.task.status, TaskStatus.FAILED)

    def test_rejects_finding_path_not_present_in_read_files(self):
        output = self._review_output()
        output["findings"][0]["path"] = "backend/src/not-read.code"
        self.engine.run.return_value = EngineResult(
            output=json.dumps(output),
            metadata={},
            provider="test",
        )

        with self.assertRaisesRegex(
            ReviewValidationError,
            "unread workspace paths",
        ):
            self.agent.handle(self.task)

        self.task_result_writer.write.assert_not_called()
        self.assertEqual(self.task.status, TaskStatus.FAILED)

    def test_rejects_finding_rule_outside_catalog(self):
        output = self._review_output()
        output["findings"][0]["rule"] = "unapproved.rule"
        self.engine.run.return_value = EngineResult(
            output=json.dumps(output),
            metadata={},
            provider="test",
        )

        with self.assertRaisesRegex(
            ReviewValidationError,
            "unknown rules",
        ):
            self.agent.handle(self.task)

        self.task_result_writer.write.assert_not_called()
        self.assertEqual(self.task.status, TaskStatus.FAILED)

    def test_engine_needs_input_is_persisted_and_sets_needs_input(self):
        output = self._review_output(
            status="needs_input",
            findings=[],
            missing_decisions=["runtime integration contract"],
            summary="The runtime integration contract is missing",
        )
        self.engine.run.return_value = EngineResult(
            output=json.dumps(output),
            metadata={"tokens": 100},
            provider="litellm",
            model_alias="project-review-model",
        )

        self.agent.handle(self.task)

        self.task_result_writer.write.assert_called_once()
        self.assertEqual(
            self.task_status_writer.update_status.call_args_list,
            [
                call(self.task.id, TaskStatus.IN_PROGRESS),
                call(self.task.id, TaskStatus.NEEDS_INPUT),
            ],
        )
        self.assertEqual(self.task.status, TaskStatus.NEEDS_INPUT)

    def test_persistence_failure_marks_failed_and_propagates(self):
        self.task_result_writer.write.side_effect = RuntimeError(
            "database unavailable"
        )

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            self.agent.handle(self.task)

        self.assertEqual(self.task.status, TaskStatus.FAILED)

    def test_unknown_project_fails_without_workspace_or_engine(self):
        self.project_reader.find_by_id.return_value = None

        with self.assertRaisesRegex(
            ReviewProjectNotFoundError,
            str(self.task.project_id),
        ):
            self.agent.handle(self.task)

        self.workspace_reader.read_architecture.assert_not_called()
        self.workspace_reader.read_files.assert_not_called()
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
