"""S1 discover: adaptive size lattice over GitHub code search (PRD §4 S1).

A query over the cap is bisected on `size:`; at a single byte size it is split by the seed's floor
qualifiers, and whatever still exceeds the cap is recorded in s1_overflows. Every count and page
is cached in search_cache.jsonl, so a killed walk replays from the cache. In slice mode
(opts.limit = target repos) child ranges are visited in a seeded random order, so the first leaves
are spread across the size range instead of all being tiny files.

In full mode each (seed, fork mode) family that has been walked to the end is recorded in the stage state
file. A rerun skips finished families, and S2 reads the state to know when nested CLAUDE.md paths are final.

Fork semantics, measured live against GitHub code search: a plain query returns non-fork repos
only; `fork:true` returns ONLY forks (the same as `fork:only`: 976 vs 6,960 results, 100/100 forks
on the page); `fork:false` is rejected with a 422. So each seed is walked as two families: the
plain query (non-forks) and `<seed> fork:only` (forks). Slice mode walks the non-fork families
only; full mode walks both.
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
from pipeline.runner import RunStats, StageSession, Unit, merge_stats, read_state, run_batched, run_whole, write_state

VERSION = 2  # 2: query strings changed (fork:true replaced by non-fork and fork:only families)
MAX_SIZE = 393_216  # code search does not index files of 384 KB or more
CAP = 1000  # code search returns at most 1,000 results per query
FORK_MODES = {"nonfork": "", "fork": "fork:only"}  # fork:true returns forks only; fork:false is a 422
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
PROGRESS_EVERY = 25  # nodes between progress lines


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


def _base(q: str, mode: str) -> str:
    return f"{q} {FORK_MODES[mode]}" if FORK_MODES[mode] else q


def _repos_by_seed(ctx: Ctx) -> dict[tuple[str, str], set[str]]:
    """Repos found so far, per (seed, fork mode) family."""
    have: dict[tuple[str, str], set[str]] = {(s, m): set() for s in SEEDS for m in FORK_MODES}
    for h in ctx.tables.read("repo_hits"):
        for (s, m), repos in have.items():
            if h["query_id"].startswith(f"{_base(SEEDS[s], m)} size:"):
                repos.add(h["repo"])
    return have


def _run_fixtures(ctx: Ctx) -> RunStats:
    forks = {r["repo"]: r["is_fork"] for r in fixture_repos(ctx.fixtures)}
    rows = [{"repo": f.repo, "path": f.path, "component": classify(f.path), "query_id": "fixture",
             "blob_sha": git_blob_sha(f.data), "is_fork": forks[f.repo]}
            for f in fixture_files(ctx.fixtures) if classify(f.path) in PARSED_KINDS]
    return run_whole(ctx, "s1", unit_key(VERSION, rows), lambda: {"repo_hits": rows})


def _walk_config() -> str:
    """Fingerprint of everything that decides which nodes the walk visits."""
    return unit_key(VERSION, SEEDS, FLOOR_SPLITS, FORK_MODES, CAP, MAX_SIZE)


def run(ctx: Ctx, opts: Opts) -> RunStats:
    if ctx.fixtures:
        return _run_fixtures(ctx)
    client = SearchClient(github_token(), ctx.root / "search_cache.jsonl")

    incomplete = 0

    def count(q: str) -> int:
        nonlocal incomplete
        body = client.search(q, per_page=1)
        if body.get("incomplete_results"):
            incomplete += 1
        return body["total_count"]

    full = opts.limit is None
    session = StageSession(ctx, "s1")
    state = read_state(ctx, "s1") if full else {}
    if state.get("config") != _walk_config():
        state = {"config": _walk_config(), "families_done": [], "complete": False}
    have = {} if full else _repos_by_seed(ctx)  # slice mode only: the full table does not fit in memory
    target = None if full else math.ceil(opts.limit / len(SEEDS))
    total = RunStats("s1")
    for seed, mode in ((s, m) for s in SEEDS for m in (FORK_MODES if full else ["nonfork"])):
        name = f"{seed}/{mode}"
        if name in state["families_done"]:
            continue
        found = have.get((seed, mode), set())
        rng = random.Random(f"{VERSION}:{seed}:{mode}")
        nodes = walk(_base(SEEDS[seed], mode), count, rng, FLOOR_SPLITS[seed])
        walked = 0
        while target is None or len(found) < target:
            node = next(nodes, None)  # pulling a node issues count requests, so check the target first
            if node is None:
                break
            walked += 1
            key = unit_key("s1", VERSION, node.query, node.kind)
            if key in session.done:
                total.units_total += 1
                total.units_skipped += 1
            else:
                got: list[str] = []

                def work(batch: list[Unit], got: list[str] = got) -> dict[str, list[dict]]:
                    out = node_work(client, batch)
                    got.extend(r["repo"] for r in out["repo_hits"])
                    return out

                merge_stats(total, run_batched(ctx, "s1", [Unit(key, (seed, node))], work, batch_size=1,
                                               log=lambda m: None, session=session))
                found.update(got)
            if walked % PROGRESS_EVERY == 0:
                print(f"s1 {name}: {walked} nodes walked, {total.units_run} fetched this run; search {client.stats}",
                      flush=True)
        if full:  # the walk ran to its end, so every repo this family can reach is in repo_hits
            state["families_done"].append(name)
            write_state(ctx, "s1", state)
        print(f"s1 {name}: {walked} nodes; search {client.stats}; incomplete counts {incomplete}", flush=True)
    if full:
        state["complete"] = True
        write_state(ctx, "s1", state)
    return total
