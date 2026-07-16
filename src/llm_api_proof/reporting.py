from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any
import gzip
import json


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"cannot serialize {type(value)!r}")


def render_json(payload: Any, *, pretty: bool = True) -> str:
    if pretty:
        return json.dumps(payload, indent=2, sort_keys=True, default=_json_default)
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=_json_default)


def write_json_report(path: str | Path, payload: Any, *, pretty: bool = True) -> Path:
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    raw = render_json(payload, pretty=pretty)

    if report_path.suffix == ".gz":
        report_path.write_bytes(gzip.compress((raw + "\n").encode("utf-8"), compresslevel=9))
    else:
        report_path.write_text(raw + "\n", encoding="utf-8")
    return report_path


def read_json_report(path: str | Path) -> Any:
    report_path = Path(path)
    if report_path.suffix == ".gz":
        raw = gzip.decompress(report_path.read_bytes()).decode("utf-8")
    else:
        raw = report_path.read_text(encoding="utf-8")
    return json.loads(raw)


def compression_stats(payload: Any) -> dict[str, float | int]:
    pretty = render_json(payload, pretty=True) + "\n"
    compact = render_json(payload, pretty=False) + "\n"
    gzipped = gzip.compress(pretty.encode("utf-8"), compresslevel=9)
    pretty_bytes = len(pretty.encode("utf-8"))
    compact_bytes = len(compact.encode("utf-8"))
    gzip_bytes = len(gzipped)
    return {
        "pretty_bytes": pretty_bytes,
        "compact_bytes": compact_bytes,
        "gzip_bytes": gzip_bytes,
        "compact_ratio": round(compact_bytes / pretty_bytes, 4),
        "gzip_ratio": round(gzip_bytes / pretty_bytes, 4),
        "pretty_to_gzip_reduction_percent": round((1 - gzip_bytes / pretty_bytes) * 100, 2),
    }
