import unittest
import io
import json
from urllib.error import HTTPError
from unittest.mock import MagicMock, Mock
from uuid import UUID

from core.contracts.configuration import LiteLLMSettings
from core.contracts.engines import (
    AgentEngine,
    EngineConfigurationError,
    EngineHTTPError,
    EngineResponseError,
    EngineResult,
    EngineTimeoutError,
)
from core.contracts.model_selection import (
    ModelConfigurationError,
    ModelSelector,
)
from core.engines.model_resolver import ModelResolver
from core.engines.resolver import EngineResolver
from core.infrastructure.engines.claude import ClaudeEngine
from core.infrastructure.engines.codex import CodexEngine
from core.infrastructure.engines.litellm import LiteLLMEngine
from core.models.project import Project
from core.models.task import AgentRole, Task


class EngineStubTests(unittest.TestCase):
    def setUp(self):
        self.project = Project(name="AgentForge", description="Agent platform")
        self.task = Task(
            project_id=self.project.id,
            title="Define architecture",
            description="Define the application architecture",
            agent=AgentRole.ARCHITECT,
        )
        self.litellm_settings = LiteLLMSettings(
            base_url="http://litellm:4000",
            api_key="gateway-key",
        )

    def test_codex_stub_returns_engine_result(self):
        result = CodexEngine().run(self.task, self.project, {"feedback": []})

        self.assertIsInstance(result, EngineResult)
        self.assertEqual(result.provider, "codex")
        self.assertEqual(result.metadata, {"stub": True})
        self.assertIn("not implemented", result.output)

    def test_claude_stub_returns_engine_result(self):
        result = ClaudeEngine().run(self.task, self.project)

        self.assertIsInstance(result, EngineResult)
        self.assertEqual(result.provider, "claude")
        self.assertEqual(result.metadata, {"stub": True})
        self.assertIn("not implemented", result.output)

    def test_litellm_posts_task_project_and_context_to_proxy(self):
        model_selector = Mock(spec=ModelSelector)
        model_selector.resolve.return_value = "architecture-primary"
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps(
            {
                "id": "chatcmpl-test",
                "model": "provider/model",
                "choices": [
                    {"message": {"content": "Architecture output"}}
                ],
                "usage": {"total_tokens": 12},
            }
        ).encode()
        response.__enter__.return_value = response
        http_open = Mock(return_value=response)
        context = {
            "project_memory": {"summary": "Known context"},
            "previous_decisions": ["Use PostgreSQL"],
        }
        engine = LiteLLMEngine(
            model_selector,
            self.litellm_settings,
            http_open=http_open,
        )

        result = engine.run(self.task, self.project, context)

        self.assertIsInstance(engine, AgentEngine)
        self.assertIsInstance(result, EngineResult)
        self.assertEqual(result.provider, "litellm")
        self.assertEqual(
            result.metadata,
            {
                "id": "chatcmpl-test",
                "usage": {"total_tokens": 12},
                "model_alias": "architecture-primary",
            },
        )
        self.assertEqual(result.output, "Architecture output")
        model_selector.resolve.assert_called_once_with(
            self.task,
            self.project,
        )
        http_open.assert_called_once()
        request = http_open.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "http://litellm:4000/v1/chat/completions",
        )
        self.assertEqual(
            request.get_header("Authorization"),
            "Bearer gateway-key",
        )
        body = json.loads(request.data)
        self.assertEqual(body["model"], "architecture-primary")
        instruction = json.loads(body["messages"][1]["content"])
        self.assertEqual(instruction["task"]["title"], self.task.title)
        self.assertEqual(instruction["project"]["name"], self.project.name)
        self.assertEqual(instruction["context"], context)
        self.assertEqual(
            http_open.call_args.kwargs["timeout"],
            180.0,
        )

    def test_litellm_uses_only_injected_gateway_settings(self):
        engine = LiteLLMEngine(
            Mock(spec=ModelSelector),
            self.litellm_settings,
        )

        self.assertEqual(engine.base_url, "http://litellm:4000")
        self.assertEqual(engine.api_key, "gateway-key")
        self.assertEqual(engine.timeout_seconds, 180.0)

    def test_litellm_uses_injected_timeout(self):
        engine = LiteLLMEngine(
            Mock(spec=ModelSelector),
            LiteLLMSettings(
                base_url="http://litellm:4000",
                api_key="gateway-key",
                timeout_seconds=240.0,
            ),
        )

        self.assertEqual(engine.timeout_seconds, 240.0)

    def test_litellm_translates_http_error(self):
        model_selector = Mock(spec=ModelSelector)
        model_selector.resolve.return_value = "general-default"
        http_open = Mock(
            side_effect=HTTPError(
                "http://litellm:4000/v1/chat/completions",
                503,
                "unavailable",
                {},
                io.BytesIO(),
            )
        )
        engine = LiteLLMEngine(
            model_selector,
            self.litellm_settings,
            http_open=http_open,
        )

        with self.assertRaisesRegex(EngineHTTPError, "HTTP 503"):
            engine.run(self.task, self.project)

    def test_litellm_translates_timeout(self):
        model_selector = Mock(spec=ModelSelector)
        model_selector.resolve.return_value = "general-default"
        http_open = Mock(side_effect=TimeoutError("timed out"))
        engine = LiteLLMEngine(
            model_selector,
            self.litellm_settings,
            http_open=http_open,
        )

        with self.assertRaisesRegex(
            EngineTimeoutError,
            "request timed out",
        ):
            engine.run(self.task, self.project)

    def test_litellm_translates_invalid_response(self):
        model_selector = Mock(spec=ModelSelector)
        model_selector.resolve.return_value = "general-default"
        response = MagicMock()
        response.status = 200
        response.read.return_value = b'{"choices": []}'
        response.__enter__.return_value = response
        engine = LiteLLMEngine(
            model_selector,
            self.litellm_settings,
            http_open=Mock(return_value=response),
        )

        with self.assertRaisesRegex(
            EngineResponseError,
            "lacks message content",
        ):
            engine.run(self.task, self.project)


class ModelResolverTests(unittest.TestCase):
    def setUp(self):
        self.project_id = UUID("93de5ea5-729a-4c5e-8dc3-443165ed516b")
        self.project = Project(
            id=self.project_id,
            name="AgentForge",
            description="Agent platform",
        )
        self.task = Task(
            project_id=self.project_id,
            title="Define architecture",
            description="Define the application architecture",
            agent=AgentRole.ARCHITECT,
        )

    def test_project_model_has_highest_priority(self):
        resolver = ModelResolver(
            default_model="general-default",
            models_by_agent={"architect": "architecture-primary"},
            models_by_project={self.project_id: "project-specialized"},
        )

        result = resolver.resolve(self.task, self.project)

        self.assertIsInstance(resolver, ModelSelector)
        self.assertEqual(result, "project-specialized")

    def test_agent_model_has_priority_over_default(self):
        resolver = ModelResolver(
            default_model="general-default",
            models_by_agent={"architect": "architecture-primary"},
        )

        result = resolver.resolve(self.task, self.project)

        self.assertEqual(result, "architecture-primary")

    def test_uses_default_model_without_overrides(self):
        resolver = ModelResolver(default_model="general-default")

        result = resolver.resolve(self.task, self.project)

        self.assertEqual(result, "general-default")

    def test_invalid_resolved_alias_raises_explicit_error(self):
        resolver = ModelResolver(default_model="")

        with self.assertRaisesRegex(
            ModelConfigurationError,
            "did not resolve a valid alias",
        ):
            resolver.resolve(self.task, self.project)


class EngineResolverTests(unittest.TestCase):
    def setUp(self):
        self.default_engine = Mock(spec=AgentEngine)
        self.agent_engine = Mock(spec=AgentEngine)
        self.project_engine = Mock(spec=AgentEngine)
        self.project_id = UUID("93de5ea5-729a-4c5e-8dc3-443165ed516b")
        self.project = Project(
            id=self.project_id,
            name="AgentForge",
            description="Agent platform",
        )

    def _task(self, agent=AgentRole.ARCHITECT):
        return Task(
            project_id=self.project_id,
            title="Define architecture",
            description="Define the application architecture",
            agent=agent,
        )

    def test_project_engine_has_highest_priority_and_receives_context(self):
        expected = EngineResult("project", {}, "claude")
        self.project_engine.run.return_value = expected
        context = {"project_memory": {"summary": "Known context"}}
        task = self._task()
        resolver = EngineResolver(
            engines={
                "default": self.default_engine,
                "agent": self.agent_engine,
                "project": self.project_engine,
            },
            default_engine="default",
            engines_by_agent={"architect": "agent"},
            engines_by_project={self.project_id: "project"},
        )

        result = resolver.run(task, self.project, context)

        self.assertIs(result, expected)
        self.project_engine.run.assert_called_once_with(
            task,
            self.project,
            context,
        )
        self.agent_engine.run.assert_not_called()
        self.default_engine.run.assert_not_called()

    def test_agent_engine_has_priority_over_default(self):
        task = self._task()
        resolver = EngineResolver(
            engines={
                "default": self.default_engine,
                "agent": self.agent_engine,
            },
            default_engine="default",
            engines_by_agent={"architect": "agent"},
        )

        resolver.run(task, self.project)

        self.agent_engine.run.assert_called_once_with(task, self.project, None)
        self.default_engine.run.assert_not_called()

    def test_uses_default_engine_without_overrides(self):
        task = self._task(agent=AgentRole.QA)
        resolver = EngineResolver(
            engines={"default": self.default_engine},
            default_engine="default",
        )

        resolver.run(task, self.project)

        self.default_engine.run.assert_called_once_with(
            task,
            self.project,
            None,
        )

    def test_unknown_engine_configuration_raises_explicit_error(self):
        resolver = EngineResolver(
            engines={"default": self.default_engine},
            default_engine="missing",
        )

        with self.assertRaisesRegex(
            EngineConfigurationError,
            "Engine is not registered: missing",
        ):
            resolver.run(self._task(agent=AgentRole.QA), self.project)


if __name__ == "__main__":
    unittest.main()
