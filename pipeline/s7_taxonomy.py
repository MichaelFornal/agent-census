"""S7 taxonomy (PRD §4 S7): a two-level use-case taxonomy with draft labels, Uncharted candidates
(noise plus clusters of three or fewer, ranked by nearest-neighbour distance), and technique candidates
(free-text technique clusters far from every catalog entry). Labels are drafts until Michael approves
them in editorial/<edition>/taxonomy_labels.json (`census labels`).
"""
import json
from collections import Counter

import numpy as np

from pipeline.cluster import nn_distance, two_level
from pipeline.context import Ctx, Opts
from pipeline.detectors.catalog import TECHNIQUES
from pipeline.embed import make_embedder
from pipeline.journal import unit_key
from pipeline.llm.cache import CachedLLM, make_llm
from pipeline.llm.client import LLMLimitReached
from pipeline.runner import RunStats, StopStage, run_whole

VERSION = 1
LABEL_MODEL = "sonnet"
LABEL_SAMPLE = 12
UNCHARTED_MAX_SIZE = 3
UNCHARTED_LIMIT = 50
CANDIDATE_SIM = 0.6  # M1 ruling; M4 calibrates
LABEL_SYSTEM = """You name clusters of Claude Code use cases. You get sentences that each say what Claude is
being made to do. Return a label of two to five words, with no digits, naming what they share, and whether
the use case is something other than software development."""
LABEL_SCHEMA = {"type": "object", "properties": {"label": {"type": "string"}, "non_coding": {"type": "boolean"}},
                "required": ["label", "non_coding"]}


def _label(llm, sentences: list[str]) -> tuple[str, bool] | None:
    """None when the call failed; the caller stops the stage so the failure is retried on rerun."""
    res = llm.call(LABEL_MODEL, LABEL_SYSTEM, "\n".join(f"- {s}" for s in sentences), LABEL_SCHEMA)
    if res.data is None:
        return None
    return str(res.data.get("label") or "Unlabelled"), bool(res.data.get("non_coding"))


def technique_candidates(emb, sem: list[dict]) -> list[dict]:
    names = [t["name"] for s in sem for t in json.loads(s["techniques_json"]) if t.get("name")]
    if not names:
        return []
    N = emb.encode(names)
    catalog = list(TECHNIQUES.values())
    C = emb.encode([f"{t.label}: {t.definition}" for t in catalog])
    labels, _ = two_level(N)
    rows = []
    for c in sorted(set(labels.tolist()) - {-1}):
        idx = np.where(labels == c)[0]
        centroid = N[idx].mean(axis=0)
        centroid /= np.linalg.norm(centroid) or 1.0
        sims = C @ centroid
        j = int(sims.argmax())
        common = Counter(names[i] for i in idx).most_common(5)
        rows.append({"candidate_id": "tc-" + unit_key(sorted(names[i] for i in idx))[:10], "label": common[0][0],
                     "size": len(idx), "nearest_technique": catalog[j].id, "similarity": float(sims[j]),
                     "is_candidate": bool(sims[j] < CANDIDATE_SIM), "examples_json": json.dumps([n for n, _ in common])})
    return rows


def taxonomy(ctx: Ctx, sem: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"use_cases": [], "uc_membership": [], "technique_candidates": [], "uncharted": []}
    if not sem:
        return out
    emb = make_embedder(ctx.embedder)
    llm = CachedLLM(make_llm(ctx.llm), ctx.root / "llm_cache.jsonl")
    X = emb.encode([s["use_case"] for s in sem])
    l1, l2 = two_level(X)
    groups: dict[tuple[int, int | None], list[int]] = {}
    for i in range(len(sem)):
        if l1[i] == -1:
            continue
        groups.setdefault((int(l1[i]), None), []).append(i)
        if l2[i] != -1:
            groups.setdefault((int(l1[i]), int(l2[i])), []).append(i)
    ids = {g: "uc-" + unit_key(sorted(sem[i]["cluster_id"] for i in idx))[:10] for g, idx in groups.items()}
    ordered = sorted(groups.items(), key=lambda kv: ids[kv[0]])
    labelled = [_label(llm, [sem[i]["use_case"] for i in idx[:LABEL_SAMPLE]]) for _, idx in ordered]
    failed = sum(1 for r in labelled if r is None)
    if failed:  # after all calls, so the successes are cached before the stage stops
        raise StopStage(f"{failed} of {len(labelled)} label calls failed; rerun to retry (successes are cached)")
    for (g, idx), (label, non_coding) in zip(ordered, labelled):
        out["use_cases"].append({"use_case_id": ids[g], "parent_id": ids[(g[0], None)] if g[1] is not None else None,
                                 "level": 1 if g[1] is None else 2, "label": label, "non_coding": non_coding,
                                 "size": len(idx)})
    for i, s in enumerate(sem):
        if l1[i] != -1:
            leaf = (int(l1[i]), int(l2[i]) if l2[i] != -1 else None)
            out["uc_membership"].append({"cluster_id": s["cluster_id"], "use_case_id": ids[leaf]})
    sizes = Counter(int(x) for x in l1 if x != -1)
    nn = nn_distance(X)
    candidates = [i for i in range(len(sem)) if l1[i] == -1 or sizes[int(l1[i])] <= UNCHARTED_MAX_SIZE]
    candidates.sort(key=lambda i: (-nn[i], sem[i]["cluster_id"]))
    out["uncharted"] = [{"cluster_id": sem[i]["cluster_id"], "use_case": sem[i]["use_case"],
                         "nn_distance": float(nn[i]), "rank": r + 1}
                        for r, i in enumerate(candidates[:UNCHARTED_LIMIT])]
    out["technique_candidates"] = technique_candidates(emb, sem)
    return out


def run(ctx: Ctx, opts: Opts) -> RunStats:
    sem = sorted((s for s in ctx.tables.read("semantics") if s["pass_id"] == "a"), key=lambda s: s["cluster_id"])
    fp = unit_key(VERSION, ctx.embedder, ctx.llm,
                  [(t.id, t.label, t.definition) for t in TECHNIQUES.values()],
                  (LABEL_MODEL, LABEL_SYSTEM, LABEL_SAMPLE, CANDIDATE_SIM, UNCHARTED_MAX_SIZE, UNCHARTED_LIMIT),
                  [(s["cluster_id"], s["use_case"], s["techniques_json"]) for s in sem])

    def work() -> dict[str, list[dict]]:
        try:
            return taxonomy(ctx, sem)
        except LLMLimitReached as e:
            raise StopStage(f"plan limit: {e}") from e

    return run_whole(ctx, "s7", fp, work)
