import json
import os
import socket
from collections.abc import Callable, Mapping
from dataclasses import asdict
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from core.engines.base import (
    AgentEngine,
    EngineConfigurationError,
    EngineHTTPError,
    EngineResponseError,
    EngineResult,
    EngineTimeoutError,
)
from core.engines.model_resolver import ModelResolver
from core.models.project import Project
from core.models.task import Task


class LiteLLMEngine(AgentEngine):
    def __init__(
        self,
        model_resolver: ModelResolver,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 180.0,
        http_open: Callable[..., Any] | None = None,
    ):
        if not base_url.strip():
            raise EngineConfigurationError(
                "LITELLM_BASE_URL must not be empty"
            )
        if not api_key.strip():
            raise EngineConfigurationError(
                "LITELLM_API_KEY must not be empty"
            )

        self.model_resolver = model_resolver
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.http_open = http_open or urlopen

    @classmethod
    def from_environment(
        cls,
        model_resolver: ModelResolver,
        environ: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> "LiteLLMEngine":
        source = os.environ if environ is None else environ
        if "timeout_seconds" in kwargs:
            timeout_seconds = kwargs.pop("timeout_seconds")
        else:
            timeout_seconds = float(
                source.get("LITELLM_TIMEOUT_SECONDS", "180")
            )
        return cls(
            model_resolver=model_resolver,
            base_url=source.get("LITELLM_BASE_URL", ""),
            api_key=source.get("LITELLM_API_KEY", ""),
            timeout_seconds=timeout_seconds,
            **kwargs,
        )

    def run(
        self,
        task: Task,
        project: Project,
        context: dict[str, Any] | None = None,
    ) -> EngineResult:
        model_alias = self.model_resolver.resolve(task, project)
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
        context: dict[str, Any] | None,
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
        )
