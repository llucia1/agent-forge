import json
import unittest
from uuid import UUID

from core.models.architecture import ArchitectureArtifact
from core.models.qa import (
    QACheckResult,
    QACheckStatus,
    QAArtifact,
    QAPlan,
    QAStatus,
    QAValidationError,
    ReviewGate,
)
from core.models.review import ReviewStatus, TechnicalBaseline
from core.models.workspace import ProjectSnapshot, WorkspaceFile
from tests.architecture_fixtures import usable_architecture_sections


def architecture() -> ArchitectureArtifact:
    return ArchitectureArtifact.from_json(
        json.dumps(
            {
                "version": 1,
                "status": "complete",
                "missing_decisions": [],
                "backend_stack": {"runtime": "ServerRuntime"},
                "backend_architecture": {"style": "ServerStyle"},
                "frontend_stack": {"runtime": "ClientRuntime"},
                "frontend_architecture": {"style": "ClientStyle"},
                "infrastructure": {"runtime": "ProjectRuntime"},
                "technical_constraints": [],
                **usable_architecture_sections(),
            }
        )
    )


class ProjectSnapshotTests(unittest.TestCase):
    def test_fingerprint_is_order_independent_and_content_sensitive(self):
        files = [
            WorkspaceFile("frontend/view.code", "view\n"),
            WorkspaceFile("backend/service.code", "service\n"),
        ]

        first = ProjectSnapshot.create(architecture(), files)
        second = ProjectSnapshot.create(architecture(), list(reversed(files)))
        changed = ProjectSnapshot.create(
            architecture(),
            [files[0], WorkspaceFile(files[1].relative_path, "changed\n")],
        )

        self.assertRegex(first.fingerprint, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertNotEqual(first.fingerprint, changed.fingerprint)

    def test_fingerprint_includes_canonical_architecture(self):
        original = architecture()
        payload = json.loads(original.to_json())
        payload["modules"][0]["responsibility"] = "Changed responsibility"
        changed = ArchitectureArtifact.from_json(json.dumps(payload))

        self.assertNotEqual(
            ProjectSnapshot.create(original, []).fingerprint,
            ProjectSnapshot.create(changed, []).fingerprint,
        )


class QAPlanTests(unittest.TestCase):
    def test_plan_contains_only_selection_rationale_and_missing_decisions(self):
        payload = {
            "version": 1,
            "selected_check_ids": ["authorized-check"],
            "rationale": {"authorized-check": "Required"},
            "missing_decisions": [],
        }

        plan = QAPlan.from_json(json.dumps(payload))

        self.assertEqual(plan.selected_check_ids, ("authorized-check",))
        contract = QAPlan.output_contract(["authorized-check"])
        self.assertNotIn("command", contract["properties"])
        self.assertNotIn("executable", contract["properties"])

    def test_plan_rejects_any_command_field(self):
        payload = {
            "version": 1,
            "selected_check_ids": ["authorized-check"],
            "rationale": {"authorized-check": "Required"},
            "missing_decisions": [],
            "command": "invented command",
        }

        with self.assertRaisesRegex(QAValidationError, "invalid fields"):
            QAPlan.from_json(json.dumps(payload))


class QAArtifactTests(unittest.TestCase):
    def test_round_trips_structured_execution_evidence(self):
        artifact = QAArtifact(
            version=1,
            status=QAStatus.PASSED,
            summary="Checks passed",
            technical_baseline=TechnicalBaseline(
                backend_stack={},
                backend_architecture={},
                frontend_stack={},
                frontend_architecture={},
                infrastructure={},
                technical_constraints=[],
            ),
            review_gate=ReviewGate(
                task_id=UUID("5312bca0-abf0-45c5-9869-e726ef78ebca"),
                result_id=UUID("11950e4a-3b2f-4596-b2a1-df6e23bb3a66"),
                status=ReviewStatus.APPROVED,
            ),
            workspace_fingerprint="sha256:" + "a" * 64,
            checks=(
                QACheckResult(
                    id="check",
                    status=QACheckStatus.PASSED,
                    exit_code=0,
                    duration_ms=1,
                    stdout_excerpt="ok",
                    stderr_excerpt="",
                    output_truncated=False,
                    failure_reason=None,
                    evidence_hashes={
                        "project.conf": "sha256:" + "b" * 64
                    },
                ),
            ),
            missing_decisions=(),
        )

        parsed = QAArtifact.from_json(artifact.to_json())

        self.assertEqual(parsed, artifact)


if __name__ == "__main__":
    unittest.main()
