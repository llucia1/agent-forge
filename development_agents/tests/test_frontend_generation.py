import json
import unittest

from core.models.frontend_generation import (
    FrontendGenerationArtifact,
    FrontendGenerationStatus,
    FrontendGenerationValidationError,
)


def valid_payload():
    return {
        "version": 1,
        "status": "complete",
        "missing_decisions": [],
        "frontend_stack": {
            "language": "TypeScript",
            "ui": "ProjectUI",
        },
        "frontend_architecture": {"style": "component-based"},
        "infrastructure": {"container": "Docker"},
        "technical_constraints": ["Keep UI responsibilities separate"],
        "files": [
            {
                "path": "frontend/package.json",
                "content": '{"scripts":{"build":"project-build"}}\n',
            },
            {
                "path": "frontend/src/components/order-list.tsx",
                "content": "export const OrderList = () => null;\n",
            },
        ],
        "summary": "Implemented the project frontend",
    }


class FrontendGenerationArtifactTests(unittest.TestCase):
    def test_parses_multiple_files_and_serializes_canonical_json(self):
        artifact = FrontendGenerationArtifact.from_json(
            json.dumps(valid_payload())
        )

        self.assertEqual(artifact.version, 1)
        self.assertEqual(
            artifact.status,
            FrontendGenerationStatus.COMPLETE,
        )
        self.assertEqual(len(artifact.files), 2)
        self.assertEqual(json.loads(artifact.to_json()), valid_payload())

    def test_needs_input_requires_decisions_and_forbids_files(self):
        payload = valid_payload()
        payload.update(
            status="needs_input",
            missing_decisions=["routing strategy"],
            files=[],
        )

        artifact = FrontendGenerationArtifact.from_json(json.dumps(payload))

        self.assertEqual(
            artifact.status,
            FrontendGenerationStatus.NEEDS_INPUT,
        )

        payload["files"] = valid_payload()["files"]
        with self.assertRaises(FrontendGenerationValidationError):
            FrontendGenerationArtifact.from_json(json.dumps(payload))

    def test_complete_requires_at_least_one_file(self):
        payload = valid_payload()
        payload["files"] = []

        with self.assertRaisesRegex(
            FrontendGenerationValidationError,
            "must contain files",
        ):
            FrontendGenerationArtifact.from_json(json.dumps(payload))

    def test_rejects_paths_outside_frontend_and_architecture_artifacts(self):
        invalid_paths = (
            "/frontend/src/app.ts",
            "../frontend/src/app.ts",
            "frontend/../../outside.ts",
            "frontend\\src\\app.ts",
            "frontend//src/app.ts",
            "frontend/./src/app.ts",
            "backend/src/app.ts",
            "architecture.json",
            "frontend/architecture.json",
        )

        for invalid_path in invalid_paths:
            with self.subTest(path=invalid_path):
                payload = valid_payload()
                payload["files"] = [
                    {"path": invalid_path, "content": "unsafe\n"}
                ]
                with self.assertRaises(FrontendGenerationValidationError):
                    FrontendGenerationArtifact.from_json(
                        json.dumps(payload)
                    )

    def test_rejects_dependencies_builds_caches_and_binary_paths(self):
        invalid_paths = (
            "frontend/.git/config",
            "frontend/node_modules/package/index.js",
            "frontend/build/app.js",
            "frontend/dist/app.js",
            "frontend/src/__pycache__/generated.js",
            "frontend/cache/result.ts",
            "frontend/src/logo.png",
        )

        for invalid_path in invalid_paths:
            with self.subTest(path=invalid_path):
                payload = valid_payload()
                payload["files"] = [
                    {"path": invalid_path, "content": "not editable\n"}
                ]
                with self.assertRaisesRegex(
                    FrontendGenerationValidationError,
                    "editable|frontend",
                ):
                    FrontendGenerationArtifact.from_json(
                        json.dumps(payload)
                    )

    def test_rejects_duplicates_unknown_fields_and_non_json_numbers(self):
        payload = valid_payload()
        payload["files"].append(dict(payload["files"][0]))
        with self.assertRaisesRegex(
            FrontendGenerationValidationError,
            "duplicate",
        ):
            FrontendGenerationArtifact.from_json(json.dumps(payload))

        payload = valid_payload()
        payload["alternative_ui"] = "OtherUI"
        with self.assertRaisesRegex(
            FrontendGenerationValidationError,
            "unknown fields",
        ):
            FrontendGenerationArtifact.from_json(json.dumps(payload))

        with self.assertRaisesRegex(
            FrontendGenerationValidationError,
            "strict JSON",
        ):
            FrontendGenerationArtifact.from_json(
                json.dumps(valid_payload()).replace(
                    '"version": 1',
                    '"version": NaN',
                )
            )


if __name__ == "__main__":
    unittest.main()
