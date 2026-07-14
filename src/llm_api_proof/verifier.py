from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .models import LLMRequest, LLMResponse, Receipt, canonical_json
from .pricing import PricingTable


@dataclass(frozen=True)
class VerificationResult:
    valid_signature: bool
    valid_request_hash: bool
    valid_response_hash: bool
    valid_cost: bool
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.valid_signature and self.valid_request_hash and self.valid_response_hash and self.valid_cost


def _verify_signature(public_key: Ed25519PublicKey, payload: dict[str, Any], signature: str | None) -> bool:
    if signature is None:
        return False
    try:
        from base64 import urlsafe_b64decode

        padding = "=" * (-len(signature) % 4)
        public_key.verify(urlsafe_b64decode(signature + padding), canonical_json(payload).encode("utf-8"))
        return True
    except Exception:
        return False


def verify_receipt(
    receipt: Receipt,
    public_key: Ed25519PublicKey,
    pricing_table: PricingTable,
    *,
    request: LLMRequest | None = None,
    response: LLMResponse | None = None,
) -> VerificationResult:
    errors: list[str] = []

    valid_signature = _verify_signature(public_key, receipt.signable_dict(), receipt.signature)
    if not valid_signature:
        errors.append("invalid signature")

    valid_request_hash = True
    if request is not None:
        valid_request_hash = receipt.request_hash == request.fingerprint()
        if not valid_request_hash:
            errors.append("request hash mismatch")

    valid_response_hash = True
    if response is not None:
        valid_response_hash = receipt.response_hash == response.fingerprint()
        if not valid_response_hash:
            errors.append("response hash mismatch")

    expected_cost = pricing_table.compute_cost(receipt.model, receipt.usage)
    valid_cost = receipt.computed_cost_usd == str(expected_cost)
    if not valid_cost:
        errors.append("cost mismatch")

    return VerificationResult(
        valid_signature=valid_signature,
        valid_request_hash=valid_request_hash,
        valid_response_hash=valid_response_hash,
        valid_cost=valid_cost,
        errors=errors,
    )
