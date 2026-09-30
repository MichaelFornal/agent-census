"""Content-addressed blob store and Parquet part tables (PRD §4 Storage)."""
import os
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import zstandard

from pipeline.redact import redact
from pipeline.schemas import SCHEMAS


def atomic_write(path: Path, data: bytes) -> None:
    """Write via a temp file and rename, so a kill leaves either the old file or the new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class BlobStore:
    """Blobs keyed by git blob SHA. Text is redacted before it reaches disk (CLAUDE.md privacy rule)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, oid: str) -> Path:
        return self.root / oid[:2] / f"{oid}.zst"

    def has(self, oid: str) -> bool:
        return self.path(oid).exists()

    def put(self, oid: str, text: str) -> dict[str, int]:
        clean, counts = redact(text)
        atomic_write(self.path(oid), zstandard.ZstdCompressor(level=10).compress(clean.encode()))
        return counts

    def get(self, oid: str) -> str:
        return zstandard.ZstdDecompressor().decompress(self.path(oid).read_bytes()).decode()


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
