"""JSONL that survives kill -9: discards partial tails and only skips the final incomplete line on read.

read_jsonl: Returns complete records only. Skips a malformed or non-object last line (killed writer's
partial append). Raises ValueError for any malformed or non-object line elsewhere (PRD §8: no silent
catches). Line numbers are 1-indexed in error messages.

append_jsonl: Truncates any partial tail (lines after the last complete newline), then appends.
This ensures resumability: if a kill leaves '{"a":1}\\n{"a":2' (partial), the next append truncates
to '{"a":1}\\n' and writes the new record, yielding '{"a":1}\\n{"a":3}\\n'. The partial line is
never committed, so discarding it loses nothing. Combined with read_jsonl's skip-last-line rule,
this keeps the pipeline resumable after kill -9.
"""
import json
import os
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    text = path.read_text()
    if text and not text.endswith("\n"):
        text = text[:text.rfind("\n") + 1]  # unterminated tail is partial even if it parses (append_jsonl drops it)
    lines = text.splitlines()
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

    # If the file has a partial tail (doesn't end with \n), truncate it
    if path.exists() and path.stat().st_size > 0:
        with path.open("r+b") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                # Find the position just after the last \n
                f.seek(0)
                content = f.read()
                last_newline_pos = content.rfind(b"\n")
                if last_newline_pos >= 0:
                    # Truncate after the last newline
                    f.truncate(last_newline_pos + 1)
                else:
                    # No newline found; truncate to empty
                    f.truncate(0)

    # Append the new record
    with path.open("a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
