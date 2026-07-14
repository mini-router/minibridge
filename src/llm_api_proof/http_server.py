from __future__ import annotations

from dataclasses import asdict, is_dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from typing import Any
import json
import re
import sys

from .models import KeyPolicy, LLMRequest, LLMResponse, Receipt
from .provider import (
    MockProvider,
    OpenAICompatibleProvider,
    ProviderDescriptor,
    ProviderRegistry,
    build_provider_from_payload,
    make_chutes_provider,
    make_openai_provider,
    make_openrouter_provider,
)
from .service import LLMProofService
from .verifier import verify_receipt


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"cannot serialize {type(value)!r}")


def _read_json(handler: BaseHTTPRequestHandler) -> Any:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length) if length else b"{}"
    return json.loads(raw.decode("utf-8"))


def _write_json(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    raw = json.dumps(payload, default=_json_default, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _write_cors_preflight(handler: BaseHTTPRequestHandler) -> None:
    handler.send_response(204)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
    handler.send_header("Access-Control-Max-Age", "600")
    handler.end_headers()


def _parse_provider_call_path(path: str) -> str | None:
    match = re.fullmatch(r"/providers/([^/]+)/call", path)
    if match is None:
        return None
    return match.group(1)


def _parse_provider_describe_path(path: str) -> str | None:
    match = re.fullmatch(r"/providers/([^/]+)", path)
    if match is None:
        return None
    return match.group(1)


def _maybe_save_state(state_save: Callable[[], None] | None) -> None:
    if state_save is None:
        return
    try:
        state_save()
    except Exception as exc:
        print(f"warning: state save failed: {exc}", file=sys.stderr)


def make_handler(service: LLMProofService, provider_registry: ProviderRegistry) -> type[BaseHTTPRequestHandler]:
    return make_handler_with_state(service, provider_registry, None)


def make_handler_with_state(
    service: LLMProofService,
    provider_registry: ProviderRegistry,
    state_save: Callable[[], None] | None,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "llm-api-proof/0.2"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            del format, args

        def _resolve_provider(self, provider_id: str) -> Any:
            return provider_registry.get(provider_id)

        def do_GET(self) -> None:  # noqa: N802
            provider_id = _parse_provider_describe_path(self.path)
            if self.path == "/health":
                _write_json(self, 200, {"ok": True, "service_id": service.service_id})
                return
            if self.path == "/receipts":
                _write_json(self, 200, {"receipts": [receipt.to_dict() for receipt in service.receipts]})
                return
            if self.path == "/providers":
                _write_json(self, 200, {"providers": [descriptor.to_dict() for descriptor in provider_registry.list()]})
                return
            if provider_id is not None:
                try:
                    provider = self._resolve_provider(provider_id)
                    _write_json(self, 200, {"ok": True, "provider": provider.describe().to_dict()})
                except Exception as exc:
                    _write_json(self, 404, {"ok": False, "error": str(exc)})
                return
            self.send_error(404, "not found")

        def do_OPTIONS(self) -> None:  # noqa: N802
            _write_cors_preflight(self)

        def do_POST(self) -> None:  # noqa: N802
            try:
                payload = _read_json(self)
            except Exception as exc:
                _write_json(self, 400, {"ok": False, "error": f"invalid json: {exc}"})
                return

            if self.path == "/register-provider":
                try:
                    provider = build_provider_from_payload(payload)
                    provider_registry.register(provider)
                    _maybe_save_state(state_save)
                    _write_json(self, 200, {"ok": True, "provider": provider.describe().to_dict()})
                except Exception as exc:
                    _write_json(self, 400, {"ok": False, "error": str(exc)})
                return

            if self.path == "/register-key":
                try:
                    policy_payload = payload.get("policy")
                    if policy_payload is None:
                        policy_payload = {
                            "allowed_callers": payload.get("allowed_callers") or [],
                            "allowed_models": payload.get("allowed_models") or [],
                            "spend_limit_usd": payload.get("spend_limit_usd"),
                            "expires_at": payload.get("expires_at"),
                            "require_nonce": payload.get("require_nonce", True),
                            "require_expiry": payload.get("require_expiry", True),
                        }
                    record = service.register_key(
                        owner_id=payload["owner_id"],
                        key_id=payload["key_id"],
                        api_key=payload["api_key"],
                        policy=KeyPolicy.from_dict(policy_payload),
                    )
                    _maybe_save_state(state_save)
                    _write_json(
                        self,
                        200,
                        {
                            "ok": True,
                            "key": {
                                "owner_id": record.owner_id,
                                "key_id": record.key_id,
                                "policy": record.policy.to_dict(),
                            },
                        },
                    )
                except Exception as exc:
                    _write_json(self, 400, {"ok": False, "error": str(exc)})
                return

            provider_call_id = _parse_provider_call_path(self.path)
            if self.path == "/call" or provider_call_id is not None:
                try:
                    request_provider_id = str(payload.get("provider_id") or provider_call_id or "mock")
                    request = LLMRequest(
                        request_id=payload["request_id"],
                        provider_id=request_provider_id,
                        caller_id=payload["caller_id"],
                        owner_id=payload["owner_id"],
                        key_id=payload["key_id"],
                        model=payload["model"],
                        messages=payload["messages"],
                        parameters=payload.get("parameters") or {},
                        metadata=payload.get("metadata") or {},
                        nonce=payload.get("nonce"),
                        expires_at=payload.get("expires_at"),
                    )
                    provider = self._resolve_provider(request.provider_id)
                    response, receipt = service.call(provider, request)
                    _maybe_save_state(state_save)
                    _write_json(
                        self,
                        200,
                        {
                            "ok": True,
                            "provider": provider.describe().to_dict(),
                            "response": response.to_dict(),
                            "receipt": receipt.to_dict(),
                        },
                    )
                except Exception as exc:
                    _write_json(self, 400, {"ok": False, "error": str(exc)})
                return

            if self.path == "/verify":
                try:
                    receipt = Receipt.from_dict(payload["receipt"])
                    request = LLMRequest.from_dict(payload["request"]) if "request" in payload else None
                    response = LLMResponse.from_dict(payload["response"]) if "response" in payload else None
                    result = verify_receipt(
                        receipt,
                        service.signer.public_key,
                        service.pricing_table,
                        request=request,
                        response=response,
                    )
                    _write_json(self, 200, {"ok": True, "result": result.__dict__})
                except Exception as exc:
                    _write_json(self, 400, {"ok": False, "error": str(exc)})
                return

            self.send_error(404, "not found")

    return Handler


def run_server(
    host: str,
    port: int,
    service: LLMProofService,
    provider_registry: ProviderRegistry | None = None,
    state_save: Callable[[], None] | None = None,
) -> ThreadingHTTPServer:
    registry = provider_registry or ProviderRegistry()
    handler = make_handler_with_state(service, registry, state_save)
    server = ThreadingHTTPServer((host, port), handler)
    return server
