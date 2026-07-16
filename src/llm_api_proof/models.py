from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any, Mapping


def _normalize(value: Any) -> Any:
    if is_dataclass(value):
        return _normalize(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _normalize(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, set):
        return [_normalize(item) for item in sorted(value, key=repr)]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return value


def canonical_json(value: Any) -> str:
    normalized = _normalize(value)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(value: Any) -> str:
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        raw = value.encode("utf-8")
    else:
        raw = canonical_json(value).encode("utf-8")
    return sha256(raw).hexdigest()


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    total_tokens: int | None = None

    def resolved_total(self) -> int:
        return self.total_tokens if self.total_tokens is not None else self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.resolved_total(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Usage":
        return cls(
            input_tokens=int(payload["input_tokens"]),
            output_tokens=int(payload["output_tokens"]),
            total_tokens=int(payload["total_tokens"]) if payload.get("total_tokens") is not None else None,
        )


@dataclass(frozen=True)
class KeyPolicy:
    allowed_callers: set[str] = field(default_factory=set)
    allowed_models: set[str] = field(default_factory=set)
    spend_limit_usd: str | None = None
    expires_at: str | None = None
    require_nonce: bool = True
    require_expiry: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_callers": sorted(self.allowed_callers),
            "allowed_models": sorted(self.allowed_models),
            "spend_limit_usd": self.spend_limit_usd,
            "expires_at": self.expires_at,
            "require_nonce": self.require_nonce,
            "require_expiry": self.require_expiry,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "KeyPolicy":
        return cls(
            allowed_callers=set(payload.get("allowed_callers") or []),
            allowed_models=set(payload.get("allowed_models") or []),
            spend_limit_usd=payload.get("spend_limit_usd"),
            expires_at=payload.get("expires_at"),
            require_nonce=bool(payload.get("require_nonce", True)),
            require_expiry=bool(payload.get("require_expiry", True)),
        )


@dataclass(frozen=True)
class LLMRequest:
    request_id: str
    provider_id: str
    caller_id: str
    owner_id: str
    key_id: str
    model: str
    messages: list[Mapping[str, Any]]
    parameters: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    nonce: str | None = None
    expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "provider_id": self.provider_id,
            "caller_id": self.caller_id,
            "owner_id": self.owner_id,
            "key_id": self.key_id,
            "model": self.model,
            "messages": list(self.messages),
            "parameters": dict(self.parameters),
            "metadata": dict(self.metadata),
            "nonce": self.nonce,
            "expires_at": self.expires_at,
        }

    def fingerprint(self) -> str:
        return sha256_hex(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LLMRequest":
        return cls(
            request_id=str(payload["request_id"]),
            provider_id=str(payload.get("provider_id") or "mock"),
            caller_id=str(payload["caller_id"]),
            owner_id=str(payload["owner_id"]),
            key_id=str(payload["key_id"]),
            model=str(payload["model"]),
            messages=list(payload.get("messages") or []),
            parameters=dict(payload.get("parameters") or {}),
            metadata=dict(payload.get("metadata") or {}),
            nonce=payload.get("nonce"),
            expires_at=payload.get("expires_at"),
        )


@dataclass(frozen=True)
class LLMResponse:
    response_id: str
    provider_request_id: str
    content: str
    usage: Usage
    finish_reason: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "response_id": self.response_id,
            "provider_request_id": self.provider_request_id,
            "content": self.content,
            "usage": self.usage.to_dict(),
            "metadata": dict(self.metadata),
        }
        if self.finish_reason is not None:
            payload["finish_reason"] = self.finish_reason
        return payload

    def fingerprint(self) -> str:
        return sha256_hex(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LLMResponse":
        return cls(
            response_id=str(payload["response_id"]),
            provider_request_id=str(payload["provider_request_id"]),
            content=str(payload["content"]),
            usage=Usage.from_dict(payload["usage"]),
            finish_reason=payload.get("finish_reason"),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    service_id: str
    service_public_key_fingerprint: str
    tee_mode: str
    pricing_table_id: str
    request_id: str
    provider_id: str
    provider_kind: str
    provider_endpoint_url: str | None
    caller_id: str
    owner_id: str
    key_id: str
    model: str
    request_nonce: str | None
    request_expires_at: str | None
    request_hash: str
    response_hash: str
    usage: Usage
    input_token_price_per_1k: str
    output_token_price_per_1k: str
    computed_cost_usd: str
    issued_at: str
    attestation: Mapping[str, Any] = field(default_factory=dict)
    signature: str | None = None

    def payload_dict(self) -> dict[str, Any]:
        payload = {
            "receipt_id": self.receipt_id,
            "service_id": self.service_id,
            "service_public_key_fingerprint": self.service_public_key_fingerprint,
            "tee_mode": self.tee_mode,
            "pricing_table_id": self.pricing_table_id,
            "request_id": self.request_id,
            "provider_id": self.provider_id,
            "provider_kind": self.provider_kind,
            "provider_endpoint_url": self.provider_endpoint_url,
            "caller_id": self.caller_id,
            "owner_id": self.owner_id,
            "key_id": self.key_id,
            "model": self.model,
            "request_nonce": self.request_nonce,
            "request_expires_at": self.request_expires_at,
            "request_hash": self.request_hash,
            "response_hash": self.response_hash,
            "usage": self.usage.to_dict(),
            "input_token_price_per_1k": self.input_token_price_per_1k,
            "output_token_price_per_1k": self.output_token_price_per_1k,
            "computed_cost_usd": self.computed_cost_usd,
            "issued_at": self.issued_at,
            "attestation": dict(self.attestation),
        }
        return payload

    def signable_dict(self) -> dict[str, Any]:
        return self.payload_dict()

    def to_dict(self) -> dict[str, Any]:
        payload = self.payload_dict()
        payload["signature"] = self.signature
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Receipt":
        return cls(
            receipt_id=str(payload["receipt_id"]),
            service_id=str(payload["service_id"]),
            service_public_key_fingerprint=str(payload["service_public_key_fingerprint"]),
            tee_mode=str(payload["tee_mode"]),
            pricing_table_id=str(payload["pricing_table_id"]),
            request_id=str(payload["request_id"]),
            provider_id=str(payload.get("provider_id") or "mock"),
            provider_kind=str(payload.get("provider_kind") or "unknown"),
            provider_endpoint_url=payload.get("provider_endpoint_url"),
            caller_id=str(payload["caller_id"]),
            owner_id=str(payload["owner_id"]),
            key_id=str(payload["key_id"]),
            model=str(payload["model"]),
            request_nonce=payload.get("request_nonce"),
            request_expires_at=payload.get("request_expires_at"),
            request_hash=str(payload["request_hash"]),
            response_hash=str(payload["response_hash"]),
            usage=Usage.from_dict(payload["usage"]),
            input_token_price_per_1k=str(payload["input_token_price_per_1k"]),
            output_token_price_per_1k=str(payload["output_token_price_per_1k"]),
            computed_cost_usd=str(payload["computed_cost_usd"]),
            issued_at=str(payload["issued_at"]),
            attestation=dict(payload.get("attestation") or {}),
            signature=payload.get("signature"),
        )


@dataclass(frozen=True)
class Proof:
    proof_id: str
    proof_version: str
    service_id: str
    request: LLMRequest
    response: LLMResponse
    receipt: Receipt
    created_at: str

    def payload_dict(self) -> dict[str, Any]:
        return {
            "proof_id": self.proof_id,
            "proof_version": self.proof_version,
            "service_id": self.service_id,
            "request": self.request.to_dict(),
            "response": self.response.to_dict(),
            "receipt": self.receipt.to_dict(),
            "created_at": self.created_at,
        }

    def summary_dict(self) -> dict[str, Any]:
        return {
            "proof_id": self.proof_id,
            "proof_version": self.proof_version,
            "service_id": self.service_id,
            "request_id": self.request.request_id,
            "provider_id": self.request.provider_id,
            "model": self.request.model,
            "receipt_id": self.receipt.receipt_id,
            "created_at": self.created_at,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.payload_dict()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Proof":
        request_payload = payload.get("request")
        response_payload = payload.get("response")
        receipt_payload = payload.get("receipt")
        if not isinstance(request_payload, Mapping) or not isinstance(response_payload, Mapping) or not isinstance(
            receipt_payload, Mapping
        ):
            raise ValueError("proof payload must include request, response, and receipt objects")
        return cls(
            proof_id=str(payload["proof_id"]),
            proof_version=str(payload.get("proof_version") or "minibridge-proof-1"),
            service_id=str(payload["service_id"]),
            request=LLMRequest.from_dict(request_payload),
            response=LLMResponse.from_dict(response_payload),
            receipt=Receipt.from_dict(receipt_payload),
            created_at=str(payload["created_at"]),
        )
