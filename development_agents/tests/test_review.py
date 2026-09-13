import json
import unittest

from core.models.review import (
    FindingScope,
    FindingSeverity,
    ReviewArtifact,
    ReviewStatus,
    ReviewValidationError,
)


def valid_baseline():
    return {
        "backend_stack": {"language": "ServerLang"},
        "backend_architecture": {"style": "ProjectServerStyle"},
        "frontend_stack": {"language": "ClientLang"},
        "frontend_architecture": {"style": "ProjectClientStyle"},
        "infrastructure": {"runtime": "ProjectRuntime"},
        "technical_constraints": ["Project-specific constraint"],
    }


def valid_finding(**overrides):
    finding = {
        "id": "REV-001",
        "severity": "high",
        "scope": "backend",
        "path": "backend/src/service.code",
        "rule": "project.backend_architecture",
        "message": "The implementation violates the declared structure",
        "suggested_action": "Align responsibilities with the declaration",
    }
    finding.update(overrides)
    return finding


def valid_payload(**overrides):
    payload = {
        "version": 1,
        "status": "changes_required",
        "summary": "The project requires one correction",
        "technical_baseline": valid_baseline(),
        "findings": [valid_finding()],
        "checked_rules": [
            "project.backend_stack",
            "project.backend_architecture",
        ],
        "missing_decisions": [],
    }
    payload.update(overrides)
    return payload


class ReviewArtifactTests(unittest.TestCase):
    def test_parses_findings_and_serializes_canonical_json(self):
        payload = valid_payload()

        review = ReviewArtifact.from_json(json.dumps(payload))

        self.assertEqual(review.status, ReviewStatus.CHANGES_REQUIRED)
        self.assertEqual(
            review.findings[0].severity,
            FindingSeverity.HIGH,
        )
        self.assertEqual(review.findings[0].scope, FindingScope.BACKEND)
        self.assertEqual(json.loads(review.to_json()), payload)

    def test_approved_allows_only_non_blocking_findings(self):
        payload = valid_payload(
            status="approved",
            findings=[valid_finding(severity="medium")],
            summary="Approved with a non-blocking observation",
        )

        review = ReviewArtifact.from_json(json.dumps(payload))

        self.assertEqual(review.status, ReviewStatus.APPROVED)

        payload["findings"][0]["severity"] = "critical"
        with self.assertRaisesRegex(
            ReviewValidationError,
            "blocking findings",
        ):
            ReviewArtifact.from_json(json.dumps(payload))

    def test_changes_required_requires_at_least_one_finding(self):
        payload = valid_payload(findings=[])

        with self.assertRaisesRegex(
            ReviewValidationError,
            "requires findings",
        ):
            ReviewArtifact.from_json(json.dumps(payload))

    def test_needs_input_requires_decisions_and_forbids_findings(self):
        payload = valid_payload(
            status="needs_input",
            findings=[],
            checked_rules=[],
            missing_decisions=["integration_contract"],
            summary="An integration decision is missing",
        )

        review = ReviewArtifact.from_json(json.dumps(payload))

        self.assertEqual(review.status, ReviewStatus.NEEDS_INPUT)

        payload["findings"] = [valid_finding()]
        with self.assertRaisesRegex(
            ReviewValidationError,
            "no findings",
        ):
            ReviewArtifact.from_json(json.dumps(payload))

    def test_rejects_unsafe_finding_paths(self):
        for path in (
            "/etc/passwd",
            "../outside.code",
            "backend/../../outside.code",
            "backend\\outside.code",
            "backend//outside.code",
        ):
            with self.subTest(path=path):
                payload = valid_payload(
                    findings=[valid_finding(path=path)]
                )
                with self.assertRaisesRegex(
                    ReviewValidationError,
                    "unsafe path",
                ):
                    ReviewArtifact.from_json(json.dumps(payload))

    def test_global_findings_may_have_null_path(self):
        payload = valid_payload(
            findings=[
                valid_finding(
                    path=None,
                    scope="integration",
                    rule="integration.backend_frontend",
                )
            ]
        )

        review = ReviewArtifact.from_json(json.dumps(payload))

        self.assertIsNone(review.findings[0].path)

    def test_rejects_duplicate_ids_rules_and_invalid_baseline(self):
        payload = valid_payload(
            findings=[valid_finding(), valid_finding()]
        )
        with self.assertRaisesRegex(ReviewValidationError, "unique ids"):
            ReviewArtifact.from_json(json.dumps(payload))

        payload = valid_payload(
            checked_rules=["project.backend_stack"] * 2
        )
        with self.assertRaisesRegex(ReviewValidationError, "duplicates"):
            ReviewArtifact.from_json(json.dumps(payload))

        payload = valid_payload()
        payload["technical_baseline"]["alternative"] = {}
        with self.assertRaisesRegex(
            ReviewValidationError,
            "technical_baseline must contain exactly",
        ):
            ReviewArtifact.from_json(json.dumps(payload))

    def test_rejects_unknown_fields_and_non_json_numbers(self):
        payload = valid_payload()
        payload["extra"] = True
        with self.assertRaisesRegex(ReviewValidationError, "unknown fields"):
            ReviewArtifact.from_json(json.dumps(payload))

        with self.assertRaisesRegex(ReviewValidationError, "strict JSON"):
            ReviewArtifact.from_json(
                json.dumps(valid_payload()).replace(
                    '"version": 1',
                    '"version": NaN',
                )
            )


if __name__ == "__main__":
    unittest.main()
