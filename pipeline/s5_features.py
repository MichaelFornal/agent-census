"""S5 tier-1 features: deterministic detectors and the glyph vector for every harness (PRD §4 S5)."""
import json
from dataclasses import asdict

from pipeline.context import Ctx, Opts
from pipeline.detectors import detect
from pipeline.detectors.base import Artifact, Harness
from pipeline.detectors.glyph import glyph
from pipeline.journal import unit_key
from pipeline.runner import RunStats, Unit, run_batched

VERSION = 1
BATCH = 200


def run(ctx: Ctx, opts: Opts) -> RunStats:
    by_repo: dict[str, list[dict]] = {}
    for a in ctx.tables.read("artifacts"):
        by_repo.setdefault(a["repo"], []).append(a)
    units = [Unit(unit_key("s5", VERSION, repo, sorted((a["artifact_id"], a["blob_sha"]) for a in arts)),
                  (repo, arts)) for repo, arts in sorted(by_repo.items())]

    def work(batch: list[Unit]) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {"features": [], "glyphs": []}
        for u in batch:
            repo, arts = u.payload
            h = Harness(repo, tuple(Artifact(a["artifact_id"], a["kind"], a["path"], json.loads(a["parsed_json"]),
                                             a["error_class"]) for a in sorted(arts, key=lambda a: a["path"])))
            out["features"] += [{"repo": repo, **asdict(e)} for e in detect(h)]
            out["glyphs"].append(glyph(h))
        return out

    return run_batched(ctx, "s5", units, work, BATCH, opts.limit)
