"""Per-stage completion journal (PRD §4): one JSONL line per committed batch of units."""
import hashlib
import json
import time
from pathlib import Path

from pipeline.jsonl import append_jsonl, read_jsonl


def unit_key(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:24]


def part_name(keys: list[str]) -> str:
    return "p-" + unit_key(sorted(keys))[:16]


class Journal:
    def __init__(self, path: Path) -> None:
        self.path = path

    def entries(self) -> list[dict]:
        return [e for e in read_jsonl(self.path) if "part" in e and "units" in e]

    def done_units(self) -> set[str]:
        return {u for e in self.entries() for u in e["units"]}

    def parts(self) -> set[str]:
        return {e["part"] for e in self.entries()}

    def record(self, part: str, units: list[str], rows: dict[str, int]) -> None:
        append_jsonl(self.path, {"part": part, "units": units, "rows": rows,
                                 "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
