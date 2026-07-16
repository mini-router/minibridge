from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import unittest
from urllib.request import Request, urlopen

from llm_api_proof import (
    AttestationPolicy,
    KeyPolicy,
    MockAttestationProvider,
    LLMProofService,
    LLMRequest,
    ModelPrice,
    MockProvider,
    ProviderRegistry,
    PricingTable,
    ReceiptSigner,
    StaticAttestationProvider,
    verify_receipt,
)
from llm_api_proof.http_server import run_server
from llm_api_proof.models import canonical_json


class RoundTripTests(unittest.TestCase):
    @staticmethod
    def _post_json(url: str, payload: dict) -> dict:
        request = Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _get_json(url: str) -> dict:
        request = Request(url)
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_receipt_round_trip_verifies(self) -> None:
        signer = ReceiptSigner.generate()
        pricing = PricingTable(
            pricing_table_id="test-plan",
            models={
                "gpt-demo": ModelPrice(
                    model="gpt-demo",
                    input_per_1k=Decimal("0.0100"),
                    output_per_1k=Decimal("0.0300"),
                )
            },
        )
        service = LLMProofService(
            service_id="svc",
            signer=signer,
            pricing_table=pricing,
            attestation_provider=MockAttestationProvider(service_instance_id="svc-test"),
        )
        service.register_key(
            owner_id="alice",
            key_id="k1",
            api_key="secret",
            policy=KeyPolicy(
                allowed_callers={"bob"},
                allowed_models={"gpt-demo"},
                require_nonce=True,
                require_expiry=True,
            ),
        )

        request = LLMRequest(
            request_id="req",
            provider_id="mock",
            caller_id="bob",
            owner_id="alice",
            key_id="k1",
            model="gpt-demo",
            messages=[{"role": "user", "content": "hello world"}],
            parameters={"temperature": 0},
            nonce="nonce-1",
            expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        )

        provider = MockProvider()
        response, receipt = service.call(provider, request)
        result = verify_receipt(receipt, signer.public_key, pricing, request=request, response=response)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(receipt.attestation["attestation_mode"], "mock-tee")
        self.assertEqual(receipt.attestation["service_instance_id"], "svc-test")

    def test_wrong_model_is_rejected(self) -> None:
        signer = ReceiptSigner.generate()
        pricing = PricingTable(
            pricing_table_id="test-plan",
            models={
                "gpt-demo": ModelPrice(
                    model="gpt-demo",
                    input_per_1k=Decimal("0.0100"),
                    output_per_1k=Decimal("0.0300"),
                )
            },
        )
        service = LLMProofService(
            service_id="svc",
            signer=signer,
            pricing_table=pricing,
            attestation_provider=MockAttestationProvider(service_instance_id="svc-test"),
        )
        service.register_key(
            owner_id="alice",
            key_id="k1",
            api_key="secret",
            policy=KeyPolicy(
                allowed_callers={"bob"},
                allowed_models={"gpt-demo"},
                require_nonce=True,
                require_expiry=True,
            ),
        )
        request = LLMRequest(
            request_id="req",
            provider_id="mock",
            caller_id="bob",
            owner_id="alice",
            key_id="k1",
            model="other-model",
            messages=[{"role": "user", "content": "hello world"}],
        )
        with self.assertRaises(Exception):
            service.call(MockProvider(), request)

    def test_duplicate_request_id_is_rejected(self) -> None:
        signer = ReceiptSigner.generate()
        pricing = PricingTable(
            pricing_table_id="test-plan",
            models={
                "gpt-demo": ModelPrice(
                    model="gpt-demo",
                    input_per_1k=Decimal("0.0100"),
                    output_per_1k=Decimal("0.0300"),
                )
            },
        )
        service = LLMProofService(
            service_id="svc",
            signer=signer,
            pricing_table=pricing,
            attestation_provider=MockAttestationProvider(service_instance_id="svc-test"),
        )
        service.register_key(
            owner_id="alice",
            key_id="k1",
            api_key="secret",
            policy=KeyPolicy(
                allowed_callers={"bob"},
                allowed_models={"gpt-demo"},
                require_nonce=True,
                require_expiry=True,
            ),
        )
        request = LLMRequest(
            request_id="dup",
            provider_id="mock",
            caller_id="bob",
            owner_id="alice",
            key_id="k1",
            model="gpt-demo",
            messages=[{"role": "user", "content": "hello world"}],
            nonce="nonce-dup",
            expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        )

        service.call(MockProvider(), request)
        with self.assertRaises(Exception):
            service.call(MockProvider(), request)

    def test_expired_request_is_rejected(self) -> None:
        signer = ReceiptSigner.generate()
        pricing = PricingTable(
            pricing_table_id="test-plan",
            models={
                "gpt-demo": ModelPrice(
                    model="gpt-demo",
                    input_per_1k=Decimal("0.0100"),
                    output_per_1k=Decimal("0.0300"),
                )
            },
        )
        service = LLMProofService(
            service_id="svc",
            signer=signer,
            pricing_table=pricing,
            attestation_provider=MockAttestationProvider(service_instance_id="svc-test"),
        )
        service.register_key(
            owner_id="alice",
            key_id="k1",
            api_key="secret",
            policy=KeyPolicy(
                allowed_callers={"bob"},
                allowed_models={"gpt-demo"},
                require_nonce=True,
                require_expiry=True,
            ),
        )
        request = LLMRequest(
            request_id="expired",
            provider_id="mock",
            caller_id="bob",
            owner_id="alice",
            key_id="k1",
            model="gpt-demo",
            messages=[{"role": "user", "content": "hello world"}],
            nonce="nonce-expired",
            expires_at=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        )
        with self.assertRaises(Exception):
            service.call(MockProvider(), request)

    def test_unauthorized_caller_is_rejected(self) -> None:
        signer = ReceiptSigner.generate()
        pricing = PricingTable(
            pricing_table_id="test-plan",
            models={
                "gpt-demo": ModelPrice(
                    model="gpt-demo",
                    input_per_1k=Decimal("0.0100"),
                    output_per_1k=Decimal("0.0300"),
                )
            },
        )
        service = LLMProofService(
            service_id="svc",
            signer=signer,
            pricing_table=pricing,
            attestation_provider=MockAttestationProvider(service_instance_id="svc-test"),
        )
        service.register_key(
            owner_id="alice",
            key_id="k1",
            api_key="secret",
            policy=KeyPolicy(
                allowed_callers={"bob"},
                allowed_models={"gpt-demo"},
                require_nonce=True,
                require_expiry=True,
            ),
        )
        request = LLMRequest(
            request_id="caller-reject",
            provider_id="mock",
            caller_id="mallory",
            owner_id="alice",
            key_id="k1",
            model="gpt-demo",
            messages=[{"role": "user", "content": "hello world"}],
            nonce="nonce-caller",
            expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        )
        with self.assertRaises(Exception):
            service.call(MockProvider(), request)

    def test_provider_specific_http_route(self) -> None:
        signer = ReceiptSigner.generate()
        pricing = PricingTable(
            pricing_table_id="test-plan",
            models={
                "gpt-demo": ModelPrice(
                    model="gpt-demo",
                    input_per_1k=Decimal("0.0100"),
                    output_per_1k=Decimal("0.0300"),
                )
            },
        )
        service = LLMProofService(
            service_id="svc",
            signer=signer,
            pricing_table=pricing,
            attestation_provider=MockAttestationProvider(service_instance_id="svc-test"),
        )
        registry = ProviderRegistry()
        registry.register(MockProvider())
        service.register_key(
            owner_id="alice",
            key_id="k1",
            api_key="secret",
            policy=KeyPolicy(
                allowed_callers={"bob"},
                allowed_models={"gpt-demo"},
                require_nonce=True,
                require_expiry=True,
            ),
        )

        server = run_server("127.0.0.1", 0, service, registry)
        try:
            import threading

            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            result = self._post_json(
                f"http://{host}:{port}/providers/mock/call",
                {
                    "request_id": "http-provider",
                    "provider_id": "mock",
                    "caller_id": "bob",
                    "owner_id": "alice",
                    "key_id": "k1",
                    "model": "gpt-demo",
                    "messages": [{"role": "user", "content": "hello world"}],
                    "nonce": "nonce-http-provider",
                    "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                },
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["provider"]["provider_id"], "mock")
            self.assertEqual(result["receipt"]["provider_id"], "mock")
            self.assertEqual(result["receipt"]["provider_kind"], "mock")
        finally:
            server.shutdown()
            server.server_close()

    def test_prove_http_route_and_proof_lookup(self) -> None:
        signer = ReceiptSigner.generate()
        pricing = PricingTable(
            pricing_table_id="test-plan",
            models={
                "gpt-demo": ModelPrice(
                    model="gpt-demo",
                    input_per_1k=Decimal("0.0100"),
                    output_per_1k=Decimal("0.0300"),
                )
            },
        )
        service = LLMProofService(
            service_id="svc",
            signer=signer,
            pricing_table=pricing,
            attestation_provider=MockAttestationProvider(service_instance_id="svc-test"),
        )
        registry = ProviderRegistry()
        registry.register(MockProvider())
        service.register_key(
            owner_id="alice",
            key_id="k1",
            api_key="secret",
            policy=KeyPolicy(
                allowed_callers={"bob"},
                allowed_models={"gpt-demo"},
                require_nonce=True,
                require_expiry=True,
            ),
        )

        server = run_server("127.0.0.1", 0, service, registry)
        try:
            import threading

            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            result = self._post_json(
                f"http://{host}:{port}/prove",
                {
                    "request_id": "proof-route",
                    "provider_id": "mock",
                    "caller_id": "bob",
                    "owner_id": "alice",
                    "key_id": "k1",
                    "model": "gpt-demo",
                    "messages": [{"role": "user", "content": "hello proof"}],
                    "nonce": "nonce-proof-route",
                    "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                },
            )
            self.assertTrue(result["ok"])
            self.assertIn("proof", result)
            proof_id = result["proof"]["proof_id"]
            self.assertEqual(proof_id, result["receipt"]["receipt_id"])
            proof = self._get_json(f"http://{host}:{port}/proofs/{proof_id}")
            self.assertTrue(proof["ok"])
            self.assertEqual(proof["proof"]["proof_id"], proof_id)
            proofs = self._get_json(f"http://{host}:{port}/proofs")
            self.assertEqual(proofs["proofs"][0]["proof_id"], proof_id)
            verify = self._post_json(
                f"http://{host}:{port}/verify",
                {"proof": result["proof"]},
            )
            self.assertTrue(verify["result"]["valid_signature"])
            self.assertTrue(verify["result"]["valid_request_hash"])
            self.assertTrue(verify["result"]["valid_response_hash"])
            self.assertTrue(verify["result"]["valid_cost"])
        finally:
            server.shutdown()
            server.server_close()

    def test_attestation_seal_and_http_status(self) -> None:
        signer = ReceiptSigner.generate()
        pricing = PricingTable(
            pricing_table_id="test-plan",
            models={
                "gpt-demo": ModelPrice(
                    model="gpt-demo",
                    input_per_1k=Decimal("0.0100"),
                    output_per_1k=Decimal("0.0300"),
                )
            },
        )
        context = {
            "service_id": "svc",
            "tee_mode": "cpu-tee",
            "pricing_table_id": "test-plan",
            "service_public_key_fingerprint": signer.public_key_fingerprint,
            "providers": ["mock"],
            "key_ids": ["alice/k1"],
        }
        evidence = {
            "attestation_mode": "cpu-tee",
            "attestation_backend": "file-attestation",
            "service_instance_id": "tee-node-1",
            "context_hash": sha256(canonical_json(context).encode("utf-8")).hexdigest(),
            "measurement": "mrenclave-demo",
            "report_data": "bind:" + sha256(canonical_json(context).encode("utf-8")).hexdigest(),
            "claims": {
                "service_id": "svc",
                "service_public_key_fingerprint": signer.public_key_fingerprint,
            },
        }
        service = LLMProofService(
            service_id="svc",
            signer=signer,
            pricing_table=pricing,
            tee_mode="cpu-tee",
            attestation_provider=StaticAttestationProvider(mode="cpu-tee", evidence=evidence),
            attestation_policy=AttestationPolicy(
                expected_mode="cpu-tee",
                expected_backend="file-attestation",
                expected_service_id="svc",
                expected_service_public_key_fingerprint=signer.public_key_fingerprint,
                expected_measurement="mrenclave-demo",
                expected_report_data_prefix="bind:",
                required_claims={"service_id", "service_public_key_fingerprint"},
            ),
        )
        registry = ProviderRegistry()
        registry.register(MockProvider())
        service.register_key(
            owner_id="alice",
            key_id="k1",
            api_key="secret",
            policy=KeyPolicy(
                allowed_callers={"bob"},
                allowed_models={"gpt-demo"},
                require_nonce=True,
                require_expiry=True,
            ),
        )
        service.seal_attestation(context=context)

        server = run_server("127.0.0.1", 0, service, registry)
        try:
            import threading

            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            attestation = self._get_json(f"http://{host}:{port}/attestation")
            self.assertTrue(attestation["ok"])
            self.assertTrue(attestation["attestation"]["verified"])
            self.assertEqual(attestation["attestation"]["evidence"]["attestation_mode"], "cpu-tee")
        finally:
            server.shutdown()
            server.server_close()

    def test_provider_registration_http_route(self) -> None:
        signer = ReceiptSigner.generate()
        pricing = PricingTable(
            pricing_table_id="test-plan",
            models={
                "gpt-demo": ModelPrice(
                    model="gpt-demo",
                    input_per_1k=Decimal("0.0100"),
                    output_per_1k=Decimal("0.0300"),
                )
            },
        )
        service = LLMProofService(
            service_id="svc",
            signer=signer,
            pricing_table=pricing,
            attestation_provider=MockAttestationProvider(service_instance_id="svc-test"),
        )
        registry = ProviderRegistry()
        server = run_server("127.0.0.1", 0, service, registry)
        try:
            import threading

            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            register_result = self._post_json(
                f"http://{host}:{port}/register-provider",
                {
                    "provider_id": "openrouter-prod",
                    "provider_kind": "openrouter",
                    "endpoint_url": "https://openrouter.ai/api/v1/chat/completions",
                    "payload_style": "chat-completions",
                    "auth_header": "Authorization",
                    "auth_scheme": "Bearer",
                    "extra_headers": {
                        "HTTP-Referer": "https://example.com",
                        "X-OpenRouter-Title": "Minibridge",
                    },
                    "timeout_seconds": 30,
                },
            )
            self.assertTrue(register_result["ok"])
            self.assertEqual(register_result["provider"]["provider_id"], "openrouter-prod")
            self.assertEqual(register_result["provider"]["provider_kind"], "openrouter")

            providers = self._get_json(f"http://{host}:{port}/providers")
            self.assertEqual(len(providers["providers"]), 1)
            self.assertEqual(providers["providers"][0]["provider_id"], "openrouter-prod")

            provider = self._get_json(f"http://{host}:{port}/providers/openrouter-prod")
            self.assertTrue(provider["ok"])
            self.assertEqual(provider["provider"]["provider_kind"], "openrouter")
        finally:
            server.shutdown()
            server.server_close()

    def test_missing_nonce_is_rejected_when_required(self) -> None:
        signer = ReceiptSigner.generate()
        pricing = PricingTable(
            pricing_table_id="test-plan",
            models={
                "gpt-demo": ModelPrice(
                    model="gpt-demo",
                    input_per_1k=Decimal("0.0100"),
                    output_per_1k=Decimal("0.0300"),
                )
            },
        )
        service = LLMProofService(
            service_id="svc",
            signer=signer,
            pricing_table=pricing,
            attestation_provider=MockAttestationProvider(service_instance_id="svc-test"),
        )
        service.register_key(
            owner_id="alice",
            key_id="k1",
            api_key="secret",
            policy=KeyPolicy(
                allowed_callers={"bob"},
                allowed_models={"gpt-demo"},
                require_nonce=True,
                require_expiry=True,
            ),
        )
        request = LLMRequest(
            request_id="missing-nonce",
            provider_id="mock",
            caller_id="bob",
            owner_id="alice",
            key_id="k1",
            model="gpt-demo",
            messages=[{"role": "user", "content": "hello world"}],
            expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        )
        with self.assertRaises(Exception):
            service.call(MockProvider(), request)


if __name__ == "__main__":
    unittest.main()
