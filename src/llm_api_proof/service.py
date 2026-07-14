from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import uuid

from .attestation import AttestationEvidence, AttestationProvider
from .models import KeyPolicy, LLMRequest, LLMResponse, Receipt
from .pricing import PricingTable
from .provider import LLMProvider
from .signing import ReceiptSigner


@dataclass
class RegisteredKey:
    owner_id: str
    key_id: str
    api_key: str
    policy: KeyPolicy = field(default_factory=KeyPolicy)
    spent_usd: Decimal = Decimal("0")
    active: bool = True

    def can_use_caller(self, caller_id: str) -> bool:
        return not self.policy.allowed_callers or caller_id in self.policy.allowed_callers

    def can_use_model(self, model: str) -> bool:
        return not self.policy.allowed_models or model in self.policy.allowed_models

    def spend_limit(self) -> Decimal | None:
        if self.policy.spend_limit_usd is None:
            return None
        return Decimal(self.policy.spend_limit_usd)

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner_id": self.owner_id,
            "key_id": self.key_id,
            "api_key": self.api_key,
            "policy": self.policy.to_dict(),
            "spent_usd": str(self.spent_usd),
            "active": self.active,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RegisteredKey":
        return cls(
            owner_id=str(payload["owner_id"]),
            key_id=str(payload["key_id"]),
            api_key=str(payload["api_key"]),
            policy=KeyPolicy.from_dict(dict(payload.get("policy") or {})),
            spent_usd=Decimal(str(payload.get("spent_usd") or "0")),
            active=bool(payload.get("active", True)),
        )


class ProofServiceError(RuntimeError):
    pass


class UnknownKeyError(ProofServiceError):
    pass


class KeyDisabledError(ProofServiceError):
    pass


class ModelNotAllowedError(ProofServiceError):
    pass


class CallerNotAllowedError(ProofServiceError):
    pass


class ReplayDetectedError(ProofServiceError):
    pass


class ExpiredRequestError(ProofServiceError):
    pass


class ExpiredKeyPolicyError(ProofServiceError):
    pass


class LLMProofService:
    def __init__(
        self,
        *,
        service_id: str,
        signer: ReceiptSigner,
        pricing_table: PricingTable,
        tee_mode: str | None = None,
        attestation: dict[str, Any] | None = None,
        attestation_provider: AttestationProvider | None = None,
    ) -> None:
        self.service_id = service_id
        self.signer = signer
        self.pricing_table = pricing_table
        self.attestation_provider = attestation_provider
        self.tee_mode = tee_mode or (attestation_provider.mode if attestation_provider is not None else "tee-ready-mock")
        self.attestation = attestation or {}
        self._keys: dict[tuple[str, str], RegisteredKey] = {}
        self._receipts: list[Receipt] = []
        self._seen_request_ids: set[tuple[str, str, str]] = set()

    @property
    def receipts(self) -> list[Receipt]:
        return list(self._receipts)

    def load_key(self, record: RegisteredKey) -> RegisteredKey:
        self._keys[(record.owner_id, record.key_id)] = record
        return record

    def load_receipts(self, receipts: list[Receipt]) -> None:
        self._receipts = list(receipts)
        self._seen_request_ids = {
            (receipt.owner_id, receipt.key_id, receipt.request_id) for receipt in self._receipts
        }

    def export_keys(self) -> list[dict[str, Any]]:
        return [record.to_dict() for record in self._keys.values()]

    def export_receipts(self) -> list[dict[str, Any]]:
        return [receipt.to_dict() for receipt in self._receipts]

    def register_key(
        self,
        *,
        owner_id: str,
        key_id: str,
        api_key: str,
        policy: KeyPolicy | dict[str, Any] | None = None,
    ) -> RegisteredKey:
        if policy is None:
            normalized_policy = KeyPolicy()
        elif isinstance(policy, KeyPolicy):
            normalized_policy = policy
        else:
            normalized_policy = KeyPolicy.from_dict(policy)
        record = RegisteredKey(
            owner_id=owner_id,
            key_id=key_id,
            api_key=api_key,
            policy=normalized_policy,
        )
        self._keys[(owner_id, key_id)] = record
        return record

    def get_key(self, owner_id: str, key_id: str) -> RegisteredKey:
        try:
            return self._keys[(owner_id, key_id)]
        except KeyError as exc:
            raise UnknownKeyError(f"unknown key {owner_id}/{key_id}") from exc

    def disable_key(self, owner_id: str, key_id: str) -> None:
        self.get_key(owner_id, key_id).active = False

    def call(self, provider: LLMProvider, request: LLMRequest) -> tuple[LLMResponse, Receipt]:
        key = self.get_key(request.owner_id, request.key_id)
        if not key.active:
            raise KeyDisabledError(f"key {request.owner_id}/{request.key_id} is disabled")
        self._reject_expired_key_policy(key)
        provider_id = getattr(provider, "provider_id", None)
        if provider_id is not None and request.provider_id != provider_id:
            raise ProofServiceError(
                f"request provider {request.provider_id!r} does not match backend {provider_id!r}"
            )
        if not key.can_use_caller(request.caller_id):
            raise CallerNotAllowedError(f"caller {request.caller_id!r} is not allowed for key {request.key_id!r}")
        if not key.can_use_model(request.model):
            raise ModelNotAllowedError(f"model {request.model!r} is not allowed for key {request.key_id!r}")
        self._reject_replay(request)
        self._reject_request_freshness(request, key)

        response = provider.complete(key.api_key, request)
        cost = self.pricing_table.compute_cost(request.model, response.usage)

        spend_limit_usd = key.spend_limit()
        if spend_limit_usd is not None and key.spent_usd + cost > spend_limit_usd:
            raise ProofServiceError(
                f"spend limit exceeded for key {request.owner_id}/{request.key_id}: "
                f"spent={key.spent_usd} + new_cost={cost} > limit={spend_limit_usd}"
            )

        key.spent_usd += cost

        receipt = Receipt(
            receipt_id=f"rcpt_{uuid.uuid4().hex}",
            service_id=self.service_id,
            service_public_key_fingerprint=self.signer.public_key_fingerprint,
            tee_mode=self.tee_mode,
            pricing_table_id=self.pricing_table.pricing_table_id,
            request_id=request.request_id,
            provider_id=request.provider_id,
            provider_kind=getattr(provider, "provider_kind", "unknown"),
            provider_endpoint_url=getattr(provider, "endpoint_url", None),
            caller_id=request.caller_id,
            owner_id=request.owner_id,
            key_id=request.key_id,
            model=request.model,
            request_nonce=request.nonce,
            request_expires_at=request.expires_at,
            request_hash=request.fingerprint(),
            response_hash=response.fingerprint(),
            usage=response.usage,
            input_token_price_per_1k=str(self.pricing_table.get(request.model).input_per_1k),
            output_token_price_per_1k=str(self.pricing_table.get(request.model).output_per_1k),
            computed_cost_usd=str(cost),
            issued_at=datetime.now(timezone.utc).isoformat(),
            attestation=self._collect_attestation(request, response),
        )
        receipt = self._sign_receipt(receipt)
        self._receipts.append(receipt)
        self._seen_request_ids.add((request.owner_id, request.key_id, request.request_id))
        return response, receipt

    def _reject_replay(self, request: LLMRequest) -> None:
        key = (request.owner_id, request.key_id, request.request_id)
        if key in self._seen_request_ids:
            raise ReplayDetectedError(f"request_id {request.request_id!r} already used for {request.owner_id}/{request.key_id}")

    def _reject_request_freshness(self, request: LLMRequest, key: RegisteredKey) -> None:
        if key.policy.require_nonce and not request.nonce:
            raise ProofServiceError(f"request {request.request_id!r} missing nonce")
        if request.expires_at is None:
            if key.policy.require_expiry:
                raise ExpiredRequestError(f"request {request.request_id!r} missing expiry timestamp")
            return
        try:
            expires_at = datetime.fromisoformat(request.expires_at)
        except ValueError as exc:
            raise ProofServiceError(f"invalid expires_at timestamp: {request.expires_at!r}") from exc
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if expires_at <= now:
            raise ExpiredRequestError(f"request {request.request_id!r} is expired at {request.expires_at!r}")

    def _reject_expired_key_policy(self, key: RegisteredKey) -> None:
        if not key.policy.require_expiry or key.policy.expires_at is None:
            return
        try:
            expires_at = datetime.fromisoformat(key.policy.expires_at)
        except ValueError as exc:
            raise ProofServiceError(f"invalid key policy expires_at timestamp: {key.policy.expires_at!r}") from exc
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise ExpiredKeyPolicyError(f"key policy for {key.owner_id}/{key.key_id} has expired at {key.policy.expires_at!r}")

    def _collect_attestation(self, request: LLMRequest, response: LLMResponse) -> dict[str, Any]:
        context = {
            "service_id": self.service_id,
            "tee_mode": self.tee_mode,
            "request_hash": request.fingerprint(),
            "response_hash": response.fingerprint(),
            "model": request.model,
        }
        evidence = dict(self.attestation)
        if self.attestation_provider is not None:
            attestation = self.attestation_provider.collect(context)
            if isinstance(attestation, AttestationEvidence):
                evidence.update(attestation.to_dict())
            else:
                evidence.update(attestation)
        else:
            evidence.update(context)
        return evidence

    def _sign_receipt(self, receipt: Receipt) -> Receipt:
        signature = self.signer.sign(receipt.signable_dict())
        return replace(receipt, signature=signature)
