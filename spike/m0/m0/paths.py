"""Filesystem locations and JSONL helpers for the M0 spike. Throwaway code."""
import json
import os
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]


def data_dir() -> Path:
    return Path(os.environ.get("M0_DATA", REPO_ROOT / "data" / "m0"))


def data_path(*parts: str) -> Path:
    p = data_dir().joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def blob_path(oid: str) -> Path:
    return data_dir() / "blobs" / oid[:2] / f"{oid}.txt"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read JSONL, skipping malformed lines (a kill -9 can leave a partial line)."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def append_jsonl(path: Path, rec: dict[str, Any]) -> None:
    """Append one record; if a previous write was cut off, start on a fresh line."""
    prefix = ""
    if path.exists() and path.stat().st_size > 0:
        with path.open("rb") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                prefix = "\n"
    with path.open("a") as f:
        f.write(prefix + json.dumps(rec, sort_keys=True) + "\n")


def write_metrics(name: str, obj: Any) -> Path:
    p = data_path("metrics", f"{name}.json")
    p.write_text(json.dumps(obj, indent=2, sort_keys=True))
    return p


def read_metrics(name: str) -> Any | None:
    p = data_dir() / "metrics" / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None
