from __future__ import annotations

from decimal import Decimal
import json
from datetime import datetime, timedelta, timezone

from llm_api_proof import (
    MockAttestationProvider,
    KeyPolicy,
    LLMProofService,
    LLMRequest,
    ModelPrice,
    MockProvider,
    PricingTable,
    ReceiptSigner,
    ProviderRegistry,
    verify_receipt,
)


def main() -> None:
    signer = ReceiptSigner.generate()
    pricing = PricingTable(
        pricing_table_id="demo-2026-07-14",
        models={
            "gpt-demo": ModelPrice(
                model="gpt-demo",
                input_per_1k=Decimal("0.0100"),
                output_per_1k=Decimal("0.0300"),
            )
        },
    )
    service = LLMProofService(
        service_id="service-demo",
        signer=signer,
        pricing_table=pricing,
        attestation={"note": "demo only"},
        attestation_provider=MockAttestationProvider(),
    )
    registry = ProviderRegistry()
    registry.register(MockProvider())
    service.register_key(
        owner_id="alice",
        key_id="alice-openai",
        api_key="sk-demo-secret",
        policy=KeyPolicy(
            allowed_callers={"bob-agent"},
            allowed_models={"gpt-demo"},
            spend_limit_usd="1.00",
            require_nonce=True,
            require_expiry=True,
        ),
    )

    request = LLMRequest(
        request_id="req_001",
        provider_id="mock",
        caller_id="bob-agent",
        owner_id="alice",
        key_id="alice-openai",
        model="gpt-demo",
        messages=[
            {"role": "system", "content": "You are a proof engine."},
            {"role": "user", "content": "Summarize the billing policy."},
        ],
        parameters={"temperature": 0.0, "max_output_tokens": 64},
        metadata={"workflow": "validator-run"},
        nonce="nonce-001",
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    )

    provider = registry.get("mock")
    response, receipt = service.call(provider, request)
    verification = verify_receipt(receipt, signer.public_key, pricing, request=request, response=response)

    print(json.dumps({"response": response.to_dict(), "receipt": receipt.to_dict()}, indent=2))
    print("verification:", verification)
    if not verification.ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
