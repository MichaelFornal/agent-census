"""Adaptive size-bisection lattice over GitHub code search (PRD §4 S1). Throwaway code.

Run: uv run python -m m0.lattice [--seed claude_md ...] | [--query Q --name N]
"""
import argparse
import math
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from m0.gh import SearchClient, github_token
from m0.paths import data_path, write_metrics

MAX_SIZE = 393_216  # code search does not index files of 384 KB or more
CAP = 1000  # code search returns at most 1,000 results per query
PACE_S = 6.0  # 10 req/min
SEEDS = {
    "claude_md": "filename:CLAUDE.md",
    "claude_dir": "path:.claude",
    "mcp": "filename:.mcp.json",
    "plugin": "path:.claude-plugin",
}


@dataclass
class LatticeResult:
    seed: str
    cap: int = CAP
    root_total: int = 0
    nodes: int = 0
    leaves: list[tuple[int, int, int]] = field(default_factory=list)  # (lo, hi, total), total > 0
    overflows: list[tuple[int, int]] = field(default_factory=list)  # (size, total) with total > cap
    incomplete: int = 0

    @property
    def leaf_sum(self) -> int:
        return sum(t for _, _, t in self.leaves) + sum(t for _, t in self.overflows)

    @property
    def fetch_requests(self) -> int:
        return sum(math.ceil(t / 100) for _, _, t in self.leaves) + len(self.overflows) * (self.cap // 100)

    def to_dict(self) -> dict:
        return {
            "seed": self.seed,
            "root_total": self.root_total,
            "nodes": self.nodes,
            "n_leaves": len(self.leaves),
            "n_overflows": len(self.overflows),
            "leaf_sum": self.leaf_sum,
            "unreachable": sum(t - self.cap for _, t in self.overflows),
            "fetch_requests": self.fetch_requests,
            "incomplete": self.incomplete,
            "overflows": [list(o) for o in self.overflows],
        }


def walk(seed: str, count: Callable[[str], tuple[int, bool]], lo: int = 0, hi: int = MAX_SIZE,
         cap: int = CAP, progress: Callable[[LatticeResult], None] | None = None,
         res: LatticeResult | None = None) -> LatticeResult:
    res = res or LatticeResult(seed=seed, cap=cap)
    res.root_total, inc = count(seed)
    res.nodes += 1
    res.incomplete += int(inc)
    stack = [(lo, hi)]
    while stack:
        a, b = stack.pop()
        total, inc = count(f"{seed} size:{a}..{b}")
        res.nodes += 1
        res.incomplete += int(inc)
        if total <= cap:
            if total:
                res.leaves.append((a, b, total))
        elif a == b:
            res.overflows.append((a, total))
        else:
            mid = (a + b) // 2
            stack += [(mid + 1, b), (a, mid)]
        if progress and res.nodes % 25 == 0:
            progress(res)
    return res


def replay(seed: str, count: Callable[[str], tuple[int, bool]], lo: int = 0, hi: int = MAX_SIZE,
           cap: int = CAP) -> dict:
    """Re-walk from cached counts only; stop at the first uncached query (count raises KeyError).

    A partial walk is projected linearly by files covered. The walk covers small sizes first,
    where files are densest, so the projection is an upper bound.
    """
    res = LatticeResult(seed=seed, cap=cap)
    partial = False
    try:
        walk(seed, count, lo, hi, cap, res=res)
    except KeyError:
        partial = True
    d = res.to_dict()
    covered = res.leaf_sum
    scale = res.root_total / covered if partial and covered else 1
    d.update(partial=partial, covered=covered, projected_nodes=round(res.nodes * scale),
             projected_fetch_requests=round(res.fetch_requests * scale))
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="append", choices=sorted(SEEDS))
    ap.add_argument("--query", help="ad-hoc seed query (smoke tests)")
    ap.add_argument("--name", default="smoke", help="metrics name for --query")
    ap.add_argument("--replay", action="store_true", help="cache only; write lattice_<seed>_partial.json")
    args = ap.parse_args()

    client = SearchClient(github_token(), data_path("search_cache.jsonl"))
    if args.replay:
        def cached(q: str) -> tuple[int, bool]:
            body = client.cache[f"{q}|1|1"]
            return body["total_count"], body["incomplete_results"]
        for k in args.seed or SEEDS:
            d = replay(SEEDS[k], cached)
            if not d["covered"]:
                print(k, "not reached yet; no metrics written")
                continue
            d["projected_hours"] = (d["projected_nodes"] + d["projected_fetch_requests"]) * PACE_S / 3600
            write_metrics(f"lattice_{k}_partial", d)
            print(k, {x: d[x] for x in ("partial", "nodes", "covered", "root_total", "projected_nodes",
                                         "n_overflows", "unreachable", "projected_hours")})
        return

    def count(q: str) -> tuple[int, bool]:
        body = client.search(q, per_page=1)
        return body["total_count"], body["incomplete_results"]

    def progress(r: LatticeResult) -> None:
        print(f"  {r.seed}: {r.nodes} nodes, {len(r.leaves)} leaves, {len(r.overflows)} overflows, "
              f"{client.stats}", file=sys.stderr, flush=True)

    jobs = [(args.name, args.query)] if args.query else [(k, SEEDS[k]) for k in (args.seed or SEEDS)]
    for name, seed in jobs:
        t0 = time.monotonic()
        res = walk(seed, count, progress=progress)
        d = res.to_dict()
        d["elapsed_s"] = time.monotonic() - t0
        d["projected_hours"] = (res.nodes + res.fetch_requests) * PACE_S / 3600
        d["client_stats"] = dict(client.stats)
        write_metrics(f"lattice_{name}", d)
        print(f"{name}: root={d['root_total']} nodes={d['nodes']} leaves={d['n_leaves']} "
              f"overflows={d['n_overflows']} unreachable={d['unreachable']} "
              f"fetch={d['fetch_requests']} hours={d['projected_hours']:.1f}")


if __name__ == "__main__":
    main()
