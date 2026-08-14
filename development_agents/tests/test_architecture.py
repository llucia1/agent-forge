import json
import unittest

from core.models.architecture import (
    ArchitectureArtifact,
    ArchitectureArtifactValidationError,
)


def valid_architecture() -> dict:
    return {
        "version": 1,
        "status": "complete",
        "missing_decisions": [],
        "backend_stack": {"language": "Python"},
        "backend_architecture": {"style": "ports-and-adapters"},
        "frontend_stack": {"framework": "React"},
        "frontend_architecture": {"style": "component-based"},
        "infrastructure": {"runtime": "Docker Compose"},
        "technical_constraints": ["Use Python 3.12"],
        "modules": [{"name": "architecture"}],
        "interfaces": [{"name": "TaskResultWriter"}],
        "apis": [{"name": "tasks"}],
        "persistence": {"database": "PostgreSQL"},
        "execution_plan": [{"step": "Define contracts"}],
    }


class ArchitectureArtifactTests(unittest.TestCase):
    def test_parses_and_canonicalizes_versioned_architecture(self):
        payload = valid_architecture()

        artifact = ArchitectureArtifact.from_json(json.dumps(payload))

        self.assertEqual(artifact.version, 1)
        self.assertEqual(json.loads(artifact.to_json()), payload)

    def test_requires_every_contract_field(self):
        for field_name in ArchitectureArtifact.required_fields():
            with self.subTest(field=field_name):
                payload = valid_architecture()
                del payload[field_name]

                with self.assertRaisesRegex(
                    ArchitectureArtifactValidationError,
                    "missing required fields",
                ):
                    ArchitectureArtifact.from_json(json.dumps(payload))

    def test_requires_version_to_be_integer_one(self):
        for invalid_version in (True, 0, 2, "1", None):
            with self.subTest(version=invalid_version):
                payload = valid_architecture()
                payload["version"] = invalid_version

                with self.assertRaisesRegex(
                    ArchitectureArtifactValidationError,
                    "version must be the integer 1",
                ):
                    ArchitectureArtifact.from_json(json.dumps(payload))

    def test_rejects_unknown_top_level_fields(self):
        payload = valid_architecture()
        payload["alternative_stack"] = {"language": "Go"}

        with self.assertRaisesRegex(
            ArchitectureArtifactValidationError,
            "unknown fields: alternative_stack",
        ):
            ArchitectureArtifact.from_json(json.dumps(payload))

    def test_enforces_status_and_missing_decisions_relationship(self):
        invalid_cases = (
            {"status": "unknown", "missing_decisions": []},
            {"status": "complete", "missing_decisions": ["database"]},
            {"status": "needs_input", "missing_decisions": []},
        )

        for changes in invalid_cases:
            with self.subTest(changes=changes):
                payload = {**valid_architecture(), **changes}

                with self.assertRaises(ArchitectureArtifactValidationError):
                    ArchitectureArtifact.from_json(json.dumps(payload))

    def test_needs_input_cannot_contain_definitive_design(self):
        payload = valid_architecture()
        payload.update(
            status="needs_input",
            missing_decisions=["infrastructure"],
        )

        with self.assertRaisesRegex(
            ArchitectureArtifactValidationError,
            "cannot contain a definitive design",
        ):
            ArchitectureArtifact.from_json(json.dumps(payload))

        payload.update(
            modules=[],
            interfaces=[],
            apis=[],
            persistence={},
            execution_plan=[],
        )
        artifact = ArchitectureArtifact.from_json(json.dumps(payload))
        self.assertEqual(artifact.status, "needs_input")

    def test_rejects_wrong_required_field_types(self):
        invalid_values = {
            "backend_stack": [],
            "backend_architecture": "layered",
            "frontend_stack": [],
            "frontend_architecture": None,
            "infrastructure": [],
            "technical_constraints": ["valid", 12],
            "modules": ["architecture"],
            "interfaces": [1],
            "apis": [None],
            "persistence": [],
            "execution_plan": ["build"],
        }

        for field_name, invalid_value in invalid_values.items():
            with self.subTest(field=field_name):
                payload = valid_architecture()
                payload[field_name] = invalid_value

                with self.assertRaises(ArchitectureArtifactValidationError):
                    ArchitectureArtifact.from_json(json.dumps(payload))

    def test_rejects_non_object_markdown_and_non_json_numbers(self):
        invalid_outputs = (
            "[]",
            "```json\n{}\n```",
            json.dumps({**valid_architecture(), "extra": float("nan")}),
        )

        for output in invalid_outputs:
            with self.subTest(output=output):
                with self.assertRaises(ArchitectureArtifactValidationError):
                    ArchitectureArtifact.from_json(output)

    def test_output_contract_declares_required_version_one(self):
        contract = ArchitectureArtifact.output_contract()

        self.assertIn("version", contract["required"])
        self.assertEqual(
            contract["properties"]["version"],
            {"type": "integer", "const": 1},
        )
        self.assertIn("status", contract["required"])
        self.assertIn("missing_decisions", contract["required"])
        self.assertFalse(contract["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
