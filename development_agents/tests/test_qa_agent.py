import json
import unittest
from unittest.mock import Mock, call
from uuid import UUID

from agents.qa.agent import PENDING_QA_INPUTS, QAAgent
from core.contracts.qa import ProjectQAExecutor
from core.contracts.repositories import ProjectReader, TaskStatusWriter
from core.contracts.results import LatestTaskExecutionReader, TaskResultWriter
from core.contracts.workspaces import ProjectCodeReader
from core.models.architecture import ArchitectureArtifact
from core.models.project import Project
from core.models.qa import QAExecutionPolicy
from core.models.task import AgentRole, Task, TaskStatus
from core.models.task_result import AgentTaskExecution, TaskExecutionResult
from core.models.workspace import ProjectSnapshot, WorkspaceFile


class QAAgentTests(unittest.TestCase):
    def setUp(self):
        self.project_id = UUID("93de5ea5-729a-4c5e-8dc3-443165ed516b")
        self.task = Task(
            id=UUID("e8d43f59-c694-4446-817d-76faa6e51fe9"),
            project_id=self.project_id,
            title="Validate generated project",
            description="Run authorized quality checks",
            agent=AgentRole.QA,
        )
        self.project = Project(
            id=self.project_id,
            name="Generated Project",
            description="Technology-neutral fixture",
            backend_stack={"runtime": "ServerRuntime"},
            backend_architecture={"style": "ServerStyle"},
            frontend_stack={"runtime": "ClientRuntime"},
            frontend_architecture={"style": "ClientStyle"},
            infrastructure={"execution": "ProjectRuntime"},
            technical_constraints=["Project constraint"],
        )
        self.architecture = self._architecture()
        self.files = [
            WorkspaceFile("backend/project.conf", "check = true\n"),
            WorkspaceFile("frontend/view.code", "view\n"),
        ]
        self.review_task_id = UUID(
            "5312bca0-abf0-45c5-9869-e726ef78ebca"
        )
        self.review_result = TaskExecutionResult(
            id=UUID("11950e4a-3b2f-4596-b2a1-df6e23bb3a66"),
            task_id=self.review_task_id,
            project_id=self.project_id,
            agent="reviewer",
            provider="test",
            model_alias=None,
            output=json.dumps(self._review_payload()),
            metadata={},
        )
        self.latest_review = AgentTaskExecution(
            task_id=self.review_task_id,
            project_id=self.project_id,
            agent=AgentRole.REVIEWER,
            task_status=TaskStatus.COMPLETED,
            result=self.review_result,
        )
        self.status_writer = Mock(spec=TaskStatusWriter)
        self.result_writer = Mock(spec=TaskResultWriter)
        self.execution_reader = Mock(spec=LatestTaskExecutionReader)
        self.execution_reader.find_latest.return_value = self.latest_review
        self.project_reader = Mock(spec=ProjectReader)
        self.project_reader.find_by_id.return_value = self.project
        self.workspace_reader = Mock(spec=ProjectCodeReader)
        self.workspace_reader.read_architecture.return_value = (
            self.architecture
        )
        self.workspace_reader.read_files.return_value = self.files
        self.executor = Mock(spec=ProjectQAExecutor)
        self.agent = QAAgent(
            task_status_writer=self.status_writer,
            task_result_writer=self.result_writer,
            task_execution_reader=self.execution_reader,
            project_reader=self.project_reader,
            workspace_reader=self.workspace_reader,
            executor=self.executor,
            execution_policy=QAExecutionPolicy((), 1024),
        )

    def _architecture(self):
        return ArchitectureArtifact.from_json(
            json.dumps(
                {
                    "version": 1,
                    "status": "complete",
                    "missing_decisions": [],
                    "backend_stack": self.project.backend_stack,
                    "backend_architecture": self.project.backend_architecture,
                    "frontend_stack": self.project.frontend_stack,
                    "frontend_architecture": (
                        self.project.frontend_architecture
                    ),
                    "infrastructure": self.project.infrastructure,
                    "technical_constraints": (
                        self.project.technical_constraints
                    ),
                    "modules": [{"name": "project"}],
                    "interfaces": [],
                    "apis": [],
                    "persistence": {},
                    "execution_plan": [],
                }
            )
        )

    def _baseline(self):
        return {
            "backend_stack": self.project.backend_stack,
            "backend_architecture": self.project.backend_architecture,
            "frontend_stack": self.project.frontend_stack,
            "frontend_architecture": self.project.frontend_architecture,
            "infrastructure": self.project.infrastructure,
            "technical_constraints": self.project.technical_constraints,
        }

    def _review_payload(self, **overrides):
        payload = {
            "version": 1,
            "status": "approved",
            "summary": "Approved",
            "technical_baseline": self._baseline(),
            "findings": [],
            "checked_rules": ["project.backend_stack"],
            "missing_decisions": [],
        }
        payload.update(overrides)
        return payload

    def _set_review(self, payload):
        result = TaskExecutionResult(
            id=self.review_result.id,
            task_id=self.review_task_id,
            project_id=self.project_id,
            agent="reviewer",
            provider="test",
            model_alias=None,
            output=json.dumps(payload),
            metadata={},
        )
        self.execution_reader.find_latest.return_value = AgentTaskExecution(
            task_id=self.review_task_id,
            project_id=self.project_id,
            agent=AgentRole.REVIEWER,
            task_status=TaskStatus.COMPLETED,
            result=result,
        )

    def _persisted_payload(self):
        return json.loads(self.result_writer.write.call_args.args[0].output)

    def test_approved_review_stops_at_explicit_pending_inputs(self):
        self.agent.handle(self.task)

        self.executor.execute.assert_not_called()
        payload = self._persisted_payload()
        snapshot = ProjectSnapshot.create(
            self.architecture,
            self.files,
        )
        self.assertEqual(payload["status"], "needs_input")
        self.assertEqual(
            payload["missing_decisions"],
            list(PENDING_QA_INPUTS),
        )
        self.assertEqual(
            payload["workspace_fingerprint"],
            snapshot.fingerprint,
        )
        self.assertEqual(
            self.status_writer.update_status.call_args_list,
            [
                call(self.task.id, TaskStatus.IN_PROGRESS),
                call(self.task.id, TaskStatus.NEEDS_INPUT),
            ],
        )

    def test_changes_required_review_blocks_without_reading_code(self):
        self._set_review(
            self._review_payload(
                status="changes_required",
                findings=[
                    {
                        "id": "REV-1",
                        "severity": "high",
                        "scope": "backend",
                        "path": "backend/project.conf",
                        "rule": "project.backend_stack",
                        "message": "Mismatch",
                        "suggested_action": "Correct it",
                    }
                ],
            )
        )

        self.agent.handle(self.task)

        self.workspace_reader.read_files.assert_not_called()
        self.executor.execute.assert_not_called()
        self.assertEqual(self._persisted_payload()["status"], "blocked")
        self.assertEqual(self.task.status, TaskStatus.COMPLETED)

    def test_incomplete_reviewer_task_never_executes(self):
        self.execution_reader.find_latest.return_value = AgentTaskExecution(
            task_id=self.review_task_id,
            project_id=self.project_id,
            agent=AgentRole.REVIEWER,
            task_status=TaskStatus.IN_PROGRESS,
            result=None,
        )

        self.agent.handle(self.task)

        self.executor.execute.assert_not_called()
        self.assertEqual(self._persisted_payload()["status"], "needs_input")
        self.assertEqual(
            self._persisted_payload()["missing_decisions"],
            ["completed_reviewer_result"],
        )


if __name__ == "__main__":
    unittest.main()
