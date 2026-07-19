from __future__ import annotations

from dataclasses import asdict, is_dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
import json
import re
import sys
from urllib.request import Request, urlopen

from .host_control import HostControlPlane, RunnerRegistration, load_host_state, restore_host_state


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"cannot serialize {type(value)!r}")


def _read_json(handler: BaseHTTPRequestHandler) -> Any:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length) if length else b"{}"
    return json.loads(raw.decode("utf-8"))


def _write_json(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    raw = json.dumps(payload, default=_json_default, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _write_cors_preflight(handler: BaseHTTPRequestHandler) -> None:
    handler.send_response(204)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
    handler.send_header("Access-Control-Max-Age", "600")
    handler.end_headers()


def _parse_job_path(path: str) -> str | None:
    match = re.fullmatch(r"/jobs/([^/]+)", path)
    if match is None:
        return None
    return match.group(1)


def _parse_job_bundle_path(path: str) -> str | None:
    match = re.fullmatch(r"/jobs/([^/]+)/bundle", path)
    if match is None:
        return None
    return match.group(1)


def _parse_job_manifest_path(path: str) -> str | None:
    match = re.fullmatch(r"/jobs/([^/]+)/manifest", path)
    if match is None:
        return None
    return match.group(1)


def _parse_job_verify_path(path: str) -> str | None:
    match = re.fullmatch(r"/jobs/([^/]+)/verify", path)
    if match is None:
        return None
    return match.group(1)


def _maybe_save_state(state_save: Callable[[], None] | None) -> None:
    if state_save is None:
        return
    try:
        state_save()
    except Exception as exc:
        print(f"warning: host state save failed: {exc}", file=sys.stderr)


def _probe_runner(endpoint_url: str) -> dict[str, Any]:
    request = Request(f"{endpoint_url.rstrip('/')}/attestation", method="GET")
    with urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def make_handler(control: HostControlPlane, state_save: Callable[[], None] | None = None) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "llm-api-proof-host/0.2"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            del format, args

        def do_GET(self) -> None:  # noqa: N802
            job_id = _parse_job_path(self.path)
            bundle_job_id = _parse_job_bundle_path(self.path)
            manifest_job_id = _parse_job_manifest_path(self.path)
            verify_job_id = _parse_job_verify_path(self.path)

            if self.path == "/health":
                _write_json(self, 200, {"ok": True, "host_id": control.host_id})
                return
            if self.path == "/runners":
                _write_json(self, 200, {"runners": [runner.to_dict() for runner in control.list_runners()]})
                return
            if self.path == "/jobs":
                _write_json(self, 200, {"jobs": [job.to_dict() for job in control.list_jobs()]})
                return
            if job_id is not None:
                try:
                    job = control.get_job(job_id)
                    _write_json(self, 200, {"ok": True, "job": job.to_dict()})
                except Exception as exc:
                    _write_json(self, 404, {"ok": False, "error": str(exc)})
                return
            if bundle_job_id is not None:
                try:
                    _write_json(self, 200, {"ok": True, "bundle": control.export_job_bundle(bundle_job_id)})
                except Exception as exc:
                    _write_json(self, 404, {"ok": False, "error": str(exc)})
                return
            if manifest_job_id is not None:
                try:
                    bundle = control.export_job_bundle(manifest_job_id)
                    _write_json(
                        self,
                        200,
                        {
                            "ok": True,
                            "manifest": bundle["manifest"],
                            "counts": {
                                "raw_proofs": len(bundle.get("raw_proofs") or []),
                                "verified_proofs": len(bundle.get("verified_proofs") or []),
                                "validation_rows": len(bundle.get("validation_report") or []),
                            },
                            "attestation_verified": bool(bundle.get("attestation", {}).get("verified")),
                        },
                    )
                except Exception as exc:
                    _write_json(self, 404, {"ok": False, "error": str(exc)})
                return
            if verify_job_id is not None:
                try:
                    result = control.verify_job_bundle(verify_job_id)
                    _write_json(self, 200, {"ok": True, "result": result})
                except Exception as exc:
                    _write_json(self, 400, {"ok": False, "error": str(exc)})
                return
            self.send_error(404, "not found")

        def do_OPTIONS(self) -> None:  # noqa: N802
            _write_cors_preflight(self)

        def do_POST(self) -> None:  # noqa: N802
            try:
                payload = _read_json(self)
            except Exception as exc:
                _write_json(self, 400, {"ok": False, "error": f"invalid json: {exc}"})
                return

            if self.path == "/register-runner":
                try:
                    endpoint_url = str(payload["endpoint_url"])
                    runner_id = str(payload.get("runner_id") or endpoint_url)
                    attestation_payload = _probe_runner(endpoint_url)
                    if not attestation_payload.get("ok"):
                        raise RuntimeError(attestation_payload.get("error") or "runner attestation failed")
                    attestation = dict(attestation_payload["attestation"])
                    evidence = dict(attestation.get("evidence") or {})
                    record = RunnerRegistration(
                        runner_id=runner_id,
                        endpoint_url=endpoint_url,
                        service_id=attestation.get("service_id"),
                        tee_mode=evidence.get("attestation_mode"),
                        attestation=attestation,
                        notes=dict(payload.get("notes") or {}),
                    )
                    control.register_runner(record)
                    _maybe_save_state(state_save)
                    _write_json(self, 200, {"ok": True, "runner": record.to_dict()})
                except Exception as exc:
                    _write_json(self, 400, {"ok": False, "error": str(exc)})
                return

            if self.path == "/jobs":
                try:
                    job = control.submit_job(
                        dict(payload.get("request") or payload),
                        runner_id=payload.get("runner_id"),
                        job_type=str(payload.get("job_type") or "prove"),
                    )
                    _maybe_save_state(state_save)
                    _write_json(self, 200, {"ok": True, "job": job.to_dict()})
                except Exception as exc:
                    _write_json(self, 400, {"ok": False, "error": str(exc)})
                return

            self.send_error(404, "not found")

    return Handler


def run_host_server(
    host: str,
    port: int,
    control: HostControlPlane,
    state_save: Callable[[], None] | None = None,
) -> ThreadingHTTPServer:
    handler = make_handler(control, state_save)
    return ThreadingHTTPServer((host, port), handler)


def load_control_from_state_or_config(payload: dict[str, Any]) -> HostControlPlane:
    if not payload:
        return HostControlPlane(host_id="host")
    return restore_host_state(payload)


def load_control_state(path: str | Path) -> HostControlPlane:
    payload = load_host_state(path)
    return restore_host_state(payload) if payload else HostControlPlane(host_id="host")
