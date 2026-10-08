import json
import socket
from collections.abc import Callable
from dataclasses import asdict
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from core.contracts.configuration import LiteLLMSettings
from core.contracts.context import AgentContext
from core.contracts.engines import (
    AgentEngine,
    EngineConfigurationError,
    EngineHTTPError,
    EngineResponseError,
    EngineResult,
    EngineTimeoutError,
)
from core.contracts.model_selection import ModelSelector
from core.models.project import Project
from core.models.task import Task


class LiteLLMEngine(AgentEngine):
    def __init__(
        self,
        model_selector: ModelSelector,
        settings: LiteLLMSettings,
        http_open: Callable[..., Any] | None = None,
    ):
        if not settings.base_url.strip():
            raise EngineConfigurationError(
                "LITELLM_BASE_URL must not be empty"
            )
        if not settings.api_key.strip():
            raise EngineConfigurationError(
                "LITELLM_API_KEY must not be empty"
            )

        self.model_selector = model_selector
        self.base_url = settings.base_url.rstrip("/")
        self.api_key = settings.api_key
        self.timeout_seconds = settings.timeout_seconds
        self.http_open = http_open or urlopen

    def run(
        self,
        task: Task,
        project: Project,
        context: AgentContext | None = None,
    ) -> EngineResult:
        model_alias = self.model_selector.resolve(task, project)
        request = self._build_request(task, project, context, model_alias)

        try:
            with self.http_open(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                status = getattr(response, "status", 200)
                response_body = response.read()
        except HTTPError as error:
            raise EngineHTTPError(
                f"LiteLLM proxy returned HTTP {error.code}"
            ) from error
        except (TimeoutError, socket.timeout) as error:
            raise EngineTimeoutError(
                "LiteLLM proxy request timed out"
            ) from error
        except URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise EngineTimeoutError(
                    "LiteLLM proxy request timed out"
                ) from error
            raise EngineHTTPError(
                f"LiteLLM proxy request failed: {error.reason}"
            ) from error
        except OSError as error:
            raise EngineHTTPError(
                f"LiteLLM proxy request failed: {error}"
            ) from error

        if status >= 400:
            raise EngineHTTPError(
                f"LiteLLM proxy returned HTTP {status}"
            )

        return self._parse_response(response_body, model_alias)

    def _build_request(
        self,
        task: Task,
        project: Project,
        context: AgentContext | None,
        model_alias: str,
    ) -> Request:
        instruction = {
            "task": asdict(task),
            "project": asdict(project),
            "context": context or {},
        }
        payload = {
            "model": model_alias,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an AgentForge engine. Use only the Task, "
                        "Project and context supplied by AgentForge."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        instruction,
                        default=str,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                },
            ],
        }
        if context is not None and "output_contract" in context:
            output_contract = dict(context["output_contract"])
            output_contract.pop("instruction", None)
            payload["temperature"] = 0
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "agentforge_output",
                    "strict": True,
                    "schema": output_contract,
                },
            }

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        return Request(
            url=f"{self.base_url}/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

    @staticmethod
    def _parse_response(
        response_body: bytes,
        model_alias: str,
    ) -> EngineResult:
        try:
            response = json.loads(response_body)
        except (TypeError, json.JSONDecodeError) as error:
            raise EngineResponseError(
                "LiteLLM proxy returned invalid JSON"
            ) from error

        try:
            output = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise EngineResponseError(
                "LiteLLM proxy response lacks message content"
            ) from error

        if not isinstance(output, str):
            raise EngineResponseError(
                "LiteLLM proxy response content must be a string"
            )

        metadata = {
            key: response[key]
            for key in ("id", "usage")
            if key in response
        }
        metadata["model_alias"] = model_alias
        return EngineResult(
            output=output,
            metadata=metadata,
            provider="litellm",
            model_alias=model_alias,
        )
