from __future__ import annotations

from decimal import Decimal
import json
from datetime import datetime, timedelta, timezone
import threading
import time
from urllib.request import Request, urlopen

from llm_api_proof import (
    LLMProofService,
    KeyPolicy,
    ModelPrice,
    MockAttestationProvider,
    MockProvider,
    PricingTable,
    ReceiptSigner,
    ProviderRegistry,
)
from llm_api_proof.http_server import run_server


def post_json(url: str, payload: dict) -> dict:
    req = Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    signer = ReceiptSigner.generate()
    pricing = PricingTable(
        pricing_table_id="demo-http",
        models={
            "gpt-demo": ModelPrice(
                model="gpt-demo",
                input_per_1k=Decimal("0.0100"),
                output_per_1k=Decimal("0.0300"),
            )
        },
    )
    service = LLMProofService(
        service_id="service-http-demo",
        signer=signer,
        pricing_table=pricing,
        attestation_provider=MockAttestationProvider(service_instance_id="http-demo"),
    )
    registry = ProviderRegistry()
    registry.register(MockProvider())
    server = run_server("127.0.0.1", 8089, service, registry)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)

    post_json(
        "http://127.0.0.1:8089/register-key",
        {
            "owner_id": "alice",
            "key_id": "alice-key",
            "api_key": "sk-demo-secret",
            "policy": {
                "allowed_callers": ["bob-agent"],
                "allowed_models": ["gpt-demo"],
                "spend_limit_usd": "1.00",
                "require_nonce": True,
                "require_expiry": True,
            },
        },
    )
    result = post_json(
        "http://127.0.0.1:8089/providers/mock/call",
        {
            "request_id": "req-http-1",
            "provider_id": "mock",
            "caller_id": "bob-agent",
            "owner_id": "alice",
            "key_id": "alice-key",
            "model": "gpt-demo",
            "messages": [{"role": "user", "content": "prove this call"}],
            "parameters": {"temperature": 0.0},
            "nonce": "nonce-http-1",
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        },
    )
    print(json.dumps(result, indent=2))
    server.shutdown()


if __name__ == "__main__":
    main()
