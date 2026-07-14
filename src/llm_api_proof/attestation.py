from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Protocol
from datetime import datetime, timezone
import secrets

from .models import canonical_json


@dataclass(frozen=True)
class AttestationEvidence:
    mode: str
    backend: str
    service_instance_id: str
    context_hash: str
    timestamp: str | None = None
    measurement: str | None = None
    report_data: str | None = None
    quote: str | None = None
    claims: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "attestation_mode": self.mode,
            "attestation_backend": self.backend,
            "service_instance_id": self.service_instance_id,
            "context_hash": self.context_hash,
            "timestamp": self.timestamp,
            "measurement": self.measurement,
            "report_data": self.report_data,
            "quote": self.quote,
            "claims": dict(self.claims),
        }
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AttestationEvidence":
        claims = dict(payload.get("claims") or {})
        for key in (
            "attestation_mode",
            "attestation_backend",
            "service_instance_id",
            "context_hash",
            "timestamp",
            "measurement",
            "report_data",
            "quote",
        ):
            payload.get(key)
        return cls(
            mode=str(payload["attestation_mode"]),
            backend=str(payload["attestation_backend"]),
            service_instance_id=str(payload["service_instance_id"]),
            context_hash=str(payload["context_hash"]),
            timestamp=payload.get("timestamp"),
            measurement=payload.get("measurement"),
            report_data=payload.get("report_data"),
            quote=payload.get("quote"),
            claims=claims,
        )


class AttestationProvider(Protocol):
    @property
    def mode(self) -> str:
        raise NotImplementedError

    def collect(self, context: dict[str, Any] | None = None) -> AttestationEvidence:
        raise NotImplementedError


@dataclass(frozen=True)
class MockAttestationProvider:
    """
    Local-development attestation stub.

    This is intentionally explicit: it proves nothing cryptographic, but it keeps
    the receipt format and service control flow compatible with a future TEE.
    """

    mode: str = "mock-tee"
    service_instance_id: str = field(default_factory=lambda: f"svc_{secrets.token_hex(8)}")

    def collect(self, context: dict[str, Any] | None = None) -> AttestationEvidence:
        context = context or {}
        digest = sha256(canonical_json(context).encode("utf-8")).hexdigest()
        return AttestationEvidence(
            mode=self.mode,
            backend="mock-attestation",
            service_instance_id=self.service_instance_id,
            context_hash=digest,
            timestamp=datetime.now(timezone.utc).isoformat(),
            claims={"source": "local-development"},
        )


@dataclass(frozen=True)
class StaticAttestationProvider:
    """
    A deterministic provider for tests or environments where attestation is external.
    """

    mode: str
    evidence: dict[str, Any]

    def collect(self, context: dict[str, Any] | None = None) -> AttestationEvidence:
        del context
        payload = {
            "attestation_mode": self.mode,
            "attestation_backend": self.evidence.get("attestation_backend", "static-evidence"),
            "service_instance_id": self.evidence.get("service_instance_id", "static-service"),
            "context_hash": self.evidence.get("context_hash", ""),
            "timestamp": self.evidence.get("timestamp"),
            "measurement": self.evidence.get("measurement"),
            "report_data": self.evidence.get("report_data"),
            "quote": self.evidence.get("quote"),
            "claims": dict(self.evidence.get("claims") or {}),
        }
        return AttestationEvidence.from_dict(payload)
