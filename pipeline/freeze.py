"""census freeze (PRD §4 Storage): hash every table part and the blob index into the edition hash."""
import hashlib
import json
import time

from pipeline.context import Ctx
from pipeline.paths import manifest_path
from pipeline.schemas import SCHEMAS
from pipeline.store import atomic_write


def edition_hash(ctx: Ctx) -> tuple[str, dict]:
    tables = {t: {p: hashlib.sha256((ctx.tables.dir(t) / f"{p}.parquet").read_bytes()).hexdigest()
                  for p in sorted(ctx.tables.parts(t))} for t in sorted(SCHEMAS)}
    blobs = sorted({f["blob_sha"] for f in ctx.tables.read("harness_files") if f["fetched"]})
    digest = hashlib.sha256(json.dumps({"tables": tables, "blobs": blobs}, sort_keys=True).encode()).hexdigest()
    return digest, {"tables": tables, "blob_count": len(blobs)}


def freeze(ctx: Ctx) -> dict:
    digest, body = edition_hash(ctx)
    manifest = {"edition": ctx.edition, "edition_hash": digest, "frozen_at": time.strftime("%Y-%m-%d", time.gmtime()),
                **body}
    atomic_write(manifest_path(ctx.edition), (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
    return manifest


def load_manifest(edition: str) -> dict:
    p = manifest_path(edition)
    if not p.exists():
        raise SystemExit(f"edition {edition} is not frozen; run `census freeze --edition {edition}`")
    return json.loads(p.read_text())
