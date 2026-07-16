from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from urllib.request import Request, urlopen

from llm_api_proof import (
    KeyPolicy,
    LLMProofService,
    LLMRequest,
    MockAttestationProvider,
    MockProvider,
    ModelPrice,
    PricingTable,
    ReceiptSigner,
)
from llm_api_proof.bundle import build_bundle, verify_bundle, write_bundle
from llm_api_proof.http_server import run_server


class BundleTests(unittest.TestCase):
    @staticmethod
    def _get_json(url: str) -> dict:
        request = Request(url)
        with urlopen(request) as response:
            import json

            return json.loads(response.read().decode("utf-8"))

    def test_bundle_create_and_verify_round_trip(self) -> None:
        signer = ReceiptSigner.generate()
        pricing = PricingTable(
            pricing_table_id="bundle-plan",
            models={
                "gpt-demo": ModelPrice(
                    model="gpt-demo",
                    input_per_1k=Decimal("0.0100"),
                    output_per_1k=Decimal("0.0300"),
                )
            },
        )
        service = LLMProofService(
            service_id="svc-bundle",
            signer=signer,
            pricing_table=pricing,
            tee_mode="cpu-tee",
            attestation_provider=MockAttestationProvider(service_instance_id="svc-bundle"),
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
            request_id="req-bundle",
            provider_id="mock",
            caller_id="bob",
            owner_id="alice",
            key_id="k1",
            model="gpt-demo",
            messages=[{"role": "user", "content": "hello bundle"}],
            nonce="nonce-bundle",
            expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        )
        service.prove(MockProvider(), request)

        bundle = build_bundle(
            service.proofs,
            service_id=service.service_id,
            tee_mode=service.tee_mode,
            service_public_key=signer.export_public_key(),
            service_public_key_fingerprint=signer.public_key_fingerprint,
            attestation=service.attestation_status(),
        )

        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = write_bundle(Path(tmp) / "bundle", bundle)
            self.assertTrue((bundle_dir / "manifest.json").exists())
            self.assertTrue((bundle_dir / "trajectories.jsonl").exists())
            self.assertTrue((bundle_dir / "validation_report.jsonl").exists())

            result = verify_bundle(bundle_dir)
            self.assertTrue(result["verified"], result["issues"])
            self.assertEqual(result["service_id"], "svc-bundle")
            self.assertEqual(result["proof_count"], 1)

    def test_bundle_http_export_and_manifest(self) -> None:
        signer = ReceiptSigner.generate()
        pricing = PricingTable(
            pricing_table_id="bundle-plan",
            models={
                "gpt-demo": ModelPrice(
                    model="gpt-demo",
                    input_per_1k=Decimal("0.0100"),
                    output_per_1k=Decimal("0.0300"),
                )
            },
        )
        service = LLMProofService(
            service_id="svc-bundle-http",
            signer=signer,
            pricing_table=pricing,
            tee_mode="cpu-tee",
            attestation_provider=MockAttestationProvider(service_instance_id="svc-bundle-http"),
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
            request_id="req-bundle-http",
            provider_id="mock",
            caller_id="bob",
            owner_id="alice",
            key_id="k1",
            model="gpt-demo",
            messages=[{"role": "user", "content": "hello bundle http"}],
            nonce="nonce-bundle-http",
            expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        )
        service.prove(MockProvider(), request)
        registry = None
        server = run_server("127.0.0.1", 0, service, registry)
        try:
            import threading

            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            manifest = self._get_json(f"http://{host}:{port}/bundle/manifest")
            self.assertTrue(manifest["ok"])
            self.assertEqual(manifest["manifest"]["service_id"], "svc-bundle-http")
            self.assertEqual(manifest["counts"]["verified_proofs"], 1)

            bundle = self._get_json(f"http://{host}:{port}/bundle/export")
            self.assertTrue(bundle["ok"])
            self.assertEqual(bundle["bundle"]["manifest"]["proof_count"], 1)
            self.assertEqual(len(bundle["bundle"]["verified_proofs"]), 1)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
