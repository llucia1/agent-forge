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
from core.models.specification import FeatureSpecification, ProjectSpecification
from core.models.review import ReviewStatus, ReviewValidationError
from core.models.task import AgentRole, Task, TaskStatus
from core.models.task_result import TaskExecutionResult
from core.models.workspace import ProjectSnapshot, WorkspaceFile
from tests.architecture_fixtures import usable_architecture_sections
from tests.specification_fixtures import feature_markdown, project_markdown


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
    def test_sdd_review_binds_specification_context_and_fingerprint(self):
        self.task.spec_path = "specs/create-task.md"
        project_spec = ProjectSpecification.from_markdown(project_markdown())
        feature_spec = FeatureSpecification.from_markdown(feature_markdown())
        self.workspace_reader.read_project_specification.return_value = (
            project_spec
        )
        self.workspace_reader.read_specification.return_value = feature_spec
        output = self._expected_output()
        output["checked_rules"].append("spec.functional_contract")
        self.engine.run.return_value = EngineResult(
            output=json.dumps(output),
            metadata={},
            provider="litellm",
            model_alias="review-model",
        )

        self.agent.handle(self.task)

        context = self.engine.run.call_args.kwargs["context"]
        self.assertEqual(
            context["review"]["functional_contract"]["spec_path"],
            self.task.spec_path,
        )
        persisted = self.task_result_writer.write.call_args.args[0]
        self.assertEqual(persisted.metadata["spec_path"], self.task.spec_path)
        self.assertEqual(
            persisted.metadata["spec_fingerprint"],
            feature_spec.fingerprint,
        )

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
            description="Project with an authoritative contract",
            backend_stack={"language": "Python"},
            backend_architecture={"style": "ports"},
            frontend_stack={"language": "JavaScript"},
            frontend_architecture={"style": "component-based"},
            infrastructure={"runtime": "browser"},
            technical_constraints=[],
        )
        self.architecture = self._architecture()
        self.workspace_files = self._valid_workspace()
        self.task_status_writer = Mock(spec=TaskStatusWriter)
        self.task_result_writer = Mock(spec=TaskResultWriter)
        self.project_reader = Mock(spec=ProjectReader)
        self.project_reader.find_by_id.return_value = self.project
        self.workspace_reader = Mock(spec=ProjectCodeReader)
        self.workspace_reader.read_architecture.return_value = self.architecture
        self.workspace_reader.read_files.return_value = self.workspace_files
        self.engine = Mock(spec=AgentEngine)
        self.context_provider = Mock(spec=ContextProvider)
        self.context_provider.build.return_value = {
            "project_memory": {},
            "previous_decisions": [],
            "feedback": [],
            "rag_context": None,
        }
        self.agent = ReviewerAgent(
            task_status_writer=self.task_status_writer,
            task_result_writer=self.task_result_writer,
            project_reader=self.project_reader,
            workspace_reader=self.workspace_reader,
            engine=self.engine,
            context_provider=self.context_provider,
        )
        self._set_engine_to_expected_review()

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

    @staticmethod
    def _valid_workspace():
        return [
            WorkspaceFile(
                "backend/src/service.py",
                (
                    'PROJECT_PATH = "/projects/{project_id}"\n'
                    "def read_project(project_id):\n"
                    '    return {"status": "ready"}\n'
                    "def get_project(project_id):\n"
                    '    method = "GET"\n'
                    "    return read_project(project_id)\n"
                ),
            ),
            WorkspaceFile(
                "frontend/package.json",
                '{"scripts":{"build":"node --check src/app.js"}}\n',
            ),
            WorkspaceFile(
                "frontend/src/app.js",
                (
                    'const PROJECT_PATH = "/projects/{project_id}";\n'
                    "export async function read_project(project_id) {\n"
                    "  return fetch(PROJECT_PATH.replace("
                    '"{project_id}", project_id), {method: "GET"});\n'
                    "}\n"
                    "export const get_project = read_project;\n"
                ),
            ),
        ]

    def _baseline(self):
        return {
            "backend_stack": self.project.backend_stack,
            "backend_architecture": self.project.backend_architecture,
            "frontend_stack": self.project.frontend_stack,
            "frontend_architecture": self.project.frontend_architecture,
            "infrastructure": self.project.infrastructure,
            "technical_constraints": self.project.technical_constraints,
        }

    def _expected_output(self):
        findings = ReviewerAgent._inspect_workspace(
            self.project,
            self.architecture,
            self.workspace_files,
        )
        return {
            "version": 1,
            "status": (
                "changes_required" if findings else "approved"
            ),
            "summary": (
                f"Deterministic review found {len(findings)} issue(s)"
            ),
            "technical_baseline": self._baseline(),
            "findings": [finding.to_dict() for finding in findings],
            "checked_rules": list(EXPECTED_RULES),
            "missing_decisions": [],
        }

    def _set_engine_to_expected_review(self):
        self.engine.run.return_value = EngineResult(
            output=json.dumps(self._expected_output()),
            metadata={"tokens": 275},
            provider="litellm",
            model_alias="project-review-model",
        )

    def test_receives_only_read_only_workspace_contract(self):
        signature = inspect.signature(ReviewerAgent.__init__)
        self.assertIs(
            signature.parameters["workspace_reader"].annotation,
            ProjectCodeReader,
        )
        self.assertFalse(hasattr(self.workspace_reader, "write_files"))

    def test_valid_workspace_is_approved_and_persisted(self):
        events = []
        self.task_status_writer.update_status.side_effect = (
            lambda task_id, status: events.append(str(status))
        )
        self.task_result_writer.write.side_effect = (
            lambda result: events.append("result")
        )

        self.agent.handle(self.task)

        persisted = self.task_result_writer.write.call_args.args[0]
        self.assertIsInstance(persisted, TaskExecutionResult)
        self.assertEqual(json.loads(persisted.output)["status"], "approved")
        self.assertEqual(json.loads(persisted.output)["findings"], [])
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
                "deterministic_findings": 0,
            },
        )
        self.assertEqual(events, ["in_progress", "result", "completed"])
        self.assertEqual(self.task.status, TaskStatus.COMPLETED)

    def test_context_locks_status_findings_baseline_and_rules(self):
        self.agent.handle(self.task)

        context = self.engine.run.call_args.kwargs["context"]
        contract = context["output_contract"]["properties"]
        self.assertEqual(contract["status"]["const"], "approved")
        self.assertEqual(contract["findings"]["const"], [])
        self.assertEqual(
            contract["technical_baseline"]["const"], self._baseline()
        )
        self.assertEqual(contract["checked_rules"]["const"], EXPECTED_RULES)
        self.assertEqual(
            context["review"]["backend_files"][0]["path"],
            "backend/src/service.py",
        )
        self.assertEqual(
            context["review"]["frontend_files"][0]["path"],
            "frontend/package.json",
        )

    def test_adversarial_workspace_reports_all_real_violation_classes(self):
        self.project.frontend_stack = {
            "language": "JavaScript",
            "dependencies": [],
        }
        self.architecture = self._architecture()
        self.workspace_files = [
            WorkspaceFile(
                "backend/src/service.py",
                (
                    "import requests\n"
                    "def unrelated():\n"
                    "    missing_reference()\n"
                    "    pass\n"
                    'ROUTE = "/wrong"\n'
                    'METHOD = "POST"\n'
                ),
            ),
            WorkspaceFile(
                "frontend/package.json",
                '{"dependencies":{"react":"18"}}',
            ),
            WorkspaceFile(
                "frontend/src/app.ts",
                (
                    "// TODO finish\n"
                    "import {missing} from './absent';\n"
                    'fetch("/other", {method: "POST"});\n'
                ),
            ),
        ]
        self.workspace_reader.read_architecture.return_value = self.architecture
        self.workspace_reader.read_files.return_value = self.workspace_files
        self._set_engine_to_expected_review()

        self.agent.handle(self.task)

        payload = json.loads(
            self.task_result_writer.write.call_args.args[0].output
        )
        ids = {finding["id"] for finding in payload["findings"]}
        self.assertEqual(payload["status"], "changes_required")
        expected_prefixes = {
            "REV-API-",
            "REV-INTERFACE-",
            "REV-BACKEND-UNAUTHORIZED-",
            "REV-BROKEN-IMPORT-",
            "REV-BROKEN-REFERENCE-",
            "REV-FRONTEND-DEPENDENCIES",
            "REV-PLACEHOLDER-",
            "REV-TECH-",
        }
        for prefix in expected_prefixes:
            self.assertTrue(
                any(identifier.startswith(prefix) for identifier in ids),
                prefix,
            )
        self.assertTrue(
            all(
                finding["severity"] in {"critical", "high"}
                for finding in payload["findings"]
            )
        )

    def test_detects_missing_imported_symbol(self):
        self.workspace_files.extend(
            [
                WorkspaceFile(
                    "frontend/src/utils.js",
                    "export const available = true;\n",
                ),
            ]
        )
        self.workspace_files[2] = WorkspaceFile(
            "frontend/src/app.js",
            (
                "import {missing} from './utils';\n"
                'const PROJECT_PATH = "/projects/{project_id}";\n'
                "export async function read_project(project_id) {\n"
                "  return fetch(PROJECT_PATH.replace("
                '"{project_id}", project_id), {method: "GET"});\n'
                "}\n"
                "export const get_project = read_project;\n"
            ),
        )

        findings = ReviewerAgent._inspect_workspace(
            self.project,
            self.architecture,
            self.workspace_files,
        )

        self.assertIn(
            "REV-BROKEN-SYMBOL-frontend-src-app-js-missing",
            {finding.id for finding in findings},
        )

    def test_invalid_package_shape_is_a_finding_not_a_crash(self):
        self.workspace_files[1] = WorkspaceFile(
            "frontend/package.json",
            "[]",
        )

        findings = ReviewerAgent._inspect_workspace(
            self.project,
            self.architecture,
            self.workspace_files,
        )

        self.assertIn(
            "REV-FRONTEND-MANIFEST-INVALID",
            {finding.id for finding in findings},
        )

    def test_get_project_name_does_not_fake_get_http_method(self):
        self.workspace_files[0] = WorkspaceFile(
            "backend/src/service.py",
            (
                'PROJECT_PATH = "/projects/{project_id}"\n'
                "def read_project(project_id):\n"
                '    return {"status": "ready"}\n'
                "def get_project(project_id):\n"
                '    method = "POST"\n'
                "    return read_project(project_id)\n"
            ),
        )

        findings = ReviewerAgent._inspect_workspace(
            self.project,
            self.architecture,
            self.workspace_files,
        )

        self.assertIn(
            "REV-API-project-api-get-project-backend",
            {finding.id for finding in findings},
        )

    def test_detects_missing_persistence_model(self):
        self.architecture = self._architecture(
            persistence={
                "stores": [
                    {
                        "name": "primary",
                        "technology": "PostgreSQL",
                        "purpose": "Store project records",
                        "owned_by": "backend",
                        "data_models": [
                            {
                                "name": "ProjectRecord",
                                "description": "Stored project",
                            }
                        ],
                    }
                ]
            }
        )

        findings = ReviewerAgent._inspect_workspace(
            self.project,
            self.architecture,
            self.workspace_files,
        )

        self.assertIn(
            "REV-PERSISTENCE-primary-projectrecord",
            {finding.id for finding in findings},
        )

    def test_missing_module_is_changes_required_not_needs_input(self):
        self.workspace_files = [
            item
            for item in self.workspace_files
            if not item.relative_path.startswith("backend/")
        ]
        self.workspace_reader.read_files.return_value = self.workspace_files
        self._set_engine_to_expected_review()

        self.agent.handle(self.task)

        payload = json.loads(
            self.task_result_writer.write.call_args.args[0].output
        )
        self.assertEqual(payload["status"], "changes_required")
        self.assertIn(
            "REV-MODULE-MISSING-backend",
            {finding["id"] for finding in payload["findings"]},
        )
        self.engine.run.assert_called_once()

    def test_cannot_approve_invalid_workspace_or_invent_finding(self):
        self.workspace_files[2] = WorkspaceFile(
            "frontend/src/app.js",
            "// TODO implement\n",
        )
        self.workspace_reader.read_files.return_value = self.workspace_files
        expected = self._expected_output()
        invalid = dict(expected)
        invalid["status"] = "approved"
        invalid["findings"] = []
        invented = dict(expected)
        invented["findings"] = [
            {
                "id": "REV-INVENTED",
                "severity": "high",
                "scope": "frontend",
                "path": "frontend/src/app.js",
                "rule": "implementation.frontend",
                "message": "Invented",
                "suggested_action": "None",
            }
        ]
        self.engine.run.side_effect = [
            EngineResult(json.dumps(invalid), {}, "test"),
            EngineResult(json.dumps(invented), {}, "test"),
            EngineResult(json.dumps(expected), {}, "test"),
        ]

        self.agent.handle(self.task)

        self.assertEqual(self.engine.run.call_count, 3)
        feedback = self.engine.run.call_args.kwargs["context"]
        self.assertIn("review_validation_feedback", feedback)
        persisted = json.loads(
            self.task_result_writer.write.call_args.args[0].output
        )
        self.assertEqual(persisted["findings"], expected["findings"])

    def test_missing_architecture_is_deterministic_needs_input(self):
        self.workspace_reader.read_architecture.return_value = None

        self.agent.handle(self.task)

        self.engine.run.assert_not_called()
        payload = json.loads(
            self.task_result_writer.write.call_args.args[0].output
        )
        self.assertEqual(payload["status"], "needs_input")
        self.assertEqual(
            payload["missing_decisions"], ["architecture_artifact"]
        )

    def test_needs_input_architecture_does_not_invoke_engine(self):
        self.workspace_reader.read_architecture.return_value = (
            self._architecture(
                status="needs_input",
                missing_decisions=["integration decision"],
                modules=[],
                interfaces=[],
                apis=[],
                persistence={},
                execution_plan=[],
            )
        )

        self.agent.handle(self.task)

        self.engine.run.assert_not_called()
        payload = json.loads(
            self.task_result_writer.write.call_args.args[0].output
        )
        self.assertEqual(payload["missing_decisions"], ["integration decision"])

    def test_project_architecture_contradiction_fails_before_engine(self):
        self.workspace_reader.read_architecture.return_value = (
            self._architecture(backend_architecture={"style": "other"})
        )

        with self.assertRaisesRegex(
            ReviewValidationError, "backend_architecture"
        ):
            self.agent.handle(self.task)

        self.engine.run.assert_not_called()
        self.assertEqual(self.task.status, TaskStatus.FAILED)

    def test_persistence_failure_marks_failed(self):
        self.task_result_writer.write.side_effect = RuntimeError(
            "database unavailable"
        )

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            self.agent.handle(self.task)

        self.assertEqual(self.task.status, TaskStatus.FAILED)

    def test_unknown_project_fails_without_workspace_or_engine(self):
        self.project_reader.find_by_id.return_value = None

        with self.assertRaisesRegex(
            ReviewProjectNotFoundError, str(self.task.project_id)
        ):
            self.agent.handle(self.task)

        self.workspace_reader.read_architecture.assert_not_called()
        self.engine.run.assert_not_called()
        self.assertEqual(self.task.status, TaskStatus.FAILED)

    def test_status_failure_attempts_failed_and_propagates(self):
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


if __name__ == "__main__":
    unittest.main()
