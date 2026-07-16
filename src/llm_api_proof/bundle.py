from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, UTC
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .models import Proof, sha256_hex
from .pricing import ModelPrice, PricingTable
from .signing import ReceiptSigner
from .verifier import verify_receipt


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"cannot serialize {type(value)!r}")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=_json_default) + "\n")


def _merkle_root(leaves: list[str]) -> str:
    if not leaves:
        return sha256_hex("")
    nodes = list(leaves)
    while len(nodes) > 1:
        if len(nodes) % 2 == 1:
            nodes.append(nodes[-1])
        next_level: list[str] = []
        for left, right in zip(nodes[0::2], nodes[1::2]):
            next_level.append(sha256_hex(left + right))
        nodes = next_level
    return nodes[0]


def _proof_leaf_hash(proof: Proof | Mapping[str, Any]) -> str:
    payload = proof.to_dict() if isinstance(proof, Proof) else dict(proof)
    return sha256_hex(payload)


def _proof_to_dict(proof: Proof | Mapping[str, Any]) -> dict[str, Any]:
    return proof.to_dict() if isinstance(proof, Proof) else dict(proof)


def _pricing_table_from_proofs(proofs: list[Proof | Mapping[str, Any]]) -> PricingTable:
    models: dict[str, ModelPrice] = {}
    for proof in proofs:
        proof_dict = _proof_to_dict(proof)
        receipt = dict(proof_dict["receipt"])
        model = str(receipt["model"])
        model_price = ModelPrice(
            model=model,
            input_per_1k=Decimal(str(receipt["input_token_price_per_1k"])),
            output_per_1k=Decimal(str(receipt["output_token_price_per_1k"])),
        )
        existing = models.get(model)
        if existing is not None and existing != model_price:
            raise ValueError(f"conflicting pricing entry for model {model!r}")
        models[model] = model_price
    return PricingTable(pricing_table_id="minibridge-bundle-pricing", models=models)


@dataclass(frozen=True)
class ProofBundleManifest:
    version: str
    generator_version: str
    bundle_kind: str
    service_id: str
    service_public_key_fingerprint: str
    service_public_key: str
    tee_mode: str
    raw_proof_count: int
    proof_count: int
    merkle_root: str
    created_at: str
    proof_ids: list[str] = field(default_factory=list)
    attestation_hash: str | None = None
    attestation_verified: bool = False
    validation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "version": self.version,
            "generator_version": self.generator_version,
            "bundle_kind": self.bundle_kind,
            "service_id": self.service_id,
            "service_public_key_fingerprint": self.service_public_key_fingerprint,
            "service_public_key": self.service_public_key,
            "tee_mode": self.tee_mode,
            "raw_proof_count": self.raw_proof_count,
            "proof_count": self.proof_count,
            "merkle_root": self.merkle_root,
            "created_at": self.created_at,
            "proof_ids": list(self.proof_ids),
            "attestation_verified": self.attestation_verified,
            "validation": dict(self.validation),
        }
        if self.attestation_hash is not None:
            payload["attestation_hash"] = self.attestation_hash
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProofBundleManifest":
        return cls(
            version=str(payload["version"]),
            generator_version=str(payload["generator_version"]),
            bundle_kind=str(payload.get("bundle_kind") or "llm-api-proof"),
            service_id=str(payload["service_id"]),
            service_public_key_fingerprint=str(payload["service_public_key_fingerprint"]),
            service_public_key=str(payload["service_public_key"]),
            tee_mode=str(payload["tee_mode"]),
            raw_proof_count=int(payload.get("raw_proof_count", payload.get("proof_count", 0))),
            proof_count=int(payload["proof_count"]),
            merkle_root=str(payload["merkle_root"]),
            created_at=str(payload["created_at"]),
            proof_ids=list(payload.get("proof_ids") or []),
            attestation_hash=payload.get("attestation_hash"),
            attestation_verified=bool(payload.get("attestation_verified", False)),
            validation=dict(payload.get("validation") or {}),
        )


@dataclass(frozen=True)
class ProofBundle:
    manifest: ProofBundleManifest
    raw_proofs: list[dict[str, Any]]
    verified_proofs: list[dict[str, Any]]
    validation_report: list[dict[str, Any]]
    attestation: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "manifest": self.manifest.to_dict(),
            "raw_proofs": list(self.raw_proofs),
            "verified_proofs": list(self.verified_proofs),
            "validation_report": list(self.validation_report),
        }
        if self.attestation is not None:
            payload["attestation"] = dict(self.attestation)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProofBundle":
        return cls(
            manifest=ProofBundleManifest.from_dict(dict(payload["manifest"])),
            raw_proofs=[dict(row) for row in list(payload.get("raw_proofs") or [])],
            verified_proofs=[dict(row) for row in list(payload.get("verified_proofs") or [])],
            validation_report=[dict(row) for row in list(payload.get("validation_report") or [])],
            attestation=dict(payload.get("attestation")) if payload.get("attestation") is not None else None,
        )


@dataclass(frozen=True)
class BundleVerificationResult:
    verified: bool
    issues: list[str]
    service_id: str
    tee_mode: str
    manifest_version: str
    raw_proof_count: int
    proof_count: int
    merkle_root: str
    attested: bool


def build_bundle(
    proofs: list[Proof | Mapping[str, Any]],
    *,
    service_id: str,
    tee_mode: str,
    service_public_key: str,
    service_public_key_fingerprint: str,
    attestation: dict[str, Any] | None = None,
    generator_version: str = "minibridge-bundle-1",
    bundle_kind: str = "llm-api-proof",
    created_at: str | None = None,
) -> ProofBundle:
    raw_proofs = [_proof_to_dict(proof) for proof in proofs]
    validation_report: list[dict[str, Any]] = []
    verified_proofs: list[dict[str, Any]] = []

    public_key = ReceiptSigner.import_public_key(service_public_key)
    pricing_table = _pricing_table_from_proofs(proofs)

    for index, proof_dict in enumerate(raw_proofs):
        proof = Proof.from_dict(proof_dict)
        result = verify_receipt(
            proof.receipt,
            public_key,
            pricing_table,
            request=proof.request,
            response=proof.response,
        )
        errors = list(result.errors)
        if proof.proof_id != proof.receipt.receipt_id:
            errors.append("proof_id does not match receipt_id")
        if proof.service_id != service_id:
            errors.append(f"proof service_id mismatch: expected {service_id!r}, got {proof.service_id!r}")
        if proof.receipt.service_public_key_fingerprint != service_public_key_fingerprint:
            errors.append("receipt service public key fingerprint mismatch")
        passed = result.ok and not errors
        validation_report.append(
            {
                "index": index,
                "proof_id": proof.proof_id,
                "receipt_id": proof.receipt.receipt_id,
                "request_id": proof.request.request_id,
                "model": proof.request.model,
                "provider_id": proof.request.provider_id,
                "passed": passed,
                "validation": {
                    "ok": result.ok,
                    "errors": errors,
                    "receipt_validation": asdict(result),
                },
            }
        )
        if passed:
            verified_proofs.append(proof_dict)

    leaves = [_proof_leaf_hash(proof) for proof in verified_proofs]
    attestation_hash = sha256_hex(attestation) if attestation is not None else None
    manifest = ProofBundleManifest(
        version="minibridge-bundle-1",
        generator_version=generator_version,
        bundle_kind=bundle_kind,
        service_id=service_id,
        service_public_key_fingerprint=service_public_key_fingerprint,
        service_public_key=service_public_key,
        tee_mode=tee_mode,
        raw_proof_count=len(raw_proofs),
        proof_count=len(verified_proofs),
        merkle_root=_merkle_root(leaves),
        created_at=created_at or datetime.now(UTC).isoformat(),
        proof_ids=[proof["proof_id"] for proof in verified_proofs],
        attestation_hash=attestation_hash,
        attestation_verified=bool(attestation.get("verified")) if attestation is not None else False,
        validation={
            "checks": [
                "receipt_signature",
                "request_hash",
                "response_hash",
                "cost",
                "proof_identity",
                "service_identity",
                "public_key_fingerprint",
            ]
        },
    )
    return ProofBundle(
        manifest=manifest,
        raw_proofs=raw_proofs,
        verified_proofs=verified_proofs,
        validation_report=validation_report,
        attestation=attestation,
    )


def write_bundle(out_dir: str | Path, bundle: ProofBundle) -> Path:
    bundle_dir = Path(out_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "manifest.json").write_text(
        json.dumps(bundle.manifest.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _write_jsonl(bundle_dir / "trajectories_raw.jsonl", bundle.raw_proofs)
    _write_jsonl(bundle_dir / "trajectories.jsonl", bundle.verified_proofs)
    _write_jsonl(bundle_dir / "validation_report.jsonl", bundle.validation_report)
    if bundle.attestation is not None:
        (bundle_dir / "attestation.json").write_text(
            json.dumps(bundle.attestation, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return bundle_dir


def verify_bundle(bundle_dir: str | Path, *, public_key: Ed25519PublicKey | None = None) -> dict[str, Any]:
    root = Path(bundle_dir)
    manifest_path = root / "manifest.json"
    verified_path = root / "trajectories.jsonl"
    raw_path = root / "trajectories_raw.jsonl"
    validation_path = root / "validation_report.jsonl"
    attestation_path = root / "attestation.json"

    if not manifest_path.exists() or not verified_path.exists():
        raise FileNotFoundError("bundle must contain manifest.json and trajectories.jsonl")

    manifest = ProofBundleManifest.from_dict(_load_json(manifest_path))
    verified_proofs = _load_jsonl(verified_path)
    raw_proofs = _load_jsonl(raw_path) if raw_path.exists() else list(verified_proofs)
    validation_report = _load_jsonl(validation_path) if validation_path.exists() else []
    attestation = _load_json(attestation_path) if attestation_path.exists() else None

    issues: list[str] = []
    if manifest.version != "minibridge-bundle-1":
        issues.append(f"unsupported manifest version: {manifest.version!r}")
    if manifest.raw_proof_count != len(raw_proofs):
        issues.append(f"raw_proof_count mismatch: manifest={manifest.raw_proof_count} raw={len(raw_proofs)}")
    if manifest.proof_count != len(verified_proofs):
        issues.append(f"proof_count mismatch: manifest={manifest.proof_count} verified={len(verified_proofs)}")

    if attestation is not None:
        if manifest.attestation_hash is not None and sha256_hex(attestation) != manifest.attestation_hash:
            issues.append("attestation hash mismatch")
        if not attestation.get("verified", False):
            issues.append("attestation not verified")
        evidence = dict(attestation.get("evidence") or {})
        if evidence.get("service_public_key_fingerprint") != manifest.service_public_key_fingerprint:
            issues.append("attestation service public key fingerprint mismatch")

    public_key_obj = public_key
    if public_key_obj is None:
        try:
            public_key_obj = ReceiptSigner.import_public_key(manifest.service_public_key)
        except Exception as exc:
            issues.append(f"could not import manifest service public key: {exc}")

    pricing_table = None
    if verified_proofs:
        try:
            pricing_table = _pricing_table_from_proofs(verified_proofs)
        except Exception as exc:
            issues.append(str(exc))

    if public_key_obj is not None and pricing_table is not None:
        for index, proof_dict in enumerate(verified_proofs):
            proof = Proof.from_dict(proof_dict)
            result = verify_receipt(
                proof.receipt,
                public_key_obj,
                pricing_table,
                request=proof.request,
                response=proof.response,
            )
            if not result.ok:
                issues.append(f"proof[{index}]: " + "; ".join(result.errors))
            if proof.proof_id != proof.receipt.receipt_id:
                issues.append(f"proof[{index}]: proof_id does not match receipt_id")
            if proof.service_id != manifest.service_id:
                issues.append(f"proof[{index}]: service_id mismatch")
            if proof.receipt.service_public_key_fingerprint != manifest.service_public_key_fingerprint:
                issues.append(f"proof[{index}]: public key fingerprint mismatch")

    if validation_report:
        passing_indices = [row.get("index") for row in validation_report if row.get("passed")]
        if len(passing_indices) != len(verified_proofs):
            issues.append("verified proof count does not match validation report")
        verified_ids = {row["proof_id"] for row in validation_report if row.get("passed")}
        if verified_ids != set(manifest.proof_ids):
            issues.append("manifest proof_ids do not match passing validation report rows")

    root_hash = _merkle_root([_proof_leaf_hash(proof) for proof in verified_proofs])
    if root_hash != manifest.merkle_root:
        issues.append(f"merkle_root mismatch: expected {manifest.merkle_root!r}, got {root_hash!r}")

    if raw_proofs and validation_report:
        raw_ids = {proof["proof_id"] for proof in raw_proofs}
        reported_ids = {row["proof_id"] for row in validation_report}
        if raw_ids != reported_ids:
            issues.append("validation_report rows do not match raw proofs")

    return {
        "verified": len(issues) == 0,
        "issues": issues,
        "service_id": manifest.service_id,
        "tee_mode": manifest.tee_mode,
        "manifest_version": manifest.version,
        "raw_proof_count": len(raw_proofs),
        "proof_count": len(verified_proofs),
        "merkle_root": manifest.merkle_root,
        "attested": bool(attestation is not None),
    }
