import tempfile
import unittest
from pathlib import Path
from uuid import UUID

from core.infrastructure.workspace import FilesystemProjectWorkspace
from core.models.specification import (
    FeatureSpecification,
    ProjectSpecification,
    SpecificationValidationError,
)
from tests.specification_fixtures import feature_markdown, project_markdown


PROJECT_ID = UUID("93de5ea5-729a-4c5e-8dc3-443165ed516b")


class SpecificationContractTests(unittest.TestCase):
    def test_parses_required_sections_and_acceptance_check_mapping(self):
        project = ProjectSpecification.from_markdown(project_markdown())
        feature = FeatureSpecification.from_markdown(
            feature_markdown("backend-tests", "frontend-build")
        )

        self.assertEqual(project.title, "Task Tracker")
        self.assertEqual(
            [criterion.check_id for criterion in feature.acceptance_criteria],
            ["backend-tests", "frontend-build"],
        )
        self.assertRegex(feature.fingerprint, r"^sha256:[0-9a-f]{64}$")

    def test_rejects_missing_sections_and_unmapped_acceptance_criteria(self):
        with self.assertRaises(SpecificationValidationError):
            FeatureSpecification.from_markdown("# Incomplete\n")
        invalid = feature_markdown().replace(
            "- AC-001 [check: spec-check]: Criterion 1 passes.",
            "- The feature works.",
        )
        with self.assertRaisesRegex(
            SpecificationValidationError,
            "Acceptance criteria",
        ):
            FeatureSpecification.from_markdown(invalid)

    def test_workspace_round_trips_project_and_feature_specs(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = FilesystemProjectWorkspace(Path(root))
            project = ProjectSpecification.from_markdown(project_markdown())
            feature = FeatureSpecification.from_markdown(feature_markdown())

            workspace.write_project_specification(PROJECT_ID, project)
            workspace.write_specification(
                PROJECT_ID,
                "specs/create-task.md",
                feature,
            )

            self.assertEqual(
                workspace.read_project_specification(PROJECT_ID),
                project,
            )
            self.assertEqual(
                workspace.read_specification(
                    PROJECT_ID,
                    "specs/create-task.md",
                ),
                feature,
            )


if __name__ == "__main__":
    unittest.main()
