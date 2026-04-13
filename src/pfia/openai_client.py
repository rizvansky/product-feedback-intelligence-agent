from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from pfia.errors import PFIAError


JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass
class OpenAIResponse:
    """Normalized response returned by the OpenAI chat wrapper."""

    text: str
    model: str
    usage: dict[str, Any]
    finish_reason: str | None = None


class OpenAIClient:
    """Small HTTP client for OpenAI chat completions used by PFIA agents."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        default_model: str,
        timeout_s: float = 30.0,
        max_retries: int = 2,
        http_client: httpx.Client | None = None,
    ) -> None:
        """Store connection parameters for OpenAI API access.

        Args:
            api_key: Secret API key for OpenAI Platform.
            base_url: API base URL, usually ``https://api.openai.com/v1``.
            default_model: Default generation model name.
            timeout_s: Request timeout in seconds.
            max_retries: Number of retries for transient upstream failures.
            http_client: Optional preconfigured HTTP client, primarily for tests.
        """
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self._http_client = http_client

    @property
    def available(self) -> bool:
        """Return whether the client has enough config to call OpenAI."""
        return bool(self.api_key and self.default_model)

    def complete_text(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 800,
    ) -> OpenAIResponse:
        """Generate plain text from a chat-style prompt.

        Args:
            messages: Ordered chat messages for the model.
            model: Optional explicit model override.
            temperature: Sampling temperature.
            max_tokens: Maximum completion size.

        Returns:
            Normalized response object.

        Raises:
            PFIAError: If the upstream call fails or returns an invalid payload.
        """
        return self._request_completion(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 1200,
    ) -> dict[str, Any]:
        """Generate a JSON object from a chat-style prompt.

        Args:
            messages: Ordered chat messages for the model.
            model: Optional explicit model override.
            temperature: Sampling temperature.
            max_tokens: Maximum completion size.

        Returns:
            Parsed JSON object.

        Raises:
            PFIAError: If the upstream call fails or the reply is not valid JSON.
        """
        response = self._request_completion(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
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
        """Call the Chat Completions endpoint with light retry handling."""
        if not self.available:
            raise PFIAError(
                "OPENAI_NOT_CONFIGURED",
                "OpenAI generation was requested, but the API key or model is missing.",
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
                    return OpenAIResponse(
                        text=_extract_message_text(data),
                        model=str(data.get("model") or payload["model"]),
                        usage=data.get("usage", {}) or {},
                        finish_reason=_extract_finish_reason(data),
                    )
                except (httpx.HTTPError, ValueError) as exc:
                    retryable = attempt < self.max_retries and _is_retryable_http_error(
                        exc
                    )
                    if retryable:
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    raise PFIAError(
                        "OPENAI_UPSTREAM_FAILED",
                        f"OpenAI request failed: {exc}",
                        status_code=502,
                        retryable=False,
                    ) from exc
            raise PFIAError(
                "OPENAI_UPSTREAM_FAILED",
                "OpenAI request failed after retries.",
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
            "OPENAI_INVALID_JSON",
            "OpenAI returned a response that could not be parsed as JSON.",
            status_code=502,
        )


def _extract_message_text(payload: dict[str, Any]) -> str:
    """Extract the assistant text from a Chat Completions payload."""
    choices = payload.get("choices") or []
    if not choices:
        raise PFIAError(
            "OPENAI_EMPTY_RESPONSE",
            "OpenAI returned no completion choices.",
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


def _is_retryable_http_error(exc: Exception) -> bool:
    """Return whether the upstream failure is worth retrying."""
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {408, 409, 429, 500, 502, 503, 504}
    if isinstance(exc, httpx.NetworkError):
        return True
    return False
