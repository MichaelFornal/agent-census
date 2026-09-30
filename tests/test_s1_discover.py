import random
import re

from helpers import FIXTURES

from pipeline import s1_discover as s1
from pipeline.context import Opts
from pipeline.fixtures import fixture_files


def fake_count(sizes: dict[int, int]):
    def count(q: str) -> int:
        m = re.search(r"size:(\d+)\.\.(\d+)", q)
        n = sum(v for s, v in sizes.items() if int(m[1]) <= s <= int(m[2]))
        return n // 2 if "path:/" in q else n
    return count


def test_walk_bisects_until_leaves_fit_cap():
    nodes = list(s1.walk("filename:CLAUDE.md", fake_count({100: 600, 5000: 700}), random.Random(0), [],
                         cap=1000, hi=8191))
    assert all(n.kind == "leaf" and n.total <= 1000 for n in nodes)
    assert sum(n.total for n in nodes) == 1300


def test_single_size_over_cap_splits_then_records_overflow():
    nodes = list(s1.walk("filename:CLAUDE.md", fake_count({42: 3000}), random.Random(0), ["path:/"],
                         cap=1000, hi=63))
    assert [(n.kind, n.total) for n in nodes] == [("capped", 1500), ("overflow", 3000)]
    assert nodes[0].query.endswith("size:42..42 path:/")
    assert nodes[1].reachable == 1000


def test_floor_without_splits_is_capped_and_recorded():
    nodes = list(s1.walk("q", fake_count({7: 2500}), random.Random(0), [], cap=1000, hi=15))
    assert [(n.kind, n.total, n.reachable) for n in nodes] == [("capped", 2500, 0), ("overflow", 2500, 1000)]


def test_walk_order_is_seeded():
    sizes = {10: 900, 200: 900, 3000: 900, 40000: 900}
    order = lambda seed: [n.query for n in s1.walk("q", fake_count(sizes), random.Random(seed), [], hi=65535)]
    assert order(1) == order(1)
    assert sorted(order(1)) == sorted(order(2))


class FakeSearch:
    SIZES = {10: 600, 2000: 800, 90000: 300}

    def __init__(self, token, cache_path):
        self.stats = {"http_requests": 0}

    def search(self, q, page=1, per_page=100):
        self.stats["http_requests"] += 1
        m = re.search(r"size:(\d+)\.\.(\d+)", q)
        sizes = [s for s in self.SIZES if int(m[1]) <= s <= int(m[2])]
        total = sum(self.SIZES[s] for s in sizes)
        if per_page == 1:
            return {"total_count": total, "incomplete_results": False, "items": []}
        tag = re.sub(r"\W", "_", q.split()[0])
        items = [{"repo": f"o/{tag}-{s}-{i}", "fork": False, "path": "CLAUDE.md", "sha": f"{s}x{i}"}
                 for s in sizes for i in range(self.SIZES[s])]
        return {"total_count": total, "incomplete_results": False, "items": items[(page - 1) * 100: page * 100]}


def test_slice_mode_stops_at_one_leaf_per_seed_and_resumes(ctx, monkeypatch):
    monkeypatch.setattr(s1, "SearchClient", FakeSearch)
    monkeypatch.setattr(s1, "github_token", lambda: "t")
    stats = s1.run(ctx, Opts(limit=40))
    assert stats.units_run == 4
    hits = ctx.tables.read("repo_hits")
    per_seed = {}
    for h in hits:
        per_seed.setdefault(h["query_id"].split(" size:")[0], set()).add(h["repo"])
    assert len(per_seed) == 4
    assert all(len(v) in (300, 600, 800) for v in per_seed.values())
    assert all(h["component"] == "claude_md" for h in hits)
    assert s1.run(ctx, Opts(limit=40)).units_run == 0


def test_full_mode_enumerates_every_leaf(ctx, monkeypatch):
    monkeypatch.setattr(s1, "SearchClient", FakeSearch)
    monkeypatch.setattr(s1, "github_token", lambda: "t")
    s1.run(ctx, Opts())
    assert len({h["repo"] for h in ctx.tables.read("repo_hits")}) == 4 * 1700


def test_fixture_mode_emits_hits_for_every_parsed_harness_file(fctx):
    s1.run(fctx, Opts())
    hits = fctx.tables.read("repo_hits")
    assert len(fixture_files(FIXTURES)) == 23
    assert len(hits) == 21  # every fixture file except the two skill_file resources
    assert {h["repo"] for h in hits if h["is_fork"]} == {"delta/skills-fork"}
    assert s1.run(fctx, Opts()).units_skipped == 1
