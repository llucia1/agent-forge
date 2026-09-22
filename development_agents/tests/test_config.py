import unittest
from uuid import UUID

from core.config import ConfigurationError, load_settings
from core.models.task import AgentRole


class EngineSettingsTests(unittest.TestCase):
    def test_loads_engine_and_model_selection_from_environment(self):
        project_id = UUID("93de5ea5-729a-4c5e-8dc3-443165ed516b")
        settings = load_settings(
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

        engine_settings = settings.engine
        self.assertEqual(engine_settings.default_engine, "litellm")
        self.assertEqual(engine_settings.default_model, "general-default")
        self.assertEqual(
            engine_settings.models_by_agent,
            {"architect": "architecture-primary"},
        )
        self.assertEqual(
            engine_settings.models_by_project,
            {project_id: "project-specialized"},
        )

    def test_rejects_missing_default_engine(self):
        with self.assertRaisesRegex(
            ConfigurationError,
            "AGENTFORGE_DEFAULT_ENGINE must not be empty",
        ):
            load_settings(
                {"AGENTFORGE_DEFAULT_MODEL": "general-default"}
            )

    def test_rejects_invalid_model_mapping_json(self):
        with self.assertRaisesRegex(
            ConfigurationError,
            "must be a valid JSON object",
        ):
            load_settings(
                {"AGENTFORGE_MODELS_BY_AGENT": "not-json"}
            )

    def test_rejects_invalid_project_uuid(self):
        with self.assertRaisesRegex(
            ConfigurationError,
            "invalid project UUID",
        ):
            load_settings(
                {
                    "AGENTFORGE_MODELS_BY_PROJECT": (
                        '{"not-a-uuid": "project-specialized"}'
                    )
                }
            )

    def test_loads_adapter_settings_and_default_task_routing(self):
        settings = load_settings(
            {
                "POSTGRES_HOST": "postgres",
                "POSTGRES_PORT": "5432",
                "POSTGRES_DB": "agent_forge",
                "POSTGRES_USER": "agent_forge",
                "POSTGRES_PASSWORD": "database-key",
                "RABBITMQ_HOST": "rabbitmq",
                "RABBITMQ_PORT": "5672",
                "RABBITMQ_USER": "agent_forge",
                "RABBITMQ_PASSWORD": "broker-key",
                "AGENTFORGE_DEFAULT_ENGINE": "litellm",
                "AGENTFORGE_DEFAULT_MODEL": "general-default",
                "LITELLM_BASE_URL": "http://litellm:4000",
                "LITELLM_API_KEY": "gateway-key",
                "LITELLM_TIMEOUT_SECONDS": "240",
            }
        )

        self.assertEqual(settings.database.host, "postgres")
        self.assertEqual(settings.database.database, "agent_forge")
        self.assertEqual(settings.rabbitmq.host, "rabbitmq")
        self.assertEqual(settings.rabbitmq.port, 5672)
        self.assertEqual(
            settings.rabbitmq.task_queues[AgentRole.ARCHITECT],
            "tasks.architect",
        )
        self.assertEqual(
            settings.rabbitmq.task_queues[AgentRole.FRONTEND],
            "tasks.frontend",
        )
        self.assertEqual(
            settings.rabbitmq.task_queues[AgentRole.REVIEWER],
            "tasks.reviewer",
        )
        self.assertEqual(settings.litellm.base_url, "http://litellm:4000")
        self.assertEqual(settings.litellm.timeout_seconds, 240.0)

    def test_litellm_timeout_defaults_to_600_seconds(self):
        settings = load_settings(
            {
                "AGENTFORGE_DEFAULT_ENGINE": "litellm",
                "AGENTFORGE_DEFAULT_MODEL": "general-default",
            }
        )

        self.assertEqual(settings.litellm.timeout_seconds, 600.0)

    def test_qa_execution_policy_is_explicit_and_deny_by_default(self):
        settings = load_settings(
            {
                "AGENTFORGE_DEFAULT_ENGINE": "litellm",
                "AGENTFORGE_DEFAULT_MODEL": "general-default",
            }
        )

        self.assertEqual(settings.qa_execution.executable_rules, ())
        self.assertFalse(settings.qa_execution.network_access)
        self.assertEqual(settings.qa_execution.max_output_bytes, 65536)

    def test_loads_exact_qa_executable_policy(self):
        settings = load_settings(
            {
                "AGENTFORGE_DEFAULT_ENGINE": "litellm",
                "AGENTFORGE_DEFAULT_MODEL": "general-default",
                "AGENTFORGE_QA_EXECUTABLE_POLICY": (
                    '{"quality-tool":{"allowed_arguments":[["verify"]],'
                    '"allowed_working_directories":["backend"],'
                    '"max_timeout_seconds":30}}'
                ),
                "AGENTFORGE_QA_MAX_OUTPUT_BYTES": "2048",
            }
        )

        rule = settings.qa_execution.executable_rules[0]
        self.assertEqual(rule.executable, "quality-tool")
        self.assertEqual(rule.allowed_arguments, (("verify",),))
        self.assertFalse(settings.qa_execution.network_access)


if __name__ == "__main__":
    unittest.main()
