"""census freeze (PRD §4 Storage): hash every table part and the blob index into the edition hash."""
import hashlib
import json
import time

from pipeline.context import Ctx
from pipeline.paths import manifest_path
from pipeline.redact import REDACT_VERSION
from pipeline.schemas import SCHEMAS
from pipeline.store import atomic_write


def fetched_blobs(ctx: Ctx) -> list[str]:
    rows = ctx.tables.connect().execute(
        "SELECT DISTINCT blob_sha FROM harness_files WHERE fetched ORDER BY blob_sha").fetchall()
    return [r[0] for r in rows]


def edition_hash(ctx: Ctx, with_redact_version: bool = True) -> tuple[str, dict]:
    """with_redact_version=False reproduces the hash of a manifest written before the field existed (M1)."""
    tables = {t: {p: hashlib.sha256((ctx.tables.dir(t) / f"{p}.parquet").read_bytes()).hexdigest()
                  for p in sorted(ctx.tables.parts(t))} for t in sorted(SCHEMAS)}
    blobs = fetched_blobs(ctx)
    body: dict = {"tables": tables, "blobs": blobs}
    if with_redact_version:
        body["redact_version"] = REDACT_VERSION  # the blob set names content; the version names what was removed
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    return digest, {"tables": tables, "blob_count": len(blobs)}


def stale_blobs(ctx: Ctx) -> int:
    return sum(1 for oid in fetched_blobs(ctx) if ctx.blobs.version(oid) != REDACT_VERSION)


def reredact(ctx: Ctx) -> tuple[int, int]:
    """Bring every blob of the edition to the current redaction version. Returns (rewritten, total)."""
    oids = fetched_blobs(ctx)
    return sum(ctx.blobs.ensure_current(oid) for oid in oids), len(oids)


UNIQUE = [  # (table, key columns, stage to reset)
    ("repos", ("repo",), "s2"), ("harness_files", ("repo", "path"), "s2"),
    ("artifacts", ("artifact_id",), "s3"), ("membership", ("artifact_id",), "s4"),
    ("clusters", ("cluster_id",), "s4"), ("features", ("repo", "technique_id", "artifact_id"), "s5"),
    ("glyphs", ("repo",), "s5"), ("uc_membership", ("cluster_id",), "s7"),
]
REFS = [  # (table, column, where, parent table, parent column, stage to reset)
    ("features", "artifact_id", "", "artifacts", "artifact_id", "s5"),
    ("membership", "artifact_id", "", "artifacts", "artifact_id", "s4"),
    ("semantics", "cluster_id", "pass_id = 'a'", "clusters", "cluster_id", "s6"),
    ("uc_membership", "cluster_id", "", "clusters", "cluster_id", "s7"),
]


def integrity_violations(ctx: Ctx) -> list[str]:
    """Stale rows from an upstream change without --reset double-count silently; name the stage to reset."""
    con = ctx.tables.connect()

    def count(sql: str) -> int:
        return con.execute(sql).fetchone()[0]

    def fix(stage: str) -> str:
        return f"run census run {stage} --reset, then rerun downstream stages"

    out = []
    for table, cols, stage in UNIQUE:
        if not count(f"SELECT count(*) FROM {table}"):
            continue
        key = ", ".join(cols)
        rows = count(f"SELECT count(*) FROM {table}")
        distinct = count(f"SELECT count(*) FROM (SELECT DISTINCT {key} FROM {table})")
        if rows != distinct:
            label = cols[0] if len(cols) == 1 else f"({key})"
            out.append(f"{table}.{label} not unique ({rows} rows, {distinct} distinct): {fix(stage)}")
    if count("SELECT count(*) FROM semantics") + count("SELECT count(*) FROM semantics_rejects"):
        both = "SELECT cluster_id, pass_id FROM semantics UNION ALL SELECT cluster_id, pass_id FROM semantics_rejects"
        rows = count(f"SELECT count(*) FROM ({both})")
        distinct = count(f"SELECT count(*) FROM (SELECT DISTINCT cluster_id, pass_id FROM ({both}))")
        if rows != distinct:
            out.append(f"semantics+semantics_rejects (cluster_id, pass_id) not unique ({rows} rows, {distinct} "
                       f"distinct): {fix('s6')}")
    for table, col, where, parent, pcol, stage in REFS:
        cond = f"WHERE {where}" if where else ""
        if not count(f"SELECT count(*) FROM {table} {cond}"):
            continue
        dangling = count(f"SELECT count(*) FROM (SELECT DISTINCT {col} FROM {table} {cond}) "
                         f"WHERE {col} NOT IN (SELECT {pcol} FROM {parent})")
        if dangling:
            out.append(f"{table}.{col} has {dangling} ids missing from {parent}.{pcol}: {fix(stage)}")
    return out


def freeze(ctx: Ctx) -> dict:
    bad = integrity_violations(ctx)
    if bad:
        raise SystemExit("freeze refused, stale rows:\n" + "\n".join(bad))
    stale = stale_blobs(ctx)
    if stale:
        raise SystemExit(f"freeze refused: {stale} blobs were redacted under rules older than v{REDACT_VERSION}, "
                         f"so parsed artifacts may hold text the current rules remove. Run "
                         f"`census reredact --edition {ctx.edition}`, then `census run s3 --reset` and every "
                         f"stage after it")
    digest, body = edition_hash(ctx)
    manifest = {"edition": ctx.edition, "edition_hash": digest, "frozen_at": time.strftime("%Y-%m-%d", time.gmtime()),
                "redact_version": REDACT_VERSION, **body}
    atomic_write(manifest_path(ctx.edition), (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
    return manifest


def load_manifest(edition: str) -> dict:
    p = manifest_path(edition)
    if not p.exists():
        raise SystemExit(f"edition {edition} is not frozen; run `census freeze --edition {edition}`")
    return json.loads(p.read_text())
