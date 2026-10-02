"""Content-addressed blob store and Parquet part tables (PRD §4 Storage)."""
import json
import os
import sqlite3
import threading
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
    with tmp.open("wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())  # the rename must not outlive the bytes in a power loss
    os.replace(tmp, path)


HEADER = "\x00census-blob "  # git treats a file with a NUL byte as binary, so no stored text starts like this


class BlobStore:
    """Blobs keyed by git blob SHA. Text is redacted before it reaches disk (CLAUDE.md privacy rule).

    Every blob is one row of blobs.sqlite (WAL, so a kill -9 loses at most an uncommitted put): zstd of one
    header line (redaction version and per-rule counts) followed by the redacted text. One file per blob cost
    2.1x its bytes in 4 KiB blocks; the packed store costs 1.2x. A blob written under older rules is
    re-redacted when it is next read: the current rules are applied to the stored text, so re-redaction can
    add redactions and never undo one.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._con: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    @property
    def db_path(self) -> Path:
        return self.root / "blobs.sqlite"

    def _db(self) -> sqlite3.Connection:
        if self._con is None:
            if legacy_files(self.root, limit=1):
                raise SystemExit(f"{self.root} still holds one file per blob; run `census pack-blobs` first")
            self._con = _open_db(self.db_path)
        return self._con

    def _put_bytes(self, oid: str, data: bytes) -> None:
        with self._lock:
            self._db().execute("INSERT OR REPLACE INTO blobs (oid, data) VALUES (?, ?)", (oid, data))

    def _get_bytes(self, oid: str) -> bytes | None:
        with self._lock:
            row = self._db().execute("SELECT data FROM blobs WHERE oid = ?", (oid,)).fetchone()
        return row[0] if row else None

    def has(self, oid: str) -> bool:
        with self._lock:
            return self._db().execute("SELECT 1 FROM blobs WHERE oid = ?", (oid,)).fetchone() is not None

    def _write(self, oid: str, text: str, counts: dict[str, int]) -> None:
        self._put_bytes(oid, encode_blob(REDACT_VERSION, counts, text))

    def _read(self, oid: str) -> tuple[int, dict[str, int], str]:
        data = self._get_bytes(oid)
        if data is None:
            raise KeyError(f"blob {oid} is not in the store")
        return decode_blob(data)

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


def _open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=300, check_same_thread=False, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")  # a put must outlive a power loss, as the fsynced files did
    con.execute("CREATE TABLE IF NOT EXISTS blobs (oid TEXT PRIMARY KEY, data BLOB NOT NULL)")
    return con


def encode_blob(version: int, counts: dict[str, int], text: str) -> bytes:
    head = HEADER + json.dumps({"v": version, "counts": counts}, sort_keys=True) + "\n"
    return zstandard.ZstdCompressor(level=10).compress((head + text).encode(errors="replace"))


def decode_blob(data: bytes, legacy_counts: dict[str, int] | None = None) -> tuple[int, dict[str, int], str]:
    """A blob with no header is M1's: version 1, its counts kept in a sidecar file."""
    raw = zstandard.ZstdDecompressor().decompress(data).decode()
    if raw.startswith(HEADER):
        head, _, text = raw.partition("\n")
        meta = json.loads(head[len(HEADER):])
        return meta["v"], meta["counts"], text
    return 1, legacy_counts or {}, raw


def legacy_files(root: Path, limit: int | None = None) -> list[Path]:
    """The M1/M2 layout: <root>/<oid[:2]>/<oid>.zst, with an M1 counts sidecar <oid>.json beside some."""
    out: list[Path] = []
    if not root.exists():
        return out
    for d in sorted(root.iterdir()):
        if d.is_dir() and len(d.name) == 2:
            for p in sorted(d.glob("*.zst")):
                out.append(p)
                if limit and len(out) >= limit:
                    return out
    return out


def pack_legacy(root: Path, batch: int = 1000) -> tuple[int, int]:
    """Move the one-file-per-blob layout into blobs.sqlite, keeping each blob's redaction version and counts
    (nothing is re-redacted here). Files are deleted only after their rows commit, so a kill at any point
    leaves every blob in the files or the database, and a rerun finishes the job. Returns (packed, already)."""
    files = legacy_files(root)
    con = _open_db(BlobStore(root).db_path)
    packed = already = 0
    for i in range(0, len(files), batch):
        chunk = files[i:i + batch]
        rows = []
        for p in chunk:
            oid = p.stem
            if con.execute("SELECT 1 FROM blobs WHERE oid = ?", (oid,)).fetchone():
                already += 1  # packed before a kill that came ahead of the file deletes
                continue
            side = p.with_suffix(".json")
            counts = json.loads(side.read_text()) if side.exists() else None
            version, counts, text = decode_blob(p.read_bytes(), counts)
            rows.append((oid, encode_blob(version, counts, text)))
        con.execute("BEGIN")
        con.executemany("INSERT INTO blobs (oid, data) VALUES (?, ?)", rows)
        con.execute("COMMIT")
        packed += len(rows)
        for p in chunk:
            p.with_suffix(".json").unlink(missing_ok=True)
            p.unlink()
    for d in root.iterdir():
        if d.is_dir() and len(d.name) == 2 and not any(d.iterdir()):
            d.rmdir()
    con.close()
    return packed, already


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
        pq.write_table(t, sink, compression="zstd")  # 43% smaller than snappy on harness_files
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
