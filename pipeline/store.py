"""Content-addressed blob store and Parquet part tables (PRD §4 Storage)."""
import json
import os
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import zstandard

from pipeline.redact import REDACT_VERSION, redact
from pipeline.schemas import SCHEMAS


def atomic_write(path: Path, data: bytes) -> None:
    """Write via a temp file and rename, so a kill leaves either the old file or the new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


HEADER = "\x00census-blob "  # git treats a file with a NUL byte as binary, so no stored text starts like this


class BlobStore:
    """Blobs keyed by git blob SHA. Text is redacted before it reaches disk (CLAUDE.md privacy rule).

    A blob file is zstd of one header line (redaction version and per-rule counts) followed by the redacted
    text, written in one atomic rename. A blob written under older rules is re-redacted when it is next read:
    the current rules are applied to the stored text, so re-redaction can add redactions and never undo one.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, oid: str) -> Path:
        return self.root / oid[:2] / f"{oid}.zst"

    def legacy_counts_path(self, oid: str) -> Path:
        """M1 kept counts in a sidecar and wrote no version. Those blobs are version 1."""
        return self.root / oid[:2] / f"{oid}.json"

    def has(self, oid: str) -> bool:
        return self.path(oid).exists()

    def _write(self, oid: str, text: str, counts: dict[str, int]) -> None:
        head = HEADER + json.dumps({"v": REDACT_VERSION, "counts": counts}, sort_keys=True) + "\n"
        atomic_write(self.path(oid), zstandard.ZstdCompressor(level=10).compress((head + text).encode()))

    def _read(self, oid: str) -> tuple[int, dict[str, int], str]:
        raw = zstandard.ZstdDecompressor().decompress(self.path(oid).read_bytes()).decode()
        if raw.startswith(HEADER):
            head, _, text = raw.partition("\n")
            meta = json.loads(head[len(HEADER):])
            return meta["v"], meta["counts"], text
        side = self.legacy_counts_path(oid)
        return 1, (json.loads(side.read_text()) if side.exists() else {}), raw

    def _current(self, oid: str) -> tuple[dict[str, int], str]:
        version, counts, text = self._read(oid)
        if version == REDACT_VERSION:
            return counts, text
        if version > REDACT_VERSION:
            raise RuntimeError(f"blob {oid} was redacted under newer rules (v{version}) than this code "
                               f"(v{REDACT_VERSION}); update the code before reading it")
        clean, found = redact(text)
        merged = dict(counts)
        for rule, n in found.items():
            merged[rule] = merged.get(rule, 0) + n
        self._write(oid, clean, merged)
        self.legacy_counts_path(oid).unlink(missing_ok=True)
        return merged, clean

    def put(self, oid: str, text: str) -> dict[str, int]:
        clean, counts = redact(text)
        self._write(oid, clean, counts)
        return counts

    def get(self, oid: str) -> str:
        return self._current(oid)[1]

    def redaction_counts(self, oid: str) -> dict[str, int]:
        return self._current(oid)[0] if self.has(oid) else {}

    def version(self, oid: str) -> int:
        return self._read(oid)[0]

    def ensure_current(self, oid: str) -> bool:
        """Re-redact the blob if it was written under older rules. True if it was rewritten."""
        if self.version(oid) == REDACT_VERSION:
            return False
        self._current(oid)
        return True


class Tables:
    """Each table is a directory of Parquet parts; a part is written whole or not at all."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def dir(self, table: str) -> Path:
        return self.root / "tables" / table

    def parts(self, table: str) -> set[str]:
        d = self.dir(table)
        return {p.stem for p in d.glob("*.parquet")} if d.exists() else set()

    def write_part(self, table: str, part: str, rows: list[dict]) -> int:
        t = pa.Table.from_pylist(rows, schema=SCHEMAS[table])
        sink = pa.BufferOutputStream()
        pq.write_table(t, sink)
        atomic_write(self.dir(table) / f"{part}.parquet", sink.getvalue().to_pybytes())
        return t.num_rows

    def delete_part(self, table: str, part: str) -> None:
        (self.dir(table) / f"{part}.parquet").unlink(missing_ok=True)

    def clear(self, table: str) -> None:
        for p in self.parts(table):
            self.delete_part(table, p)

    def connect(self) -> duckdb.DuckDBPyConnection:
        con = duckdb.connect()
        for name, schema in SCHEMAS.items():
            if self.parts(name):
                con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{self.dir(name)}/*.parquet')")
            else:
                con.register(name, pa.Table.from_pylist([], schema=schema))
        return con

    def read(self, table: str) -> list[dict]:
        cur = self.connect().execute(f"SELECT * FROM {table}")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
