"""S3 parse: one parser per harness kind, over redacted blobs only (PRD §4 S3)."""
import json

from pipeline.context import Ctx, Opts
from pipeline.journal import unit_key
from pipeline.kinds import PARSED_KINDS, artifact_id
from pipeline.parsers import parse
from pipeline.redact import REDACT_VERSION
from pipeline.runner import RunStats, Unit, run_batched

VERSION = 1
BATCH = 500


def units(ctx: Ctx) -> tuple[list[Unit], dict[str, list[str]]]:
    """The S3 units for the current harness_files and redaction version, and each repo's file paths."""
    files = ctx.tables.read("harness_files")
    paths_by_repo: dict[str, list[str]] = {}
    for f in files:
        paths_by_repo.setdefault(f["repo"], []).append(f["path"])
    todo = sorted((f for f in files if f["fetched"] and f["kind"] in PARSED_KINDS),
                  key=lambda f: (f["repo"], f["path"]))
    return ([Unit(unit_key("s3", VERSION, REDACT_VERSION, f["repo"], f["path"], f["blob_sha"]), f) for f in todo],
            paths_by_repo)


def run(ctx: Ctx, opts: Opts) -> RunStats:
    all_units, paths_by_repo = units(ctx)

    def work(batch: list[Unit]) -> dict[str, list[dict]]:
        rows = []
        for u in batch:
            f = u.payload
            parsed, err = parse(f["kind"], ctx.blobs.get(f["blob_sha"]), f["path"], paths_by_repo[f["repo"]])
            rows.append({"artifact_id": artifact_id(f["repo"], f["path"]), "repo": f["repo"], "kind": f["kind"],
                         "path": f["path"], "blob_sha": f["blob_sha"],
                         "parsed_json": json.dumps(parsed, sort_keys=True), "error_class": err})
        return {"artifacts": rows}

    return run_batched(ctx, "s3", all_units, work, BATCH, opts.limit)
