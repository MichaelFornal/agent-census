"""S6 tier-2 extraction (PRD §4 S6, §5): one representative per distinct cluster, 20 per claude -p call,
3 calls in parallel (PRD §9.1). A call error splits the batch; a plan limit stops the stage cleanly.
"""
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from pipeline.context import Ctx, Opts
from pipeline.journal import unit_key
from pipeline.llm.cache import make_llm
from pipeline.llm.client import LLMLimitReached
from pipeline.llm.extract import RECORDS_SCHEMA, SYSTEM_A, SYSTEM_B, build_prompt, score
from pipeline.runner import RunStats, StopStage, Unit, run_batched

VERSION = 1
TEXT_KINDS = ("claude_md", "skill", "agent", "command")
MAX_CHARS = 6000
BATCH = 20
WORKERS = 3
MAX_CONSECUTIVE_FAILED_BATCHES = 5
TABLES = ("semantics", "semantics_rejects", "llm_calls")


@dataclass(frozen=True)
class Pass:
    pass_id: str
    model: str
    system: str
    order_seed: int


PASSES = {"a": Pass("a", "sonnet", SYSTEM_A, 0), "b": Pass("b", "haiku", SYSTEM_B, 1)}


def representatives(ctx: Ctx) -> list[dict]:
    arts = {a["artifact_id"]: a for a in ctx.tables.read("artifacts")}
    reps = [{"cluster_id": c["cluster_id"], "artifact_id": c["canonical_artifact"],
             "blob_sha": arts[c["canonical_artifact"]]["blob_sha"]}
            for c in ctx.tables.read("clusters") if c["kind"] in TEXT_KINDS]
    return sorted(reps, key=lambda r: r["cluster_id"])


def extract_batch(llm, p: Pass, items: list[dict], texts: dict[str, str]) -> dict[str, list[dict]]:
    ids = {f"a{i}": it for i, it in enumerate(items)}
    sources = {k: texts[it["blob_sha"]][:MAX_CHARS] for k, it in ids.items()}
    res = llm.call(p.model, p.system, build_prompt(list(sources.items())), RECORDS_SCHEMA)
    call = {"call_id": unit_key(p.pass_id, [it["cluster_id"] for it in items], time.time()), "pass_id": p.pass_id,
            "model": p.model, "n_sent": len(items), "n_ok": 0, "error": res.error, "wall_s": res.wall_s,
            "cost_usd": res.cost_usd}
    out: dict[str, list[dict]] = {"semantics": [], "semantics_rejects": [], "llm_calls": [call]}
    if res.error:
        if len(items) > 1:
            mid = len(items) // 2
            for half in (items[:mid], items[mid:]):
                sub = extract_batch(llm, p, half, texts)
                for t in TABLES:
                    out[t] += sub[t]
        else:
            out["semantics_rejects"].append({"cluster_id": items[0]["cluster_id"], "pass_id": p.pass_id,
                                             "reason": "call_error"})
        return out
    ok, rejects = score(sources, res.data.get("records") or [])
    for r in ok:
        it = ids[r["id"]]
        out["semantics"].append({"cluster_id": it["cluster_id"], "pass_id": p.pass_id, "artifact_id": it["artifact_id"],
                                 "use_case": r["use_case"], "domain_guess": r["domain_guess"],
                                 "non_coding": r["non_coding"],
                                 "techniques_json": json.dumps(r["techniques_described"], sort_keys=True),
                                 "notable": r["notable"]})
    for rid, reason in rejects.items():
        out["semantics_rejects"].append({"cluster_id": ids[rid]["cluster_id"], "pass_id": p.pass_id, "reason": reason})
    call["n_ok"] = len(ok)
    return out


def run(ctx: Ctx, opts: Opts) -> RunStats:
    p = PASSES[opts.pass_id]
    reps = representatives(ctx)
    random.Random(p.order_seed).shuffle(reps)  # passes see artifacts in different orders (PRD §6.2)
    units = [Unit(unit_key("s6", VERSION, p.pass_id, p.system, r["cluster_id"], r["blob_sha"]), r) for r in reps]
    llm = make_llm(ctx.llm)
    failed = 0

    def work(batch: list[Unit]) -> dict[str, list[dict]]:
        nonlocal failed
        texts = {u.payload["blob_sha"]: ctx.blobs.get(u.payload["blob_sha"]) for u in batch}
        chunks = [[u.payload for u in batch[i:i + BATCH]] for i in range(0, len(batch), BATCH)]
        try:
            with ThreadPoolExecutor(WORKERS) as pool:
                parts = list(pool.map(lambda c: extract_batch(llm, p, c, texts), chunks))
        except LLMLimitReached as e:
            raise StopStage(f"plan limit: {e}") from e
        out = {t: [row for part in parts for row in part[t]] for t in TABLES}
        failed = failed + 1 if all(c["error"] for c in out["llm_calls"]) else 0
        if failed >= MAX_CONSECUTIVE_FAILED_BATCHES:
            raise StopStage(f"{failed} consecutive batches failed; last error: {out['llm_calls'][-1]['error']}")
        return out

    return run_batched(ctx, "s6", units, work, BATCH * WORKERS, opts.limit)
