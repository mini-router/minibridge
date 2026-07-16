from __future__ import annotations

from argparse import ArgumentParser, Namespace
from decimal import Decimal
from pathlib import Path
from typing import Any
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import (
    LLMProofService,
    MockAttestationProvider,
    MockProvider,
    ModelPrice,
    PricingTable,
    ProviderRegistry,
    ReceiptSigner,
    StaticAttestationProvider,
    verify_receipt,
)
from .bundle import ProofBundle, verify_bundle, write_bundle
from .attestation import AttestationPolicy, FileAttestationProvider
from .http_server import run_server
from .models import LLMRequest, LLMResponse, Proof, Receipt
from .provider import build_provider_from_payload
from .state import load_state, restore_keys, restore_proofs, restore_receipts, save_state
from .reporting import render_json, write_json_report


def _load_json_source(path: str | Path) -> Any:
    if str(path) == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path).read_text(encoding="utf-8")
    return json.loads(raw)


def _dump_json(payload: Any, path: str | Path | None = None) -> None:
    raw = render_json(payload, pretty=True)
    if path is None:
        sys.stdout.write(raw)
        sys.stdout.write("\n")
        return
    if str(path) == "-":
        sys.stdout.write(raw)
        sys.stdout.write("\n")
        return
    write_json_report(path, payload, pretty=True)


def _http_json(method: str, url: str, payload: Any | None = None) -> Any:
    headers = {}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} calling {url}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"request to {url} failed: {exc.reason}") from exc
    return json.loads(raw)


def _build_pricing_table(payload: dict[str, Any], *, default_table_id: str) -> PricingTable:
    table_id = str(payload.get("pricing_table_id") or default_table_id)
    models_payload = payload.get("models") or {}
    models: dict[str, ModelPrice] = {}

    if isinstance(models_payload, list):
        for model_payload in models_payload:
            model = str(model_payload["model"])
            models[model] = ModelPrice(
                model=model,
                input_per_1k=Decimal(str(model_payload["input_per_1k"])),
                output_per_1k=Decimal(str(model_payload["output_per_1k"])),
            )
    else:
        for model, model_payload in dict(models_payload).items():
            model_name = str(model)
            if isinstance(model_payload, dict):
                input_per_1k = model_payload.get("input_per_1k")
                output_per_1k = model_payload.get("output_per_1k")
            else:
                raise TypeError(f"pricing entry for {model_name!r} must be an object")
            models[model_name] = ModelPrice(
                model=model_name,
                input_per_1k=Decimal(str(input_per_1k)),
                output_per_1k=Decimal(str(output_per_1k)),
            )

    return PricingTable(pricing_table_id=table_id, models=models)


def _build_attestation_provider(payload: dict[str, Any] | None) -> Any:
    if payload is None:
        return MockAttestationProvider()
    kind = str(payload.get("kind") or payload.get("type") or "mock")
    if kind == "mock":
        return MockAttestationProvider(
            mode=str(payload.get("mode") or "mock-tee"),
            service_instance_id=str(payload.get("service_instance_id") or "minibridge-local"),
        )
    if kind == "static":
        evidence = dict(payload.get("evidence") or {})
        if not evidence:
            evidence = {
                key: value
                for key, value in payload.items()
                if key not in {"kind", "type", "mode"}
            }
        return StaticAttestationProvider(mode=str(payload.get("mode") or "static-tee"), evidence=evidence)
    if kind in {"file", "cpu-file", "tee-file"}:
        path = payload.get("path")
        if path is None:
            raise ValueError("file attestation provider requires a path")
        return FileAttestationProvider(path=str(path), mode=str(payload.get("mode") or "cpu-tee"))
    raise ValueError(f"unknown attestation provider kind {kind!r}")


def _build_attestation_policy(payload: dict[str, Any] | None) -> AttestationPolicy | None:
    if payload is None:
        return None
    return AttestationPolicy.from_dict(payload)


def _load_or_create_signer(private_key_file: Path) -> ReceiptSigner:
    if private_key_file.exists():
        return ReceiptSigner.import_private_key(private_key_file.read_text(encoding="utf-8").strip())

    private_key_file.parent.mkdir(parents=True, exist_ok=True)
    signer = ReceiptSigner.generate()
    private_key_file.write_text(signer.export_private_key() + "\n", encoding="utf-8")
    try:
        private_key_file.chmod(0o600)
    except OSError:
        pass
    return signer


def _derive_public_key_file(private_key_file: Path) -> Path:
    return Path(f"{private_key_file}.pub")


def _write_public_key_file(public_key_file: Path, signer: ReceiptSigner) -> None:
    public_key_file.parent.mkdir(parents=True, exist_ok=True)
    public_key_file.write_text(signer.export_public_key() + "\n", encoding="utf-8")


def _load_public_key(path: Path) -> Ed25519PublicKey:
    return ReceiptSigner.import_public_key(path.read_text(encoding="utf-8").strip())


def _bootstrap_from_payload(payload: dict[str, Any], signer: ReceiptSigner) -> tuple[LLMProofService, ProviderRegistry]:
    if "pricing_table" not in payload:
        raise ValueError("state/config payload must include pricing_table")
    pricing_table = _build_pricing_table(payload["pricing_table"], default_table_id="minibridge")
    attestation_provider = _build_attestation_provider(payload.get("attestation_provider"))
    attestation_policy = _build_attestation_policy(payload.get("attestation_policy"))
    service = LLMProofService(
        service_id=str(payload.get("service_id") or "minibridge"),
        signer=signer,
        pricing_table=pricing_table,
        tee_mode=str(payload.get("tee_mode") or "tee-ready-mock"),
        attestation=dict(payload.get("attestation") or {}),
        attestation_provider=attestation_provider,
        attestation_policy=attestation_policy,
    )
    registry = ProviderRegistry()
    for provider_payload in payload.get("providers") or []:
        registry.register(build_provider_from_payload(provider_payload))
    restore_keys(service, list(payload.get("keys") or []))
    restore_receipts(service, list(payload.get("receipts") or []))
    restore_proofs(service, list(payload.get("proofs") or []))
    try:
        service.seal_attestation(
            context=service.build_attestation_context(
                provider_registry=registry.providers,
                extra=dict(payload.get("attestation_context") or {}),
            ),
            policy=attestation_policy,
        )
    except Exception:
        if attestation_provider is not None:
            raise
    return service, registry


def _bootstrap_runtime(args: Namespace) -> tuple[LLMProofService, ProviderRegistry, ReceiptSigner, Path, Path | None]:
    private_key_file = Path(args.signing_key_file)
    public_key_file = Path(args.public_key_file) if args.public_key_file else _derive_public_key_file(private_key_file)
    state_file_arg = getattr(args, "state_file", None)
    state_file = Path(state_file_arg) if state_file_arg else None
    signer = _load_or_create_signer(private_key_file)
    _write_public_key_file(public_key_file, signer)

    state_payload = load_state(state_file) if state_file is not None else {}
    if state_payload:
        service, registry = _bootstrap_from_payload(state_payload, signer)
        return service, registry, signer, public_key_file, state_file

    if args.config is None:
        pricing_table = PricingTable(
            pricing_table_id="minibridge-demo",
            models={
                "gpt-demo": ModelPrice(
                    model="gpt-demo",
                    input_per_1k=Decimal("0.0100"),
                    output_per_1k=Decimal("0.0300"),
                )
            },
        )
        service = LLMProofService(
            service_id="minibridge-demo",
            signer=signer,
            pricing_table=pricing_table,
            attestation_provider=MockAttestationProvider(service_instance_id="minibridge-demo"),
        )
        registry = ProviderRegistry()
        registry.register(MockProvider())
        service.seal_attestation(
            context=service.build_attestation_context(provider_registry=registry.providers),
        )
        if state_file is not None:
            save_state(state_file, service, registry)
        return service, registry, signer, public_key_file, state_file

    config = _load_json_source(args.config)
    if not isinstance(config, dict):
        raise TypeError("service config must be a JSON object")

    service, registry = _bootstrap_from_payload(config, signer)
    if state_file is not None:
        save_state(state_file, service, registry)
    return service, registry, signer, public_key_file, state_file


def _build_pricing_table_from_receipt(receipt: Receipt) -> PricingTable:
    return PricingTable(
        pricing_table_id=receipt.pricing_table_id,
        models={
            receipt.model: ModelPrice(
                model=receipt.model,
                input_per_1k=Decimal(receipt.input_token_price_per_1k),
                output_per_1k=Decimal(receipt.output_token_price_per_1k),
            )
        },
    )


def _build_request_from_payload(payload: dict[str, Any], provider_id: str | None = None) -> LLMRequest:
    request_provider_id = str(provider_id or payload.get("provider_id") or "mock")
    return LLMRequest(
        request_id=str(payload["request_id"]),
        provider_id=request_provider_id,
        caller_id=str(payload["caller_id"]),
        owner_id=str(payload["owner_id"]),
        key_id=str(payload["key_id"]),
        model=str(payload["model"]),
        messages=list(payload.get("messages") or []),
        parameters=dict(payload.get("parameters") or {}),
        metadata=dict(payload.get("metadata") or {}),
        nonce=payload.get("nonce"),
        expires_at=payload.get("expires_at"),
    )


def cmd_serve(args: Namespace) -> int:
    service, registry, signer, public_key_file, state_file = _bootstrap_runtime(args)
    state_save = None
    if state_file is not None:
        state_save = lambda: save_state(state_file, service, registry)
    server = run_server(args.host, args.port, service, registry, state_save=state_save)
    host, port = server.server_address
    print(f"minibridge listening on http://{host}:{port}", file=sys.stderr)
    print(f"service_id={service.service_id}", file=sys.stderr)
    print(f"public_key_file={public_key_file}", file=sys.stderr)
    print(f"public_key_fingerprint={signer.public_key_fingerprint}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


def cmd_providers_list(args: Namespace) -> int:
    _dump_json(_http_json("GET", f"{args.server}/providers"), args.output)
    return 0


def cmd_providers_show(args: Namespace) -> int:
    _dump_json(_http_json("GET", f"{args.server}/providers/{args.provider_id}"), args.output)
    return 0


def cmd_providers_add(args: Namespace) -> int:
    payload = _load_json_source(args.payload)
    _dump_json(_http_json("POST", f"{args.server}/register-provider", payload), args.output)
    return 0


def cmd_keys_add(args: Namespace) -> int:
    payload = _load_json_source(args.payload)
    _dump_json(_http_json("POST", f"{args.server}/register-key", payload), args.output)
    return 0


def cmd_receipts_list(args: Namespace) -> int:
    _dump_json(_http_json("GET", f"{args.server}/receipts"), args.output)
    return 0


def cmd_call(args: Namespace) -> int:
    payload = _load_json_source(args.payload)
    provider_id = args.provider_id or payload.get("provider_id")
    if provider_id is not None:
        payload["provider_id"] = provider_id
        url = f"{args.server}/providers/{provider_id}/call"
    else:
        url = f"{args.server}/call"
    _dump_json(_http_json("POST", url, payload), args.output)
    return 0


def cmd_prove(args: Namespace) -> int:
    payload = _load_json_source(args.payload)
    provider_id = args.provider_id or payload.get("provider_id")
    if provider_id is not None:
        payload["provider_id"] = provider_id
        url = f"{args.server}/providers/{provider_id}/prove"
    else:
        url = f"{args.server}/prove"
    _dump_json(_http_json("POST", url, payload), args.output)
    return 0


def cmd_proofs_list(args: Namespace) -> int:
    _dump_json(_http_json("GET", f"{args.server}/proofs"), args.output)
    return 0


def cmd_proofs_show(args: Namespace) -> int:
    _dump_json(_http_json("GET", f"{args.server}/proofs/{args.proof_id}"), args.output)
    return 0


def cmd_bundle_create(args: Namespace) -> int:
    payload = _http_json("GET", f"{args.server}/bundle/export")
    if not payload.get("ok"):
        raise RuntimeError("failed to fetch bundle export")
    bundle = ProofBundle.from_dict(dict(payload["bundle"]))
    bundle_dir = write_bundle(args.bundle, bundle)
    payload = {
        "ok": True,
        "bundle_dir": str(bundle_dir),
        "manifest": bundle.manifest.to_dict(),
        "raw_proof_count": len(bundle.raw_proofs),
        "proof_count": len(bundle.verified_proofs),
    }
    _dump_json(payload, args.output)
    return 0


def cmd_bundle_verify(args: Namespace) -> int:
    public_key = None
    if args.public_key_file is not None:
        public_key = _load_public_key(Path(args.public_key_file))
    result = verify_bundle(args.bundle, public_key=public_key)
    _dump_json({"ok": result["verified"], "result": result}, args.output)
    return 0 if result["verified"] else 1


def cmd_verify(args: Namespace) -> int:
    proof_obj = None
    if args.proof is not None:
        proof_obj = Proof.from_dict(_load_json_source(args.proof))

    if args.receipt is not None:
        receipt_payload = _load_json_source(args.receipt)
        receipt = Receipt.from_dict(receipt_payload)
    elif proof_obj is not None:
        receipt = proof_obj.receipt
    else:
        raise ValueError("verify requires --receipt or --proof")

    request_obj = proof_obj.request if proof_obj is not None else None
    response_obj = proof_obj.response if proof_obj is not None else None
    if args.request is not None:
        request_obj = LLMRequest.from_dict(_load_json_source(args.request))
    if args.response is not None:
        response_obj = LLMResponse.from_dict(_load_json_source(args.response))
    if args.pricing_table is not None:
        pricing_table_payload = _load_json_source(args.pricing_table)
        if not isinstance(pricing_table_payload, dict):
            raise TypeError("pricing table file must contain a JSON object")
        pricing_table = _build_pricing_table(pricing_table_payload, default_table_id=receipt.pricing_table_id)
    else:
        pricing_table = _build_pricing_table_from_receipt(receipt)

    if args.public_key_file is not None:
        public_key = _load_public_key(Path(args.public_key_file))
    elif args.signing_key_file is not None:
        public_key = ReceiptSigner.import_private_key(Path(args.signing_key_file).read_text(encoding="utf-8").strip()).public_key
    else:
        raise ValueError("verify requires --public-key-file or --signing-key-file")

    result = verify_receipt(
        receipt,
        public_key,
        pricing_table,
        request=request_obj,
        response=response_obj,
    )
    payload = {"ok": result.ok, "result": asdict(result)}
    _dump_json(payload, args.output)
    return 0 if result.ok else 1


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(prog="minibridge", description="Run and inspect Minibridge proof receipts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Run the Minibridge HTTP service.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--config", default=None, help="Bootstrap config JSON file.")
    serve.add_argument(
        "--signing-key-file",
        default=".minibridge-signing.key",
        help="File that stores the receipt signing private key.",
    )
    serve.add_argument(
        "--public-key-file",
        default=None,
        help="File that stores the verifier-facing public key. Defaults to <signing-key-file>.pub.",
    )
    serve.add_argument(
        "--state-file",
        default=".minibridge-state.json",
        help="Persistent JSON state file for providers, keys, and receipts. Use an empty string to disable.",
    )
    serve.set_defaults(func=cmd_serve)

    providers = subparsers.add_parser("providers", help="Inspect or register upstream providers.")
    providers_sub = providers.add_subparsers(dest="providers_command", required=True)

    providers_list = providers_sub.add_parser("list", help="List registered providers.")
    providers_list.add_argument("--server", default="http://127.0.0.1:8080")
    providers_list.add_argument("--output", default=None, help="Write JSON to this file instead of stdout.")
    providers_list.set_defaults(func=cmd_providers_list)

    providers_show = providers_sub.add_parser("show", help="Describe a single provider.")
    providers_show.add_argument("provider_id")
    providers_show.add_argument("--server", default="http://127.0.0.1:8080")
    providers_show.add_argument("--output", default=None, help="Write JSON to this file instead of stdout.")
    providers_show.set_defaults(func=cmd_providers_show)

    providers_add = providers_sub.add_parser("add", help="Register a provider from JSON.")
    providers_add.add_argument("--server", default="http://127.0.0.1:8080")
    providers_add.add_argument("--payload", default="-", help="Provider payload JSON file or - for stdin.")
    providers_add.add_argument("--output", default=None, help="Write JSON to this file instead of stdout.")
    providers_add.set_defaults(func=cmd_providers_add)

    keys = subparsers.add_parser("keys", help="Enroll API keys.")
    keys_sub = keys.add_subparsers(dest="keys_command", required=True)

    keys_add = keys_sub.add_parser("add", help="Register a key from JSON.")
    keys_add.add_argument("--server", default="http://127.0.0.1:8080")
    keys_add.add_argument("--payload", default="-", help="Key payload JSON file or - for stdin.")
    keys_add.add_argument("--output", default=None, help="Write JSON to this file instead of stdout.")
    keys_add.set_defaults(func=cmd_keys_add)

    receipts = subparsers.add_parser("receipts", help="Inspect receipts recorded by the service.")
    receipts_sub = receipts.add_subparsers(dest="receipts_command", required=True)

    receipts_list = receipts_sub.add_parser("list", help="List receipts.")
    receipts_list.add_argument("--server", default="http://127.0.0.1:8080")
    receipts_list.add_argument("--output", default=None, help="Write JSON to this file instead of stdout.")
    receipts_list.set_defaults(func=cmd_receipts_list)

    call = subparsers.add_parser("call", help="Submit an LLM request through Minibridge.")
    call.add_argument("--server", default="http://127.0.0.1:8080")
    call.add_argument("--provider-id", default=None, help="Override the request provider_id and route.")
    call.add_argument("--payload", default="-", help="Request payload JSON file or - for stdin.")
    call.add_argument("--output", default=None, help="Write JSON to this file instead of stdout.")
    call.set_defaults(func=cmd_call)

    prove = subparsers.add_parser("prove", help="Submit an LLM request and capture a proof bundle.")
    prove.add_argument("--server", default="http://127.0.0.1:8080")
    prove.add_argument("--provider-id", default=None, help="Override the request provider_id and route.")
    prove.add_argument("--payload", default="-", help="Request payload JSON file or - for stdin.")
    prove.add_argument("--output", default=None, help="Write JSON to this file instead of stdout.")
    prove.set_defaults(func=cmd_prove)

    proofs = subparsers.add_parser("proofs", help="Inspect proofs captured by the service.")
    proofs_sub = proofs.add_subparsers(dest="proofs_command", required=True)

    proofs_list = proofs_sub.add_parser("list", help="List proofs.")
    proofs_list.add_argument("--server", default="http://127.0.0.1:8080")
    proofs_list.add_argument("--output", default=None, help="Write JSON to this file instead of stdout.")
    proofs_list.set_defaults(func=cmd_proofs_list)

    proofs_show = proofs_sub.add_parser("show", help="Describe a single proof.")
    proofs_show.add_argument("proof_id")
    proofs_show.add_argument("--server", default="http://127.0.0.1:8080")
    proofs_show.add_argument("--output", default=None, help="Write JSON to this file instead of stdout.")
    proofs_show.set_defaults(func=cmd_proofs_show)

    bundle = subparsers.add_parser("bundle", help="Create or verify proof bundles.")
    bundle_sub = bundle.add_subparsers(dest="bundle_command", required=True)

    bundle_create = bundle_sub.add_parser("create", help="Create a bundle directory from a running service.")
    bundle_create.add_argument("--server", default="http://127.0.0.1:8080")
    bundle_create.add_argument("--bundle", required=True, help="Output bundle directory.")
    bundle_create.add_argument("--output", default=None, help="Write JSON to this file instead of stdout.")
    bundle_create.set_defaults(func=cmd_bundle_create)

    bundle_verify = bundle_sub.add_parser("verify", help="Verify a bundle directory offline.")
    bundle_verify.add_argument("--bundle", required=True, help="Bundle directory to verify.")
    bundle_verify.add_argument("--public-key-file", default=None, help="Optional service public key file.")
    bundle_verify.add_argument("--output", default=None, help="Write JSON to this file instead of stdout.")
    bundle_verify.set_defaults(func=cmd_bundle_verify)

    verify = subparsers.add_parser("verify", help="Verify a signed receipt.")
    verify.add_argument("--receipt", default=None, help="Receipt JSON file or - for stdin.")
    verify.add_argument("--proof", default=None, help="Proof JSON file or - for stdin.")
    verify.add_argument("--request", default=None, help="Optional request JSON file.")
    verify.add_argument("--response", default=None, help="Optional response JSON file.")
    verify.add_argument(
        "--pricing-table",
        default=None,
        help="Optional pricing table JSON file. If omitted, verification uses the receipt's embedded pricing data.",
    )
    verify.add_argument("--public-key-file", default=None, help="Verifier public key file.")
    verify.add_argument("--signing-key-file", default=None, help="Signer private key file.")
    verify.add_argument("--output", default=None, help="Write JSON to this file instead of stdout.")
    verify.set_defaults(func=cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
