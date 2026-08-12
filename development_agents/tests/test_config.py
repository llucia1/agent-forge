import unittest
from uuid import UUID

from core.config import ConfigurationError, EngineSettings


class EngineSettingsTests(unittest.TestCase):
    def test_loads_engine_and_model_selection_from_environment(self):
        project_id = UUID("93de5ea5-729a-4c5e-8dc3-443165ed516b")
        settings = EngineSettings.from_environment(
            {
                "AGENTFORGE_DEFAULT_ENGINE": "litellm",
                "AGENTFORGE_DEFAULT_MODEL": "general-default",
                "AGENTFORGE_MODELS_BY_AGENT": (
                    '{"architect": "architecture-primary"}'
                ),
                "AGENTFORGE_MODELS_BY_PROJECT": (
                    '{"93de5ea5-729a-4c5e-8dc3-443165ed516b": '
                    '"project-specialized"}'
                ),
            }
        )

        self.assertEqual(settings.default_engine, "litellm")
        self.assertEqual(settings.default_model, "general-default")
        self.assertEqual(
            settings.models_by_agent,
            {"architect": "architecture-primary"},
        )
        self.assertEqual(
            settings.models_by_project,
            {project_id: "project-specialized"},
        )

    def test_rejects_missing_default_engine(self):
        with self.assertRaisesRegex(
            ConfigurationError,
            "AGENTFORGE_DEFAULT_ENGINE must not be empty",
        ):
            EngineSettings.from_environment(
                {"AGENTFORGE_DEFAULT_MODEL": "general-default"}
            )

    def test_rejects_invalid_model_mapping_json(self):
        with self.assertRaisesRegex(
            ConfigurationError,
            "must be a valid JSON object",
        ):
            EngineSettings.from_environment(
                {"AGENTFORGE_MODELS_BY_AGENT": "not-json"}
            )

    def test_rejects_invalid_project_uuid(self):
        with self.assertRaisesRegex(
            ConfigurationError,
            "invalid project UUID",
        ):
            EngineSettings.from_environment(
                {
                    "AGENTFORGE_MODELS_BY_PROJECT": (
                        '{"not-a-uuid": "project-specialized"}'
                    )
                }
            )


if __name__ == "__main__":
    unittest.main()
