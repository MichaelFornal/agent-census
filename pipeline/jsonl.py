"""JSONL that survives kill -9: a cut-off last line is skipped, and the next append starts a fresh line."""
import json
import os
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    lines = path.read_text().splitlines()
    # Find indices of non-empty lines
    non_empty_indices = [i for i, line in enumerate(lines) if line.strip()]

    out = []
    for idx, line_idx in enumerate(non_empty_indices):
        line = lines[line_idx]
        is_last_non_empty = (idx == len(non_empty_indices) - 1)

        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            if is_last_non_empty:
                # Last non-empty line is partial from killed writer; skip it
                continue
            else:
                # Corrupt line in the middle; raise with line number
                raise ValueError(f"{path}:{line_idx + 1}: corrupt JSONL line") from e

        # Check that the object is a dict (not just any JSON)
        if not isinstance(obj, dict):
            if is_last_non_empty:
                # Last line is non-object; skip it as partial
                continue
            else:
                # Non-object line in the middle; raise
                raise ValueError(f"{path}:{line_idx + 1}: corrupt JSONL line")

        out.append(obj)

    return out


def append_jsonl(path: Path, rec: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if path.exists() and path.stat().st_size > 0:
        with path.open("rb") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                prefix = "\n"
    with path.open("a") as f:
        f.write(prefix + json.dumps(rec, sort_keys=True) + "\n")
