from __future__ import annotations

from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import tempfile
import unittest

from llm_api_proof import LLMRequest, MockProvider, verify_receipt
from llm_api_proof.cli import _bootstrap_runtime
from llm_api_proof.state import save_state


class CliTests(unittest.TestCase):
    def test_bootstrap_runtime_loads_config_and_writes_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "minibridge.config.json"
            signing_key_file = tmp_path / "minibridge.signing.key"
            public_key_file = tmp_path / "minibridge.signing.key.pub"

            config = {
                "service_id": "svc-cli",
                "pricing_table": {
                    "pricing_table_id": "plan-cli",
                    "models": {
                        "gpt-demo": {
                            "input_per_1k": "0.0100",
                            "output_per_1k": "0.0300",
                        }
                    },
                },
                "providers": [
                    {
                        "provider_id": "mock",
                        "provider_kind": "mock",
                    }
                ],
                "keys": [
                    {
                        "owner_id": "alice",
                        "key_id": "k1",
                        "api_key": "secret",
                        "policy": {
                            "allowed_callers": ["bob"],
                            "allowed_models": ["gpt-demo"],
                            "require_nonce": True,
                            "require_expiry": True,
                        },
                    }
                ],
            }
            config_path.write_text(json.dumps(config), encoding="utf-8")

            args = Namespace(
                config=str(config_path),
                signing_key_file=str(signing_key_file),
                public_key_file=str(public_key_file),
            )
            service, registry, signer, derived_public_key_file, _state_file = _bootstrap_runtime(args)

            self.assertTrue(signing_key_file.exists())
            self.assertTrue(public_key_file.exists())
            self.assertEqual(derived_public_key_file, public_key_file)
            self.assertEqual(service.service_id, "svc-cli")
            self.assertIsNotNone(registry.get("mock"))

            request = LLMRequest(
                request_id="req-cli",
                provider_id="mock",
                caller_id="bob",
                owner_id="alice",
                key_id="k1",
                model="gpt-demo",
                messages=[{"role": "user", "content": "hello"}],
                nonce="nonce-cli",
                expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            )
            response, receipt = service.call(MockProvider(), request)
            result = verify_receipt(receipt, signer.public_key, service.pricing_table, request=request, response=response)
            self.assertTrue(result.ok, result.errors)

    def test_state_file_persists_keys_providers_and_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "minibridge.config.json"
            state_path = tmp_path / "minibridge.state.json"
            signing_key_file = tmp_path / "minibridge.signing.key"
            public_key_file = tmp_path / "minibridge.signing.key.pub"

            config = {
                "service_id": "svc-state",
                "pricing_table": {
                    "pricing_table_id": "plan-state",
                    "models": {
                        "gpt-demo": {
                            "input_per_1k": "0.0100",
                            "output_per_1k": "0.0300",
                        }
                    },
                },
                "providers": [
                    {
                        "provider_id": "mock",
                        "provider_kind": "mock",
                    }
                ],
                "keys": [
                    {
                        "owner_id": "alice",
                        "key_id": "k1",
                        "api_key": "secret",
                        "policy": {
                            "allowed_callers": ["bob"],
                            "allowed_models": ["gpt-demo"],
                            "require_nonce": True,
                            "require_expiry": True,
                        },
                    }
                ],
            }
            config_path.write_text(json.dumps(config), encoding="utf-8")

            args = Namespace(
                config=str(config_path),
                signing_key_file=str(signing_key_file),
                public_key_file=str(public_key_file),
                state_file=str(state_path),
            )
            service, registry, signer, _public_key_file, loaded_state_file = _bootstrap_runtime(args)
            self.assertEqual(loaded_state_file, state_path)

            request = LLMRequest(
                request_id="req-state",
                provider_id="mock",
                caller_id="bob",
                owner_id="alice",
                key_id="k1",
                model="gpt-demo",
                messages=[{"role": "user", "content": "hello state"}],
                nonce="nonce-state",
                expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            )
            response, receipt, proof = service.prove(MockProvider(), request)
            save_state(state_path, service, registry)
            self.assertTrue(state_path.exists())

            reloaded_args = Namespace(
                config=None,
                signing_key_file=str(signing_key_file),
                public_key_file=str(public_key_file),
                state_file=str(state_path),
            )
            reloaded_service, reloaded_registry, reloaded_signer, _, _ = _bootstrap_runtime(reloaded_args)

            self.assertEqual(reloaded_service.service_id, "svc-state")
            self.assertEqual(reloaded_service.receipts[0].receipt_id, receipt.receipt_id)
            self.assertEqual(reloaded_service.proofs[0].proof_id, proof.proof_id)
            self.assertEqual(reloaded_service.get_key("alice", "k1").spent_usd, service.get_key("alice", "k1").spent_usd)
            self.assertEqual(reloaded_registry.get("mock").describe().provider_id, "mock")
            result = verify_receipt(receipt, reloaded_signer.public_key, reloaded_service.pricing_table, request=request, response=response)
            self.assertTrue(result.ok, result.errors)


if __name__ == "__main__":
    unittest.main()
