from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any
import json
import os

from .models import Proof, Receipt
from .provider import ProviderRegistry
from .service import LLMProofService, RegisteredKey
from .attestation import FileAttestationProvider, MockAttestationProvider, StaticAttestationProvider


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"cannot serialize {type(value)!r}")


def load_state(path: str | Path) -> dict[str, Any]:
    state_path = Path(path)
    if not state_path.exists():
        return {}
    raw = state_path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise TypeError("state file must contain a JSON object")
    return payload


def save_state(path: str | Path, service: LLMProofService, provider_registry: ProviderRegistry) -> None:
    attestation_provider_payload: dict[str, Any] | None = None
    attestation_provider = service.attestation_provider
    if isinstance(attestation_provider, MockAttestationProvider):
        attestation_provider_payload = {
            "kind": "mock",
            "mode": attestation_provider.mode,
            "service_instance_id": attestation_provider.service_instance_id,
        }
    elif isinstance(attestation_provider, StaticAttestationProvider):
        attestation_provider_payload = {
            "kind": "static",
            "mode": attestation_provider.mode,
            "evidence": dict(attestation_provider.evidence),
        }
    elif isinstance(attestation_provider, FileAttestationProvider):
        attestation_provider_payload = {
            "kind": "file",
            "mode": attestation_provider.mode,
            "path": attestation_provider.path,
        }

    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "service_id": service.service_id,
        "tee_mode": service.tee_mode,
        "attestation": dict(service.attestation),
        "attestation_policy": service.attestation_policy.to_dict() if getattr(service, "attestation_policy", None) is not None else None,
        "attestation_provider": attestation_provider_payload,
        "pricing_table": {
            "pricing_table_id": service.pricing_table.pricing_table_id,
            "models": {
                model: {
                    "model": price.model,
                    "input_per_1k": str(price.input_per_1k),
                    "output_per_1k": str(price.output_per_1k),
                }
                for model, price in service.pricing_table.models.items()
            },
        },
        "providers": [provider.describe().to_dict() for provider in provider_registry.providers.values()],
        "keys": service.export_keys(),
        "receipts": service.export_receipts(),
        "proofs": service.export_proofs(),
    }

    tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")
    raw = json.dumps(payload, default=_json_default, indent=2, sort_keys=True)
    tmp_path.write_text(raw + "\n", encoding="utf-8")
    os.replace(tmp_path, state_path)


def restore_keys(service: LLMProofService, keys_payload: list[dict[str, Any]]) -> None:
    for key_payload in keys_payload:
        service.load_key(RegisteredKey.from_dict(dict(key_payload)))


def restore_receipts(service: LLMProofService, receipts_payload: list[dict[str, Any]]) -> None:
    service.load_receipts([Receipt.from_dict(dict(receipt)) for receipt in receipts_payload])


def restore_proofs(service: LLMProofService, proofs_payload: list[dict[str, Any]]) -> None:
    service.load_proofs([Proof.from_dict(dict(proof)) for proof in proofs_payload])
