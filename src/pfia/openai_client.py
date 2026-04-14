from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from pfia.errors import PFIAError
from pfia.observability import record_provider_call


JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass
class OpenAIResponse:
    """Normalized response returned by the chat wrapper."""

    text: str
    model: str
    usage: dict[str, Any]
    finish_reason: str | None = None
    provider: str = "openai"


class OpenAIClient:
    """Small HTTP client for OpenAI-compatible chat completions."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        default_model: str,
        timeout_s: float = 30.0,
        max_retries: int = 2,
        http_client: httpx.Client | None = None,
        provider_name: str = "openai",
        supports_response_format: bool = True,
    ) -> None:
        """Store connection parameters for chat completion API access.

        Args:
            api_key: Secret API key for the upstream provider.
            base_url: API base URL.
            default_model: Default generation model name.
            timeout_s: Request timeout in seconds.
            max_retries: Number of retries for transient upstream failures.
            http_client: Optional preconfigured HTTP client, primarily for tests.
            provider_name: Human-readable provider identifier.
            supports_response_format: Whether the provider supports structured JSON response hints.
        """
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self._http_client = http_client
        self.provider_name = provider_name
        self.supports_response_format = supports_response_format
        self.last_provider_used: str | None = None
        self.last_model_used: str | None = None

    @property
    def available(self) -> bool:
        """Return whether the client has enough config to call the provider."""

        return bool(self.api_key and self.default_model)

    def complete_text(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 800,
    ) -> OpenAIResponse:
        """Generate plain text from a chat-style prompt."""

        response = self._request_completion(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self.last_provider_used = response.provider
        self.last_model_used = response.model
        return response

    def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 1200,
    ) -> dict[str, Any]:
        """Generate a JSON object from a chat-style prompt."""

        response = self._request_completion(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=(
                {"type": "json_object"} if self.supports_response_format else None
            ),
        )
        self.last_provider_used = response.provider
        self.last_model_used = response.model
        return self._parse_json(response.text)

    def _request_completion(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None,
        temperature: float,
        max_tokens: int,
        response_format: dict[str, Any] | None = None,
    ) -> OpenAIResponse:
        """Call the chat completions endpoint with light retry handling."""

        if not self.available:
            raise PFIAError(
                "LLM_PROVIDER_NOT_CONFIGURED",
                f"{self.provider_name} generation was requested, but the API key or model is missing.",
                status_code=500,
            )

        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            payload["response_format"] = response_format

        client = self._http_client or httpx.Client(timeout=self.timeout_s)
        should_close = self._http_client is None

        try:
            for attempt in range(self.max_retries + 1):
                started = time.perf_counter()
                try:
                    response = client.post(
                        f"{self.base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                    response.raise_for_status()
                    data = response.json()
                    payload = OpenAIResponse(
                        text=_extract_message_text(data),
                        model=str(data.get("model") or payload["model"]),
                        usage=data.get("usage", {}) or {},
                        finish_reason=_extract_finish_reason(data),
                        provider=self.provider_name,
                    )
                    record_provider_call(
                        kind="llm",
                        provider=self.provider_name,
                        model=payload.model,
                        status="success",
                        latency_s=time.perf_counter() - started,
                        usage=payload.usage,
                    )
                    return payload
                except (httpx.HTTPError, ValueError) as exc:
                    error_code = (
                        exc.code
                        if isinstance(exc, PFIAError)
                        else "LLM_UPSTREAM_FAILED"
                    )
                    record_provider_call(
                        kind="llm",
                        provider=self.provider_name,
                        model=payload["model"],
                        status="error",
                        latency_s=time.perf_counter() - started,
                        error_code=error_code,
                    )
                    retryable = attempt < self.max_retries and _is_retryable_http_error(
                        exc
                    )
                    if retryable:
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    raise PFIAError(
                        "LLM_UPSTREAM_FAILED",
                        f"{self.provider_name} request failed: {exc}",
                        status_code=502,
                        retryable=False,
                    ) from exc
            raise PFIAError(
                "LLM_UPSTREAM_FAILED",
                f"{self.provider_name} request failed after retries.",
                status_code=502,
            )
        finally:
            if should_close:
                client.close()

    def _parse_json(self, content: str) -> dict[str, Any]:
        """Parse a JSON object from raw model text."""

        candidates = [content.strip()]
        fenced = JSON_BLOCK_RE.search(content)
        if fenced:
            candidates.append(fenced.group(1).strip())
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(content[start : end + 1].strip())

        for candidate in candidates:
            if not candidate:
                continue
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed

        raise PFIAError(
            "LLM_INVALID_JSON",
            f"{self.provider_name} returned a response that could not be parsed as JSON.",
            status_code=502,
        )


class MistralClient(OpenAIClient):
    """Mistral chat client using the same normalized interface."""

    def __init__(self, **kwargs: Any) -> None:
        """Initialize a Mistral chat client."""

        super().__init__(
            provider_name="mistral",
            supports_response_format=False,
            **kwargs,
        )


class AnthropicClient:
    """Anthropic Messages API client with the same normalized interface."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        default_model: str,
        timeout_s: float = 30.0,
        max_retries: int = 2,
        http_client: httpx.Client | None = None,
        provider_name: str = "anthropic",
        api_version: str = "2023-06-01",
    ) -> None:
        """Store connection parameters for Anthropic Messages API access."""

        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self._http_client = http_client
        self.provider_name = provider_name
        self.api_version = api_version
        self.last_provider_used: str | None = None
        self.last_model_used: str | None = None

    @property
    def available(self) -> bool:
        """Return whether the client has enough config to call Anthropic."""

        return bool(self.api_key and self.default_model)

    def complete_text(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 800,
    ) -> OpenAIResponse:
        """Generate plain text from a chat-style prompt."""

        response = self._request_completion(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self.last_provider_used = response.provider
        self.last_model_used = response.model
        return response

    def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 1200,
    ) -> dict[str, Any]:
        """Generate a JSON object from a chat-style prompt."""

        response = self._request_completion(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
        )
        self.last_provider_used = response.provider
        self.last_model_used = response.model
        return _parse_json_from_text(response.text, provider_name=self.provider_name)

    def _request_completion(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None,
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
    ) -> OpenAIResponse:
        """Call Anthropic Messages API with light retry handling."""

        if not self.available:
            raise PFIAError(
                "LLM_PROVIDER_NOT_CONFIGURED",
                f"{self.provider_name} generation was requested, but the API key or model is missing.",
                status_code=500,
            )

        system_prompt, anthropic_messages = _prepare_anthropic_messages(
            messages, json_mode=json_mode
        )
        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": anthropic_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system_prompt:
            payload["system"] = system_prompt

        client = self._http_client or httpx.Client(timeout=self.timeout_s)
        should_close = self._http_client is None

        try:
            for attempt in range(self.max_retries + 1):
                started = time.perf_counter()
                try:
                    response = client.post(
                        f"{self.base_url}/messages",
                        headers={
                            "x-api-key": self.api_key,
                            "anthropic-version": self.api_version,
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                    response.raise_for_status()
                    data = response.json()
                    payload = OpenAIResponse(
                        text=_extract_anthropic_text(data),
                        model=str(data.get("model") or payload["model"]),
                        usage=data.get("usage", {}) or {},
                        finish_reason=_extract_anthropic_finish_reason(data),
                        provider=self.provider_name,
                    )
                    record_provider_call(
                        kind="llm",
                        provider=self.provider_name,
                        model=payload.model,
                        status="success",
                        latency_s=time.perf_counter() - started,
                        usage=payload.usage,
                    )
                    return payload
                except (httpx.HTTPError, ValueError) as exc:
                    record_provider_call(
                        kind="llm",
                        provider=self.provider_name,
                        model=payload["model"],
                        status="error",
                        latency_s=time.perf_counter() - started,
                        error_code="LLM_UPSTREAM_FAILED",
                    )
                    retryable = attempt < self.max_retries and _is_retryable_http_error(
                        exc
                    )
                    if retryable:
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    raise PFIAError(
                        "LLM_UPSTREAM_FAILED",
                        f"{self.provider_name} request failed: {exc}",
                        status_code=502,
                        retryable=False,
                    ) from exc
            raise PFIAError(
                "LLM_UPSTREAM_FAILED",
                f"{self.provider_name} request failed after retries.",
                status_code=502,
            )
        finally:
            if should_close:
                client.close()


class FallbackRoutingClient:
    """Route generation requests across primary and fallback providers."""

    def __init__(
        self,
        *,
        primary: Any | None,
        fallbacks: list[Any] | None = None,
    ) -> None:
        """Store the primary and fallback provider clients."""

        self.primary = primary
        self.fallbacks = fallbacks or []
        self.last_provider_used: str | None = None
        self.last_model_used: str | None = None

    @property
    def available(self) -> bool:
        """Return whether any external provider is currently callable."""

        return any(
            client is not None and client.available for client in self._clients()
        )

    @property
    def default_model(self) -> str:
        """Return the most relevant default model for diagnostics."""

        for client in self._clients():
            if client is not None and client.default_model:
                return client.default_model
        return ""

    def complete_text(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 800,
    ) -> OpenAIResponse:
        """Generate text through the primary provider with fallback support."""

        last_error: PFIAError | None = None
        for client in self._clients():
            if client is None or not client.available:
                continue
            try:
                response = client.complete_text(
                    messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                self.last_provider_used = response.provider
                self.last_model_used = response.model
                return response
            except PFIAError as exc:
                last_error = exc
                continue
        raise last_error or PFIAError(
            "LLM_PROVIDER_NOT_CONFIGURED",
            "No external LLM provider is configured.",
            status_code=500,
        )

    def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 1200,
    ) -> dict[str, Any]:
        """Generate JSON through the primary provider with fallback support."""

        last_error: PFIAError | None = None
        for client in self._clients():
            if client is None or not client.available:
                continue
            try:
                result = client.complete_json(
                    messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                self.last_provider_used = (
                    client.last_provider_used or client.provider_name
                )
                self.last_model_used = client.last_model_used or client.default_model
                return result
            except PFIAError as exc:
                last_error = exc
                continue
        raise last_error or PFIAError(
            "LLM_PROVIDER_NOT_CONFIGURED",
            "No external LLM provider is configured.",
            status_code=500,
        )

    def _clients(self) -> tuple[Any | None, ...]:
        """Return provider clients in call order."""

        return (self.primary, *self.fallbacks)


def _extract_message_text(payload: dict[str, Any]) -> str:
    """Extract the assistant text from a chat completions payload."""

    choices = payload.get("choices") or []
    if not choices:
        raise PFIAError(
            "LLM_EMPTY_RESPONSE",
            "The provider returned no completion choices.",
            status_code=502,
        )
    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                elif isinstance(text, dict) and isinstance(text.get("value"), str):
                    parts.append(text["value"])
                elif isinstance(item.get("content"), str):
                    parts.append(str(item["content"]))
        return "\n".join(part for part in parts if part).strip()
    return str(content)


def _extract_finish_reason(payload: dict[str, Any]) -> str | None:
    """Return the finish reason for the first choice when present."""

    choices = payload.get("choices") or []
    if not choices:
        return None
    finish_reason = choices[0].get("finish_reason")
    return None if finish_reason is None else str(finish_reason)


def _extract_anthropic_text(payload: dict[str, Any]) -> str:
    """Extract assistant text from an Anthropic Messages response."""

    content = payload.get("content") or []
    if not content:
        raise PFIAError(
            "LLM_EMPTY_RESPONSE",
            "Anthropic returned no content blocks.",
            status_code=502,
        )
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            parts.append(item["text"])
    text = "\n".join(part for part in parts if part).strip()
    if not text:
        raise PFIAError(
            "LLM_EMPTY_RESPONSE",
            "Anthropic returned content without text blocks.",
            status_code=502,
        )
    return text


def _extract_anthropic_finish_reason(payload: dict[str, Any]) -> str | None:
    """Return the finish reason for an Anthropic response."""

    stop_reason = payload.get("stop_reason")
    return None if stop_reason is None else str(stop_reason)


def _prepare_anthropic_messages(
    messages: list[dict[str, str]], *, json_mode: bool
) -> tuple[str, list[dict[str, str]]]:
    """Convert generic chat messages into Anthropic Messages payload."""

    system_parts: list[str] = []
    anthropic_messages: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = str(message.get("content") or "")
        if role == "system":
            system_parts.append(content)
            continue
        normalized_role = "assistant" if role == "assistant" else "user"
        anthropic_messages.append({"role": normalized_role, "content": content})
    if json_mode:
        system_parts.append(
            "Return only valid JSON matching the requested schema. Do not add markdown fences."
        )
    if not anthropic_messages:
        anthropic_messages.append({"role": "user", "content": "Continue."})
    return "\n\n".join(
        part for part in system_parts if part
    ).strip(), anthropic_messages


def _parse_json_from_text(content: str, *, provider_name: str) -> dict[str, Any]:
    """Parse a JSON object from raw provider text."""

    candidates = [content.strip()]
    fenced = JSON_BLOCK_RE.search(content)
    if fenced:
        candidates.append(fenced.group(1).strip())
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(content[start : end + 1].strip())

    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise PFIAError(
        "LLM_INVALID_JSON",
        f"{provider_name} returned a response that could not be parsed as JSON.",
        status_code=502,
    )


def _is_retryable_http_error(exc: Exception) -> bool:
    """Return whether the upstream failure is worth retrying."""

    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {408, 409, 429, 500, 502, 503, 504}
    if isinstance(exc, httpx.NetworkError):
        return True
    return False
