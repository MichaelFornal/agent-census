"""S6 tier-2 extraction (PRD §4 S6, §5): one representative per distinct cluster, 20 per claude -p call,
3 calls in parallel (PRD §9.1). A call error splits the batch. An artifact whose own call still fails is
deferred and retried on later runs, and rejected as call_error only after runner.MAX_ATTEMPTS. A plan limit,
or a batch of fresh artifacts in which every call failed (an outage), stops the stage without journaling.
"""
import json
import random
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from pipeline.context import Ctx, Opts
from pipeline.journal import unit_key
from pipeline.llm.cache import make_llm
from pipeline.llm.client import LLMLimitReached
from pipeline.llm.extract import RECORDS_SCHEMA, SYSTEM_A, SYSTEM_B, build_prompt, score
from pipeline.runner import Partial, RunStats, StopStage, Unit, run_batched

VERSION = 1
TEXT_KINDS = ("claude_md", "skill", "agent", "command")
MAX_CHARS = 6000
BATCH = 20
WORKERS = 3
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


def extract_batch(llm, p: Pass, items: list[dict], texts: dict[str, str]) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """(rows, {cluster_id: error} for artifacts whose single-artifact call failed)."""
    ids = {f"a{i}": it for i, it in enumerate(items)}
    sources = {k: texts[it["blob_sha"]][:MAX_CHARS] for k, it in ids.items()}
    res = llm.call(p.model, p.system, build_prompt(list(sources.items()), secrets.token_hex(6)), RECORDS_SCHEMA)
    call = {"call_id": unit_key(p.pass_id, [it["cluster_id"] for it in items], time.time()), "pass_id": p.pass_id,
            "model": p.model, "n_sent": len(items), "n_ok": 0, "error": res.error, "wall_s": res.wall_s,
            "cost_usd": res.cost_usd}
    out: dict[str, list[dict]] = {"semantics": [], "semantics_rejects": [], "llm_calls": [call]}
    failed: dict[str, str] = {}
    if res.error:
        if len(items) > 1:
            mid = len(items) // 2
            for half in (items[:mid], items[mid:]):
                sub, sub_failed = extract_batch(llm, p, half, texts)
                for t in TABLES:
                    out[t] += sub[t]
                failed.update(sub_failed)
        else:
            failed[items[0]["cluster_id"]] = res.error
        return out, failed
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
    return out, failed


def run(ctx: Ctx, opts: Opts) -> RunStats:
    p = PASSES[opts.pass_id]
    reps = representatives(ctx)
    random.Random(p.order_seed).shuffle(reps)  # passes see artifacts in different orders (PRD §6.2)
    units = [Unit(unit_key("s6", VERSION, p.pass_id, p.model, p.system, r["cluster_id"], r["blob_sha"]), r) for r in reps]
    llm = make_llm(ctx.llm)
    tried_before = set(ctx.attempts("s6").counts())

    def work(batch: list[Unit]) -> Partial:
        texts = {u.payload["blob_sha"]: ctx.blobs.get(u.payload["blob_sha"]) for u in batch}
        chunks = [[u.payload for u in batch[i:i + BATCH]] for i in range(0, len(batch), BATCH)]
        try:
            with ThreadPoolExecutor(WORKERS) as pool:
                parts = list(pool.map(lambda c: extract_batch(llm, p, c, texts), chunks))
        except LLMLimitReached as e:
            raise StopStage(f"plan limit: {e}") from e
        out = {t: [row for rows, _ in parts for row in rows[t]] for t in TABLES}
        failed = {c: e for _, part_failed in parts for c, e in part_failed.items()}
        # An outage fails fresh artifacts wholesale. A batch made only of artifacts that failed on an
        # earlier run is content the model cannot process, and must be allowed to reach MAX_ATTEMPTS.
        fresh = any(u.key not in tried_before for u in batch)
        if fresh and out["llm_calls"] and all(c["error"] for c in out["llm_calls"]):
            raise StopStage(f"every call in the batch failed; last error: {out['llm_calls'][-1]['error']}")
        key_of = {u.payload["cluster_id"]: u.key for u in batch}
        return Partial(out, {key_of[c]: e for c, e in failed.items()})

    def give_up(u: Unit, error: str) -> dict[str, list[dict]]:
        return {"semantics_rejects": [{"cluster_id": u.payload["cluster_id"], "pass_id": p.pass_id,
                                       "reason": "call_error"}]}

    return run_batched(ctx, "s6", units, work, BATCH * WORKERS, opts.limit, give_up=give_up)
