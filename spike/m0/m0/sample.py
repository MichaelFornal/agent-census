"""Stratified 5k sample of harness files from code search. Throwaway code.

Run: uv run python -m m0.sample [--size 5000]
"""
import argparse
import json
import math
import random
import sys

from m0.gh import SearchClient, github_token
from m0.paths import data_path, write_metrics

# kind -> (code search query, PRD §2 seed count measured 2026-09-28)
COMPONENTS: dict[str, tuple[str, int]] = {
    "claude_md": ("filename:CLAUDE.md", 790_528),
    "skill": ("path:.claude/skills filename:SKILL.md", 433_152),
    "agent": ("path:.claude/agents extension:md", 238_080),
    "command": ("path:.claude/commands extension:md", 216_064),
    "hook": ("path:.claude/hooks", 87_552),
    "settings_local": ("path:.claude filename:settings.local.json", 85_504),
    "settings": ("path:.claude filename:settings.json", 65_408),
    "mcp": ("filename:.mcp.json", 64_896),
    "plugin": ("path:.claude-plugin filename:plugin.json", 25_408),
}
PER_WINDOW = 10


def quotas(total: int, counts: dict[str, int]) -> dict[str, int]:
    s = sum(counts.values())
    raw = {k: total * v / s for k, v in counts.items()}
    q = {k: int(r) for k, r in raw.items()}
    short = total - sum(q.values())
    for k in sorted(raw, key=lambda k: raw[k] - q[k], reverse=True)[:short]:
        q[k] += 1
    return q


def window(rng: random.Random) -> tuple[int, int]:
    lo = int(10 ** rng.uniform(1, 4.7))  # 10 B .. ~50 KB, log-uniform
    return lo, lo + max(20, lo // 2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=5000)
    args = ap.parse_args()

    rng = random.Random(0)
    client = SearchClient(github_token(), data_path("search_cache.jsonl"))
    q = quotas(args.size, {k: n for k, (_, n) in COMPONENTS.items()})
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    per_kind: dict[str, dict] = {}
    for kind, (query, _) in COMPONENTS.items():
        got = requests = 0
        max_requests = 3 * math.ceil(q[kind] / PER_WINDOW) + 10
        while got < q[kind] and requests < max_requests:
            lo, hi = window(rng)
            body = client.search(f"{query} size:{lo}..{hi}", per_page=100)
            requests += 1
            items = [it for it in body["items"] if (it["repo"], it["path"]) not in seen]
            for it in rng.sample(items, min(PER_WINDOW, len(items), q[kind] - got)):
                seen.add((it["repo"], it["path"]))
                out.append({"kind": kind, **it})
                got += 1
        per_kind[kind] = {"quota": q[kind], "got": got, "requests": requests}
        print(f"{kind}: {got}/{q[kind]} in {requests} requests {client.stats}", file=sys.stderr, flush=True)
    data_path("sample.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in out))
    write_metrics("sample", per_kind)


if __name__ == "__main__":
    main()
