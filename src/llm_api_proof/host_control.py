from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import uuid
from urllib.request import Request, urlopen

from .bundle import ProofBundle, verify_bundle


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"cannot serialize {type(value)!r}")


def _http_json(method: str, url: str, payload: Any | None = None) -> Any:
    headers = {}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


@dataclass
class RunnerRegistration:
    runner_id: str
    endpoint_url: str
    service_id: str | None = None
    tee_mode: str | None = None
    attestation: dict[str, Any] = field(default_factory=dict)
    active: bool = True
    last_seen_at: str | None = None
    notes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runner_id": self.runner_id,
            "endpoint_url": self.endpoint_url,
            "service_id": self.service_id,
            "tee_mode": self.tee_mode,
            "attestation": dict(self.attestation),
            "active": self.active,
            "last_seen_at": self.last_seen_at,
            "notes": dict(self.notes),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RunnerRegistration":
        return cls(
            runner_id=str(payload["runner_id"]),
            endpoint_url=str(payload["endpoint_url"]),
            service_id=payload.get("service_id"),
            tee_mode=payload.get("tee_mode"),
            attestation=dict(payload.get("attestation") or {}),
            active=bool(payload.get("active", True)),
            last_seen_at=payload.get("last_seen_at"),
            notes=dict(payload.get("notes") or {}),
        )


@dataclass
class HostJobRecord:
    job_id: str
    job_type: str
    runner_id: str
    request: dict[str, Any]
    status: str
    submitted_at: str
    completed_at: str | None = None
    response: dict[str, Any] | None = None
    receipt: dict[str, Any] | None = None
    proof: dict[str, Any] | None = None
    bundle: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "runner_id": self.runner_id,
            "request": dict(self.request),
            "status": self.status,
            "submitted_at": self.submitted_at,
            "completed_at": self.completed_at,
            "response": self.response,
            "receipt": self.receipt,
            "proof": self.proof,
            "bundle": self.bundle,
            "verification": self.verification,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "HostJobRecord":
        return cls(
            job_id=str(payload["job_id"]),
            job_type=str(payload.get("job_type") or "prove"),
            runner_id=str(payload["runner_id"]),
            request=dict(payload.get("request") or {}),
            status=str(payload.get("status") or "queued"),
            submitted_at=str(payload["submitted_at"]),
            completed_at=payload.get("completed_at"),
            response=payload.get("response"),
            receipt=payload.get("receipt"),
            proof=payload.get("proof"),
            bundle=payload.get("bundle"),
            verification=payload.get("verification"),
            error=payload.get("error"),
        )


@dataclass
class HostControlPlane:
    host_id: str
    runners: dict[str, RunnerRegistration] = field(default_factory=dict)
    jobs: dict[str, HostJobRecord] = field(default_factory=dict)

    def register_runner(self, record: RunnerRegistration) -> RunnerRegistration:
        self.runners[record.runner_id] = record
        return record

    def list_runners(self) -> list[RunnerRegistration]:
        return list(self.runners.values())

    def get_runner(self, runner_id: str) -> RunnerRegistration:
        try:
            return self.runners[runner_id]
        except KeyError as exc:
            raise KeyError(f"unknown runner {runner_id!r}") from exc

    def list_jobs(self) -> list[HostJobRecord]:
        return list(self.jobs.values())

    def get_job(self, job_id: str) -> HostJobRecord:
        try:
            return self.jobs[job_id]
        except KeyError as exc:
            raise KeyError(f"unknown job {job_id!r}") from exc

    def submit_job(
        self,
        request: dict[str, Any],
        *,
        runner_id: str | None = None,
        job_type: str = "prove",
    ) -> HostJobRecord:
        runner = self._select_runner(runner_id)
        job_id = str(request.get("job_id") or f"job_{uuid.uuid4().hex}")
        submitted_at = datetime.now(timezone.utc).isoformat()
        record = HostJobRecord(
            job_id=job_id,
            job_type=job_type,
            runner_id=runner.runner_id,
            request=dict(request),
            status="queued",
            submitted_at=submitted_at,
        )
        self.jobs[job_id] = record
        record.status = "running"
        try:
            payload = {
                "job_id": job_id,
                "job_type": job_type,
                "request": request,
            }
            result = _http_json("POST", f"{runner.endpoint_url.rstrip('/')}/execute-job", payload)
            if not result.get("ok"):
                raise RuntimeError(result.get("error") or "runner job execution failed")
            record.status = "completed"
            record.completed_at = datetime.now(timezone.utc).isoformat()
            record.response = result.get("response")
            record.receipt = result.get("receipt")
            record.proof = result.get("proof")
            record.bundle = result.get("bundle")
            record.verification = result.get("verification")
            runner.last_seen_at = record.completed_at
            runner.service_id = result.get("runner", {}).get("service_id", runner.service_id)
            runner.tee_mode = result.get("runner", {}).get("tee_mode", runner.tee_mode)
            runner.attestation = dict(result.get("runner", {}).get("attestation") or runner.attestation)
            return record
        except Exception as exc:
            record.status = "failed"
            record.completed_at = datetime.now(timezone.utc).isoformat()
            record.error = str(exc)
            raise

    def export_job_bundle(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job.bundle is None:
            raise KeyError(f"job {job_id!r} has no bundle")
        return dict(job.bundle)

    def verify_job_bundle(self, job_id: str) -> dict[str, Any]:
        bundle_payload = self.export_job_bundle(job_id)
        bundle = ProofBundle.from_dict(bundle_payload)
        bundle_dir = Path("/tmp") / f"minibridge-job-{job_id}"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        from .bundle import write_bundle

        write_bundle(bundle_dir, bundle)
        return verify_bundle(bundle_dir)

    def _select_runner(self, runner_id: str | None) -> RunnerRegistration:
        if runner_id is not None:
            runner = self.get_runner(runner_id)
            if not runner.active:
                raise RuntimeError(f"runner {runner_id!r} is disabled")
            return runner
        active_runners = [runner for runner in self.runners.values() if runner.active]
        if not active_runners:
            raise RuntimeError("no active runners are registered")
        return active_runners[0]


def load_host_state(path: str | Path) -> dict[str, Any]:
    state_path = Path(path)
    if not state_path.exists():
        return {}
    raw = state_path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise TypeError("host state file must contain a JSON object")
    return payload


def save_host_state(path: str | Path, control: HostControlPlane) -> None:
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "host_id": control.host_id,
        "runners": [runner.to_dict() for runner in control.list_runners()],
        "jobs": [job.to_dict() for job in control.list_jobs()],
    }
    tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")
    raw = json.dumps(payload, default=_json_default, indent=2, sort_keys=True)
    tmp_path.write_text(raw + "\n", encoding="utf-8")
    tmp_path.replace(state_path)


def restore_host_state(payload: dict[str, Any]) -> HostControlPlane:
    control = HostControlPlane(host_id=str(payload.get("host_id") or "host"))
    for runner_payload in list(payload.get("runners") or []):
        control.register_runner(RunnerRegistration.from_dict(dict(runner_payload)))
    for job_payload in list(payload.get("jobs") or []):
        job = HostJobRecord.from_dict(dict(job_payload))
        control.jobs[job.job_id] = job
    return control
