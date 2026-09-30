"""S1 discover: adaptive size lattice over GitHub code search (PRD §4 S1).

A query over the cap is bisected on `size:`; at a single byte size it is split by the seed's floor
qualifiers, and whatever still exceeds the cap is recorded in s1_overflows. Every count and page
is cached in search_cache.jsonl, so a killed walk replays from the cache. In slice mode
(opts.limit = target repos) child ranges are visited in a seeded random order, so the first leaves
are spread across the size range instead of all being tiny files.
"""
import math
import random
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from pipeline.context import Ctx, Opts
from pipeline.fixtures import fixture_files, fixture_repos, git_blob_sha
from pipeline.gh import SearchClient, github_token
from pipeline.journal import unit_key
from pipeline.kinds import PARSED_KINDS, classify
from pipeline.runner import RunStats, Unit, merge_stats, run_batched, run_whole

VERSION = 1
MAX_SIZE = 393_216  # code search does not index files of 384 KB or more
CAP = 1000  # code search returns at most 1,000 results per query
FORK = "fork:true"  # code search leaves forks out by default (M0)
SEEDS = {
    "claude_md": "filename:CLAUDE.md",
    "claude_dir": "path:.claude",
    "mcp": "filename:.mcp.json",
    "plugin": "path:.claude-plugin",
}
FLOOR_SPLITS = {
    "claude_md": ["path:/"],
    "claude_dir": ["extension:md", "extension:json", "extension:sh", "extension:py", "extension:js",
                   "extension:ts"],
    "mcp": ["path:/"],
    "plugin": ["extension:json", "extension:md"],
}


@dataclass(frozen=True)
class Node:
    query: str
    total: int
    kind: str  # "leaf": complete; "capped": only the first 1,000 reachable; "overflow": coverage record
    reachable: int = 0


def walk(base: str, count: Callable[[str], int], rng: random.Random, floor_splits: list[str],
         cap: int = CAP, lo: int = 0, hi: int = MAX_SIZE) -> Iterator[Node]:
    stack = [(lo, hi)]
    while stack:
        a, b = stack.pop()
        q = f"{base} size:{a}..{b}"
        total = count(q)
        if total == 0:
            continue
        if total <= cap:
            yield Node(q, total, "leaf")
            continue
        if a < b:
            mid = (a + b) // 2
            kids = [(a, mid), (mid + 1, b)]
            rng.shuffle(kids)
            stack += kids
            continue
        children = [(f"{q} {extra}", t) for extra in floor_splits if (t := count(f"{q} {extra}")) > 0]
        for sq, t in children:
            yield Node(sq, t, "leaf" if t <= cap else "capped")
        if not children:
            yield Node(q, total, "capped")
        yield Node(q, total, "overflow", reachable=sum(min(t, cap) for _, t in children) or cap)


def fetch_node(client: SearchClient, seed: str, node: Node) -> list[dict]:
    rows, seen = [], set()
    for page in range(1, math.ceil(min(node.total, CAP) / 100) + 1):
        body = client.search(node.query, page=page, per_page=100)
        for it in body["items"]:
            if (it["repo"], it["path"]) in seen:
                continue
            seen.add((it["repo"], it["path"]))
            rows.append({"repo": it["repo"], "path": it["path"], "component": classify(it["path"]) or seed,
                         "query_id": node.query, "blob_sha": it["sha"], "is_fork": it["fork"]})
        if len(body["items"]) < 100:
            break
    return rows


def node_work(client: SearchClient, batch: list[Unit]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"repo_hits": [], "s1_overflows": []}
    for u in batch:
        seed, node = u.payload
        if node.kind == "overflow":
            out["s1_overflows"].append({"seed": seed, "query": node.query, "total": node.total,
                                        "reachable": node.reachable})
        else:
            out["repo_hits"] += fetch_node(client, seed, node)
    return out


def _repos_by_seed(ctx: Ctx) -> dict[str, set[str]]:
    have: dict[str, set[str]] = {s: set() for s in SEEDS}
    for h in ctx.tables.read("repo_hits"):
        for s, q in SEEDS.items():
            if h["query_id"].startswith(f"{q} {FORK} "):
                have[s].add(h["repo"])
    return have


def _run_fixtures(ctx: Ctx) -> RunStats:
    forks = {r["repo"]: r["is_fork"] for r in fixture_repos(ctx.fixtures)}
    rows = [{"repo": f.repo, "path": f.path, "component": classify(f.path), "query_id": "fixture",
             "blob_sha": git_blob_sha(f.data), "is_fork": forks[f.repo]}
            for f in fixture_files(ctx.fixtures) if classify(f.path) in PARSED_KINDS]
    return run_whole(ctx, "s1", unit_key(VERSION, rows), lambda: {"repo_hits": rows})


def run(ctx: Ctx, opts: Opts) -> RunStats:
    if ctx.fixtures:
        return _run_fixtures(ctx)
    client = SearchClient(github_token(), ctx.root / "search_cache.jsonl")

    def count(q: str) -> int:
        return client.search(q, per_page=1)["total_count"]

    have = _repos_by_seed(ctx)
    target = math.ceil(opts.limit / len(SEEDS)) if opts.limit else None
    total = RunStats("s1")
    for seed, q in SEEDS.items():
        rng = random.Random(f"{VERSION}:{seed}")
        for node in walk(f"{q} {FORK}", count, rng, FLOOR_SPLITS[seed]):
            if target is not None and len(have[seed]) >= target:
                break
            got: list[str] = []

            def work(batch: list[Unit]) -> dict[str, list[dict]]:
                out = node_work(client, batch)
                got.extend(r["repo"] for r in out["repo_hits"])
                return out

            unit = Unit(unit_key("s1", VERSION, node.query, node.kind), (seed, node))
            merge_stats(total, run_batched(ctx, "s1", [unit], work, batch_size=1, log=lambda m: None))
            have[seed].update(got)
        print(f"s1 {seed}: {len(have[seed])} repos; search {client.stats}", flush=True)
    return total
