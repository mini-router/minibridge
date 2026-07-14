from __future__ import annotations

from argparse import ArgumentParser, Namespace
from dataclasses import asdict
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
from .http_server import run_server
from .models import LLMRequest, LLMResponse, Receipt
from .provider import build_provider_from_payload
from .state import load_state, restore_keys, restore_receipts, save_state


def _load_json_source(path: str | Path) -> Any:
    if str(path) == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path).read_text(encoding="utf-8")
    return json.loads(raw)


def _dump_json(payload: Any, path: str | Path | None = None) -> None:
    raw = json.dumps(payload, indent=2, sort_keys=True, default=asdict)
    if path is None:
        sys.stdout.write(raw)
        sys.stdout.write("\n")
        return
    if str(path) == "-":
        sys.stdout.write(raw)
        sys.stdout.write("\n")
        return
    Path(path).write_text(raw + "\n", encoding="utf-8")


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
    raise ValueError(f"unknown attestation provider kind {kind!r}")


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
    service = LLMProofService(
        service_id=str(payload.get("service_id") or "minibridge"),
        signer=signer,
        pricing_table=pricing_table,
        tee_mode=str(payload.get("tee_mode") or "tee-ready-mock"),
        attestation=dict(payload.get("attestation") or {}),
        attestation_provider=attestation_provider,
    )
    registry = ProviderRegistry()
    for provider_payload in payload.get("providers") or []:
        registry.register(build_provider_from_payload(provider_payload))
    restore_keys(service, list(payload.get("keys") or []))
    restore_receipts(service, list(payload.get("receipts") or []))
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


def cmd_verify(args: Namespace) -> int:
    receipt_payload = _load_json_source(args.receipt)
    receipt = Receipt.from_dict(receipt_payload)
    request_obj = None
    response_obj = None
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

    verify = subparsers.add_parser("verify", help="Verify a signed receipt.")
    verify.add_argument("--receipt", required=True, help="Receipt JSON file or - for stdin.")
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
