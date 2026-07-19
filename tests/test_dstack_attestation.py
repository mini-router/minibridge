from __future__ import annotations

import json
import socketserver
import tempfile
import threading
import unittest
from pathlib import Path

from llm_api_proof.attestation import DstackSocketAttestationProvider


class _UnixQuoteHandler(socketserver.StreamRequestHandler):
    response_payload: dict[str, object] = {}

    def handle(self) -> None:  # noqa: D401
        # Consume the request so the client sees a normal HTTP lifecycle.
        while self.rfile.readline().strip():
            pass
        body = json.dumps(self.response_payload).encode("utf-8")
        raw = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode("utf-8")
        raw += b"\r\nConnection: close\r\n\r\n" + body
        self.wfile.write(raw)


class DstackAttestationTests(unittest.TestCase):
    def test_socket_provider_collects_quote_and_context_hash(self) -> None:
        with tempfile.TemporaryDirectory(prefix="minibridge-dstack-") as tmp:
            socket_path = Path(tmp) / "dstack.sock"
            _UnixQuoteHandler.response_payload = {
                "quote": "0xdeadbeef",
                "event_log": "0xfeedface",
                "vm_config": {"app_id": "app-123", "instance_id": "inst-456"},
            }
            server = socketserver.UnixStreamServer(str(socket_path), _UnixQuoteHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                provider = DstackSocketAttestationProvider(socket_path=str(socket_path), mode="cpu-tee")
                evidence = provider.collect({"service_id": "runner-1", "feature": "proof"})
                self.assertEqual(evidence.mode, "cpu-tee")
                self.assertEqual(evidence.backend, "dstack-socket")
                self.assertEqual(evidence.quote, "0xdeadbeef")
                self.assertEqual(evidence.claims["service_id"], "runner-1")
                self.assertEqual(evidence.claims["app_id"], "app-123")
                self.assertEqual(evidence.claims["instance_id"], "inst-456")
                self.assertEqual(evidence.claims["attestation_source"], "dstack-socket")
                self.assertTrue(str(evidence.report_data).startswith("0x"))
                self.assertEqual(len(evidence.context_hash), 64)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
