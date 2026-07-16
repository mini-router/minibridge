from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol
from datetime import datetime, timezone
import json
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
class AttestationPolicy:
    expected_mode: str | None = None
    expected_backend: str | None = None
    expected_service_id: str | None = None
    expected_service_instance_id: str | None = None
    expected_service_public_key_fingerprint: str | None = None
    expected_measurement: str | None = None
    expected_report_data_prefix: str | None = None
    expected_context_hash: str | None = None
    required_claims: set[str] = field(default_factory=set)
    max_clock_skew_seconds: int | None = 300

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_mode": self.expected_mode,
            "expected_backend": self.expected_backend,
            "expected_service_id": self.expected_service_id,
            "expected_service_instance_id": self.expected_service_instance_id,
            "expected_service_public_key_fingerprint": self.expected_service_public_key_fingerprint,
            "expected_measurement": self.expected_measurement,
            "expected_report_data_prefix": self.expected_report_data_prefix,
            "expected_context_hash": self.expected_context_hash,
            "required_claims": sorted(self.required_claims),
            "max_clock_skew_seconds": self.max_clock_skew_seconds,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AttestationPolicy":
        return cls(
            expected_mode=payload.get("expected_mode"),
            expected_backend=payload.get("expected_backend"),
            expected_service_id=payload.get("expected_service_id"),
            expected_service_instance_id=payload.get("expected_service_instance_id"),
            expected_service_public_key_fingerprint=payload.get("expected_service_public_key_fingerprint"),
            expected_measurement=payload.get("expected_measurement"),
            expected_report_data_prefix=payload.get("expected_report_data_prefix"),
            expected_context_hash=payload.get("expected_context_hash"),
            required_claims=set(payload.get("required_claims") or []),
            max_clock_skew_seconds=(
                int(payload["max_clock_skew_seconds"]) if payload.get("max_clock_skew_seconds") is not None else None
            ),
        )


@dataclass(frozen=True)
class AttestationVerificationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    expected_context_hash: str | None = None


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _claim_value(evidence: AttestationEvidence, key: str) -> Any:
    if key in evidence.claims:
        return evidence.claims[key]
    return None


def verify_attestation_evidence(
    evidence: AttestationEvidence,
    *,
    policy: AttestationPolicy | None = None,
    context: dict[str, Any] | None = None,
) -> AttestationVerificationResult:
    policy = policy or AttestationPolicy()
    context = context or {}
    expected_context_hash = sha256(canonical_json(context).encode("utf-8")).hexdigest()
    errors: list[str] = []

    if policy.expected_mode is not None and evidence.mode != policy.expected_mode:
        errors.append(f"attestation mode mismatch: expected {policy.expected_mode!r}, got {evidence.mode!r}")
    if policy.expected_backend is not None and evidence.backend != policy.expected_backend:
        errors.append(f"attestation backend mismatch: expected {policy.expected_backend!r}, got {evidence.backend!r}")
    if policy.expected_service_instance_id is not None and evidence.service_instance_id != policy.expected_service_instance_id:
        errors.append(
            "service instance mismatch: "
            f"expected {policy.expected_service_instance_id!r}, got {evidence.service_instance_id!r}"
        )

    if policy.expected_context_hash is not None and evidence.context_hash != policy.expected_context_hash:
        errors.append(
            "context hash mismatch against policy: "
            f"expected {policy.expected_context_hash!r}, got {evidence.context_hash!r}"
        )
    elif evidence.backend != "static-evidence" and evidence.context_hash != expected_context_hash:
        errors.append(
            "context hash mismatch against runtime context: "
            f"expected {expected_context_hash!r}, got {evidence.context_hash!r}"
        )

    if policy.expected_service_id is not None:
        claim_service_id = _claim_value(evidence, "service_id")
        if claim_service_id != policy.expected_service_id:
            errors.append(f"service id claim mismatch: expected {policy.expected_service_id!r}, got {claim_service_id!r}")
    if policy.expected_service_public_key_fingerprint is not None:
        claim_fingerprint = _claim_value(evidence, "service_public_key_fingerprint")
        if claim_fingerprint != policy.expected_service_public_key_fingerprint:
            errors.append(
                "service public key fingerprint claim mismatch: "
                f"expected {policy.expected_service_public_key_fingerprint!r}, got {claim_fingerprint!r}"
            )
    if policy.expected_measurement is not None and evidence.measurement != policy.expected_measurement:
        errors.append(f"measurement mismatch: expected {policy.expected_measurement!r}, got {evidence.measurement!r}")
    if policy.expected_report_data_prefix is not None:
        report_data = evidence.report_data or ""
        if not report_data.startswith(policy.expected_report_data_prefix):
            errors.append(
                "report data prefix mismatch: "
                f"expected prefix {policy.expected_report_data_prefix!r}, got {report_data!r}"
            )

    for claim_name in sorted(policy.required_claims):
        if _claim_value(evidence, claim_name) is None:
            errors.append(f"missing attestation claim: {claim_name}")

    if evidence.timestamp is not None and policy.max_clock_skew_seconds is not None:
        timestamp = _parse_timestamp(evidence.timestamp)
        if timestamp is None:
            errors.append(f"invalid attestation timestamp: {evidence.timestamp!r}")
        else:
            now = datetime.now(timezone.utc)
            skew = abs((now - timestamp).total_seconds())
            if skew > policy.max_clock_skew_seconds:
                errors.append(
                    "attestation timestamp outside allowed skew: "
                    f"skew={skew:.0f}s > {policy.max_clock_skew_seconds}s"
                )

    return AttestationVerificationResult(
        ok=not errors,
        errors=errors,
        expected_context_hash=expected_context_hash,
    )


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


@dataclass(frozen=True)
class FileAttestationProvider:
    """
    Read attestation evidence from a JSON file produced by a CPU-TEE runtime.

    This is the integration point for a real TDX/SEV-SNP agent. The provider
    does not invent evidence; it only loads and rehydrates it.
    """

    path: str
    mode: str = "cpu-tee"

    def collect(self, context: dict[str, Any] | None = None) -> AttestationEvidence:
        del context
        evidence_path = Path(self.path)
        raw = evidence_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise TypeError("attestation file must contain a JSON object")
        return AttestationEvidence.from_dict(payload)
