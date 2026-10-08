import json
import unittest

from core.models.backend_generation import (
    BackendGenerationArtifact,
    BackendGenerationValidationError,
    BackendGenerationStatus,
)


def valid_payload():
    return {
        "version": 1,
        "status": "complete",
        "missing_decisions": [],
        "backend_stack": {
            "language": "Python",
            "framework": "FastAPI",
        },
        "backend_architecture": {
            "style": "Hexagonal",
            "patterns": ["DDD", "CQRS"],
        },
        "infrastructure": {"database": "PostgreSQL"},
        "technical_constraints": ["Apply all SOLID principles"],
        "files": [
            {
                "path": "backend/domain/orders/order.py",
                "content": "class Order:\n    order_id = 'new'\n",
            },
            {
                "path": "backend/application/create_order.py",
                "content": "def create_order():\n    return 'created'\n",
            },
        ],
        "implementation": {
            "modules": ["orders"],
            "interfaces": [],
            "apis": [],
            "persistence_stores": [],
            "dependencies": [],
            "file_modules": {
                "backend/domain/orders/order.py": "orders",
                "backend/application/create_order.py": "orders",
            },
        },
        "summary": "Implemented the order backend",
    }


class BackendGenerationArtifactTests(unittest.TestCase):
    def test_parses_multiple_files_and_serializes_canonical_json(self):
        artifact = BackendGenerationArtifact.from_json(
            json.dumps(valid_payload())
        )

        self.assertEqual(artifact.version, 1)
        self.assertEqual(artifact.status, BackendGenerationStatus.COMPLETE)
        self.assertEqual(len(artifact.files), 2)
        self.assertEqual(json.loads(artifact.to_json()), valid_payload())

    def test_needs_input_requires_decisions_and_forbids_files(self):
        payload = valid_payload()
        payload.update(
            status="needs_input",
            missing_decisions=["database schema"],
            files=[],
            implementation={
                "modules": [],
                "interfaces": [],
                "apis": [],
                "persistence_stores": [],
                "dependencies": [],
                "file_modules": {},
            },
        )

        artifact = BackendGenerationArtifact.from_json(json.dumps(payload))

        self.assertEqual(
            artifact.status,
            BackendGenerationStatus.NEEDS_INPUT,
        )

        payload["files"] = valid_payload()["files"]
        with self.assertRaises(BackendGenerationValidationError):
            BackendGenerationArtifact.from_json(json.dumps(payload))

    def test_complete_requires_at_least_one_file(self):
        payload = valid_payload()
        payload["files"] = []

        with self.assertRaisesRegex(
            BackendGenerationValidationError,
            "must contain files",
        ):
            BackendGenerationArtifact.from_json(json.dumps(payload))

    def test_manifest_must_cover_every_generated_file(self):
        payload = valid_payload()
        payload["implementation"]["file_modules"].pop(
            "backend/application/create_order.py"
        )

        with self.assertRaisesRegex(
            BackendGenerationValidationError,
            "map every generated file",
        ):
            BackendGenerationArtifact.from_json(json.dumps(payload))

    def test_single_module_derives_unambiguous_file_mapping(self):
        payload = valid_payload()
        payload["implementation"]["file_modules"] = {}

        artifact = BackendGenerationArtifact.from_json(json.dumps(payload))

        self.assertEqual(
            artifact.implementation["file_modules"],
            {
                "backend/application/create_order.py": "orders",
                "backend/domain/orders/order.py": "orders",
            },
        )

    def test_manifest_references_only_generated_files(self):
        payload = valid_payload()
        payload["implementation"]["file_modules"]["backend/missing.py"] = (
            "orders"
        )

        with self.assertRaisesRegex(
            BackendGenerationValidationError,
            "map every generated file",
        ):
            BackendGenerationArtifact.from_json(json.dumps(payload))

    def test_rejects_unsafe_and_reserved_paths(self):
        unsafe_paths = (
            "/etc/passwd",
            "../outside.py",
            "backend/../../outside.py",
            "backend\\outside.py",
            "backend//outside.py",
            "backend/./outside.py",
            "architecture.json",
        )

        for unsafe_path in unsafe_paths:
            with self.subTest(path=unsafe_path):
                payload = valid_payload()
                payload["files"] = [
                    {"path": unsafe_path, "content": "unsafe\n"}
                ]
                with self.assertRaises(BackendGenerationValidationError):
                    BackendGenerationArtifact.from_json(json.dumps(payload))

    def test_rejects_duplicates_unknown_fields_and_non_json_numbers(self):
        payload = valid_payload()
        payload["files"].append(dict(payload["files"][0]))
        with self.assertRaisesRegex(
            BackendGenerationValidationError,
            "duplicate",
        ):
            BackendGenerationArtifact.from_json(json.dumps(payload))

        payload = valid_payload()
        payload["alternative_framework"] = "Django"
        with self.assertRaisesRegex(
            BackendGenerationValidationError,
            "unknown fields",
        ):
            BackendGenerationArtifact.from_json(json.dumps(payload))

        with self.assertRaisesRegex(
            BackendGenerationValidationError,
            "strict JSON",
        ):
            BackendGenerationArtifact.from_json(
                json.dumps(valid_payload()).replace('"version": 1', '"version": NaN')
            )


if __name__ == "__main__":
    unittest.main()
