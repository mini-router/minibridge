from __future__ import annotations

from argparse import Namespace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen

from llm_api_proof import (
    HostControlPlane,
    KeyPolicy,
    LLMProofService,
    LLMRequest,
    MockAttestationProvider,
    MockProvider,
    ModelPrice,
    PricingTable,
    ReceiptSigner,
    ProviderRegistry,
)
from llm_api_proof.cli import _bootstrap_host
from llm_api_proof.host_http_server import run_host_server
from llm_api_proof.http_server import run_server


class HostControlTests(unittest.TestCase):
    @staticmethod
    def _get_json(url: str) -> dict:
        request = Request(url)
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _post_json(url: str, payload: dict) -> dict:
        request = Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_host_registers_runner_and_submits_job(self) -> None:
        signer = ReceiptSigner.generate()
        pricing = PricingTable(
            pricing_table_id="host-plan",
            models={
                "gpt-demo": ModelPrice(
                    model="gpt-demo",
                    input_per_1k=Decimal("0.0100"),
                    output_per_1k=Decimal("0.0300"),
                )
            },
        )
        runner_service = LLMProofService(
            service_id="runner-1",
            signer=signer,
            pricing_table=pricing,
            tee_mode="cpu-tee",
            attestation_provider=MockAttestationProvider(service_instance_id="runner-1"),
        )
        runner_service.register_key(
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
        runner_registry = ProviderRegistry()
        runner_registry.register(MockProvider())
        runner_server = run_server("127.0.0.1", 0, runner_service, runner_registry)

        control = HostControlPlane(host_id="host-1")
        host_server = run_host_server("127.0.0.1", 0, control)
        try:
            runner_thread = threading.Thread(target=runner_server.serve_forever, daemon=True)
            runner_thread.start()
            runner_host, runner_port = runner_server.server_address

            host_thread = threading.Thread(target=host_server.serve_forever, daemon=True)
            host_thread.start()
            host_host, host_port = host_server.server_address

            register = self._post_json(
                f"http://{host_host}:{host_port}/register-runner",
                {
                    "runner_id": "runner-1",
                    "endpoint_url": f"http://{runner_host}:{runner_port}",
                },
            )
            self.assertTrue(register["ok"])
            self.assertEqual(register["runner"]["runner_id"], "runner-1")

            job = self._post_json(
                f"http://{host_host}:{host_port}/jobs",
                {
                    "runner_id": "runner-1",
                    "job_type": "prove",
                    "request": {
                        "request_id": "job-001",
                        "provider_id": "mock",
                        "caller_id": "bob",
                        "owner_id": "alice",
                        "key_id": "k1",
                        "model": "gpt-demo",
                        "messages": [{"role": "user", "content": "hello host"}],
                        "nonce": "nonce-job-001",
                        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                    },
                },
            )
            self.assertTrue(job["ok"])
            self.assertEqual(job["job"]["status"], "completed")
            self.assertIsNotNone(job["job"]["bundle"])

            manifest = self._get_json(f"http://{host_host}:{host_port}/jobs/{job['job']['job_id']}/manifest")
            self.assertTrue(manifest["ok"])
            self.assertEqual(manifest["counts"]["verified_proofs"], 1)

            verify = self._get_json(f"http://{host_host}:{host_port}/jobs/{job['job']['job_id']}/verify")
            self.assertTrue(verify["ok"])
            self.assertTrue(verify["result"]["verified"])
        finally:
            host_server.shutdown()
            host_server.server_close()
            runner_server.shutdown()
            runner_server.server_close()

    def test_host_auto_probes_runner_from_config(self) -> None:
        signer = ReceiptSigner.generate()
        pricing = PricingTable(
            pricing_table_id="host-plan",
            models={
                "gpt-demo": ModelPrice(
                    model="gpt-demo",
                    input_per_1k=Decimal("0.0100"),
                    output_per_1k=Decimal("0.0300"),
                )
            },
        )
        runner_service = LLMProofService(
            service_id="runner-1",
            signer=signer,
            pricing_table=pricing,
            tee_mode="cpu-tee",
            attestation_provider=MockAttestationProvider(service_instance_id="runner-1"),
        )
        runner_service.register_key(
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
        runner_registry = ProviderRegistry()
        runner_registry.register(MockProvider())
        runner_server = run_server("127.0.0.1", 0, runner_service, runner_registry)

        try:
            runner_thread = threading.Thread(target=runner_server.serve_forever, daemon=True)
            runner_thread.start()
            runner_host, runner_port = runner_server.server_address

            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                host_config_path = tmp_path / "host.json"
                host_config_path.write_text(
                    json.dumps(
                        {
                            "host_id": "host-config",
                            "runners": [
                                {
                                    "runner_id": "runner-1",
                                    "endpoint_url": f"http://{runner_host}:{runner_port}",
                                    "auto_probe": True,
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                args = Namespace(config=str(host_config_path), state_file="", host="127.0.0.1", port=0)
                control, _state_file = _bootstrap_host(args)
                runner = control.get_runner("runner-1")
                self.assertTrue(runner.attestation)
                self.assertEqual(runner.tee_mode, "mock-tee")

                host_server = run_host_server("127.0.0.1", 0, control)
                host_thread = threading.Thread(target=host_server.serve_forever, daemon=True)
                host_thread.start()
                host_host, host_port = host_server.server_address
                try:
                    job = self._post_json(
                        f"http://{host_host}:{host_port}/jobs",
                        {
                            "runner_id": "runner-1",
                            "job_type": "prove",
                            "request": {
                                "request_id": "job-auto-001",
                                "provider_id": "mock",
                                "caller_id": "bob",
                                "owner_id": "alice",
                                "key_id": "k1",
                                "model": "gpt-demo",
                                "messages": [{"role": "user", "content": "hello host"}],
                                "nonce": "nonce-job-auto-001",
                                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                            },
                        },
                    )
                    self.assertTrue(job["ok"])
                    self.assertEqual(job["job"]["status"], "completed")
                finally:
                    host_server.shutdown()
                    host_server.server_close()
        finally:
            runner_server.shutdown()
            runner_server.server_close()


if __name__ == "__main__":
    unittest.main()
