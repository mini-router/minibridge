from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import json
import uuid

from .models import LLMRequest, LLMResponse, Usage


@dataclass(frozen=True)
class ProviderDescriptor:
    provider_id: str
    provider_kind: str
    endpoint_url: str | None = None
    payload_style: str | None = None
    auth_header: str | None = None
    auth_scheme: str | None = None
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float | None = None
    notes: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "provider_kind": self.provider_kind,
            "endpoint_url": self.endpoint_url,
            "payload_style": self.payload_style,
            "auth_header": self.auth_header,
            "auth_scheme": self.auth_scheme,
            "extra_headers": dict(self.extra_headers),
            "timeout_seconds": self.timeout_seconds,
            "notes": dict(self.notes),
        }


class LLMProvider(Protocol):
    provider_id: str
    provider_kind: str
    endpoint_url: str | None

    def describe(self) -> ProviderDescriptor:
        raise NotImplementedError

    def complete(self, api_key: str, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError


@dataclass
class ProviderRegistry:
    providers: dict[str, LLMProvider] = field(default_factory=dict)

    def register(self, provider: LLMProvider) -> None:
        self.providers[provider.provider_id] = provider

    def get(self, provider_id: str) -> LLMProvider:
        try:
            return self.providers[provider_id]
        except KeyError as exc:
            raise KeyError(f"unknown provider {provider_id!r}") from exc

    def list(self) -> list[ProviderDescriptor]:
        return [provider.describe() for provider in self.providers.values()]


def _count_tokens(text: str) -> int:
    return len(text.split()) if text.strip() else 0


def _count_message_tokens(messages: list[dict]) -> int:
    total = 0
    for message in messages:
        total += _count_tokens(str(message.get("role", "")))
        total += _count_tokens(str(message.get("content", "")))
    return total


def _extract_content(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        raise ValueError("provider response is not an object")

    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    content = payload.get("content")
    if isinstance(content, str) and content.strip():
        return content

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first, dict) else None
        if isinstance(message, dict):
            message_content = message.get("content")
            if isinstance(message_content, str):
                return message_content
        choice_text = first.get("text") if isinstance(first, dict) else None
        if isinstance(choice_text, str):
            return choice_text

    output = payload.get("output")
    if isinstance(output, list):
        chunks: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            item_content = item.get("content")
            if isinstance(item_content, str):
                chunks.append(item_content)
                continue
            if isinstance(item_content, list):
                for piece in item_content:
                    if isinstance(piece, dict):
                        text = piece.get("text") or piece.get("content")
                        if isinstance(text, str):
                            chunks.append(text)
        if chunks:
            return "".join(chunks)

    raise ValueError("could not extract assistant content from provider response")


def _extract_usage(payload: Any, request: LLMRequest, content: str) -> Usage:
    if not isinstance(payload, dict):
        raise ValueError("provider response is not an object")

    usage_payload = payload.get("usage") or {}
    if not isinstance(usage_payload, dict):
        usage_payload = {}

    input_tokens = usage_payload.get("input_tokens", usage_payload.get("prompt_tokens"))
    output_tokens = usage_payload.get("output_tokens", usage_payload.get("completion_tokens"))
    total_tokens = usage_payload.get("total_tokens")

    if input_tokens is None and total_tokens is not None and output_tokens is not None:
        input_tokens = int(total_tokens) - int(output_tokens)
    if output_tokens is None and total_tokens is not None and input_tokens is not None:
        output_tokens = int(total_tokens) - int(input_tokens)
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = int(input_tokens) + int(output_tokens)

    if input_tokens is None:
        input_tokens = _count_message_tokens([dict(message) for message in request.messages])
    if output_tokens is None:
        output_tokens = _count_tokens(content)
    if total_tokens is None:
        total_tokens = int(input_tokens) + int(output_tokens)

    return Usage(
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        total_tokens=int(total_tokens),
    )


def _extract_provider_request_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return f"prov_{uuid.uuid4().hex}"
    for key in ("provider_request_id", "request_id", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return f"prov_{uuid.uuid4().hex}"


@dataclass
class MockProvider:
    provider_id: str = "mock"
    provider_kind: str = "mock"
    endpoint_url: str | None = None

    def describe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=self.provider_id,
            provider_kind=self.provider_kind,
            endpoint_url=self.endpoint_url,
            payload_style="mock",
        )

    def complete(self, api_key: str, request: LLMRequest) -> LLMResponse:
        del api_key
        input_tokens = _count_message_tokens([dict(message) for message in request.messages])
        last_user = ""
        for message in reversed(request.messages):
            if message.get("role") == "user":
                last_user = str(message.get("content", ""))
                break

        digest = sha256(request.fingerprint().encode("utf-8")).hexdigest()[:16]
        content = f"[{self.provider_id}:{request.model}] {last_user}".strip()
        content = f"{content} :: proof-{digest}"
        output_tokens = _count_tokens(content)
        return LLMResponse(
            response_id=f"resp_{uuid.uuid4().hex}",
            provider_request_id=f"prov_{uuid.uuid4().hex}",
            content=content,
            usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
            finish_reason="stop",
            metadata={"provider": self.provider_id, "provider_kind": self.provider_kind},
        )


@dataclass
class OpenAICompatibleProvider:
    provider_id: str
    endpoint_url: str
    provider_kind: str = "openai-compatible"
    payload_style: str = "responses"
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float = 30.0

    def describe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=self.provider_id,
            provider_kind=self.provider_kind,
            endpoint_url=self.endpoint_url,
            payload_style=self.payload_style,
            auth_header=self.auth_header,
            auth_scheme=self.auth_scheme,
            extra_headers=self.extra_headers,
            timeout_seconds=self.timeout_seconds,
        )

    def _build_body(self, request: LLMRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.model,
            "metadata": {
                "request_id": request.request_id,
                "provider_id": request.provider_id,
                "caller_id": request.caller_id,
                "owner_id": request.owner_id,
                "key_id": request.key_id,
                **dict(request.metadata),
            },
            **dict(request.parameters),
        }
        if self.payload_style == "chat-completions":
            body["messages"] = [dict(message) for message in request.messages]
        else:
            body["input"] = [dict(message) for message in request.messages]
        return body

    def complete(self, api_key: str, request: LLMRequest) -> LLMResponse:
        body = self._build_body(request)
        headers = {
            "Content-Type": "application/json",
            self.auth_header: f"{self.auth_scheme} {api_key}".strip(),
        }
        headers.update(dict(self.extra_headers))
        data = json.dumps(body).encode("utf-8")
        http_request = Request(self.endpoint_url, data=data, headers=headers, method="POST")
        try:
            with urlopen(http_request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{self.provider_id} request failed with HTTP {exc.code}: {error_body}") from exc
        except URLError as exc:
            raise RuntimeError(f"{self.provider_id} request failed: {exc.reason}") from exc

        payload = json.loads(raw.decode("utf-8"))
        content = _extract_content(payload)
        usage = _extract_usage(payload, request, content)
        provider_request_id = _extract_provider_request_id(payload)
        finish_reason = None
        if isinstance(payload, dict):
            finish_reason = payload.get("finish_reason")
            choices = payload.get("choices")
            if finish_reason is None and isinstance(choices, list) and choices:
                first = choices[0] if isinstance(choices[0], dict) else {}
                finish_reason = first.get("finish_reason")

        response_id = provider_request_id if provider_request_id else f"resp_{uuid.uuid4().hex}"
        return LLMResponse(
            response_id=response_id,
            provider_request_id=provider_request_id,
            content=content,
            usage=usage,
            finish_reason=finish_reason,
            metadata={
                "provider": self.provider_id,
                "provider_kind": self.provider_kind,
                "endpoint_url": self.endpoint_url,
            },
        )


def make_openai_provider(
    provider_id: str = "openai",
    *,
    endpoint_url: str = "https://api.openai.com/v1/responses",
    payload_style: str = "responses",
    extra_headers: Mapping[str, str] | None = None,
    timeout_seconds: float = 30.0,
) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        provider_id=provider_id,
        endpoint_url=endpoint_url,
        provider_kind="openai",
        payload_style=payload_style,
        extra_headers=dict(extra_headers or {}),
        timeout_seconds=timeout_seconds,
    )


def make_openrouter_provider(
    provider_id: str = "openrouter",
    *,
    endpoint_url: str = "https://openrouter.ai/api/v1/chat/completions",
    payload_style: str = "chat-completions",
    extra_headers: Mapping[str, str] | None = None,
    timeout_seconds: float = 30.0,
) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        provider_id=provider_id,
        endpoint_url=endpoint_url,
        provider_kind="openrouter",
        payload_style=payload_style,
        extra_headers=dict(extra_headers or {}),
        timeout_seconds=timeout_seconds,
    )


def make_chutes_provider(
    provider_id: str = "chutes",
    *,
    endpoint_url: str = "https://llm.chutes.ai/v1",
    payload_style: str = "responses",
    extra_headers: Mapping[str, str] | None = None,
    timeout_seconds: float = 30.0,
) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        provider_id=provider_id,
        endpoint_url=endpoint_url,
        provider_kind="chutes",
        payload_style=payload_style,
        extra_headers=dict(extra_headers or {}),
        timeout_seconds=timeout_seconds,
    )


def build_provider_from_payload(payload: Mapping[str, Any]) -> LLMProvider:
    provider_id = str(payload["provider_id"])
    provider_kind = str(payload.get("provider_kind") or "openai-compatible")
    endpoint_url = payload.get("endpoint_url")
    payload_style = str(payload.get("payload_style") or "responses")
    auth_header = str(payload.get("auth_header") or "Authorization")
    auth_scheme = str(payload.get("auth_scheme") or "Bearer")
    extra_headers = dict(payload.get("extra_headers") or {})
    timeout_seconds_value = payload.get("timeout_seconds")
    timeout_seconds = float(timeout_seconds_value if timeout_seconds_value is not None else 30.0)

    if provider_kind == "mock":
        return MockProvider(provider_id=provider_id, endpoint_url=endpoint_url)
    if provider_kind == "openai":
        return make_openai_provider(
            provider_id=provider_id,
            endpoint_url=endpoint_url or "https://api.openai.com/v1/responses",
            payload_style=payload_style,
            extra_headers=extra_headers,
            timeout_seconds=timeout_seconds,
        )
    if provider_kind == "openrouter":
        return make_openrouter_provider(
            provider_id=provider_id,
            endpoint_url=endpoint_url or "https://openrouter.ai/api/v1/chat/completions",
            payload_style=payload_style,
            extra_headers=extra_headers,
            timeout_seconds=timeout_seconds,
        )
    if provider_kind == "chutes":
        return make_chutes_provider(
            provider_id=provider_id,
            endpoint_url=endpoint_url or "https://llm.chutes.ai/v1",
            payload_style=payload_style,
            extra_headers=extra_headers,
            timeout_seconds=timeout_seconds,
        )
    return OpenAICompatibleProvider(
        provider_id=provider_id,
        endpoint_url=str(endpoint_url),
        provider_kind=provider_kind,
        payload_style=payload_style,
        auth_header=auth_header,
        auth_scheme=auth_scheme,
        extra_headers=extra_headers,
        timeout_seconds=timeout_seconds,
    )
