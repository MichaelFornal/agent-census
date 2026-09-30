import random
import re

import pytest
from helpers import FIXTURES

from pipeline import s1_discover as s1
from pipeline.context import Opts, make_ctx
from pipeline.fixtures import fixture_files
from pipeline.runner import read_state


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

    queries: list[str] = []

    def __init__(self, token, cache_path):
        self.stats = {"http_requests": 0}

    def search(self, q, page=1, per_page=100):
        self.stats["http_requests"] += 1
        self.queries.append(q)
        m = re.search(r"size:(\d+)\.\.(\d+)", q)
        sizes = [s for s in self.SIZES if int(m[1]) <= s <= int(m[2])]
        total = sum(self.SIZES[s] for s in sizes)
        if per_page == 1:
            return {"total_count": total, "incomplete_results": False, "items": []}
        fork = "fork:only" in q  # measured: fork:only (and fork:true) return forks only
        tag = re.sub(r"\W", "_", q.split()[0]) + ("-fork" if fork else "")
        items = [{"repo": f"o/{tag}-{s}-{i}", "fork": fork, "path": "CLAUDE.md", "sha": f"{s}x{i}"}
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
    assert set(per_seed) == set(s1.SEEDS.values())  # slice mode walks the nonfork families only
    assert len(per_seed) == 4
    assert all(len(v) in (300, 600, 800) for v in per_seed.values())
    assert all(h["component"] == "claude_md" for h in hits)
    assert s1.run(ctx, Opts(limit=40)).units_run == 0


def test_full_mode_enumerates_every_leaf(ctx, monkeypatch):
    monkeypatch.setattr(s1, "SearchClient", FakeSearch)
    monkeypatch.setattr(s1, "github_token", lambda: "t")
    s1.run(ctx, Opts())
    hits = ctx.tables.read("repo_hits")
    assert len({h["repo"] for h in hits if not h["is_fork"]}) == 4 * 1700
    assert len({h["repo"] for h in hits if h["is_fork"]}) == 4 * 1700
    assert all(h["is_fork"] == ("fork:only" in h["query_id"]) for h in hits)


def test_full_mode_walks_fork_families_and_slice_mode_does_not(ctx, monkeypatch):
    monkeypatch.setattr(s1, "SearchClient", FakeSearch)
    monkeypatch.setattr(s1, "github_token", lambda: "t")
    FakeSearch.queries = []
    s1.run(ctx, Opts(limit=4))
    assert FakeSearch.queries and not any("fork" in q for q in FakeSearch.queries)
    FakeSearch.queries = []
    s1.run(ctx, Opts())
    assert any("fork:only" in q for q in FakeSearch.queries)
    assert not any("fork:true" in q for q in FakeSearch.queries)


def test_fixture_mode_emits_hits_for_every_parsed_harness_file(fctx):
    s1.run(fctx, Opts())
    hits = fctx.tables.read("repo_hits")
    assert len(fixture_files(FIXTURES)) == 23
    assert len(hits) == 21  # every fixture file except the two skill_file resources
    assert {h["repo"] for h in hits if h["is_fork"]} == {"delta/skills-fork"}
    assert s1.run(fctx, Opts()).units_skipped == 1


def test_slice_mode_makes_no_count_requests_after_target(ctx, monkeypatch):
    pulled = []
    real_walk = s1.walk

    def spy_walk(*a, **kw):
        for n in real_walk(*a, **kw):
            pulled.append(n.query)
            yield n

    monkeypatch.setattr(s1, "walk", spy_walk)
    monkeypatch.setattr(s1, "SearchClient", FakeSearch)
    monkeypatch.setattr(s1, "github_token", lambda: "t")
    s1.run(ctx, Opts(limit=4))
    assert len(pulled) == 4  # one leaf per seed, no further node pulled
    pulled.clear()
    s1.run(ctx, Opts(limit=4))
    assert pulled == []


class PageSearch:
    def __init__(self, items_for_page):
        self.items_for_page, self.pages = items_for_page, []

    def search(self, q, page=1, per_page=100):
        assert per_page == 100
        self.pages.append(page)
        return {"total_count": 0, "incomplete_results": False, "items": self.items_for_page(page)}


def _items(n, offset=0):
    return [{"repo": f"o/r{offset + i}", "fork": False, "path": "CLAUDE.md", "sha": "s"} for i in range(n)]


def test_fetch_node_capped_requests_ten_pages():
    c = PageSearch(lambda p: _items(100, p * 100))
    rows = s1.fetch_node(c, "claude_md", s1.Node("q", 2500, "capped"))
    assert c.pages == list(range(1, 11))
    assert len(rows) == 1000


def test_fetch_node_stops_on_short_page():
    c = PageSearch(lambda p: _items(100, p * 100) if p == 1 else _items(40, 100))
    s1.fetch_node(c, "claude_md", s1.Node("q", 250, "leaf"))
    assert c.pages == [1, 2]


def test_fetch_node_150_issues_two_pages():
    c = PageSearch(lambda p: _items(100) if p == 1 else _items(50, 100))
    rows = s1.fetch_node(c, "claude_md", s1.Node("q", 150, "leaf"))
    assert c.pages == [1, 2]
    assert len(rows) == 150


def test_fetch_node_dedupes_repo_path_across_pages():
    c = PageSearch(lambda p: _items(100) if p == 1 else _items(50))
    rows = s1.fetch_node(c, "claude_md", s1.Node("q", 150, "leaf"))
    assert len(rows) == 100


def fake_github(monkeypatch):
    monkeypatch.setattr(s1, "SearchClient", FakeSearch)
    monkeypatch.setattr(s1, "github_token", lambda: "t")
    FakeSearch.queries = []


def hit_keys(ctx):
    return sorted((h["repo"], h["path"], h["query_id"]) for h in ctx.tables.read("repo_hits"))


def test_full_mode_records_each_family_and_completion(ctx, monkeypatch):
    fake_github(monkeypatch)
    s1.run(ctx, Opts())
    state = read_state(ctx, "s1")
    assert state["families_done"] == [f"{s}/{m}" for s in s1.SEEDS for m in s1.FORK_MODES]
    assert state["complete"] is True


def test_slice_mode_writes_no_state(ctx, monkeypatch):
    fake_github(monkeypatch)
    s1.run(ctx, Opts(limit=4))
    assert read_state(ctx, "s1") == {}


def test_rerun_of_a_complete_walk_makes_no_requests(ctx, monkeypatch):
    fake_github(monkeypatch)
    s1.run(ctx, Opts())
    FakeSearch.queries = []
    stats = s1.run(ctx, Opts())
    assert FakeSearch.queries == [] and stats.units_run == 0


def test_changed_walk_config_walks_again_without_duplicating(ctx, monkeypatch):
    fake_github(monkeypatch)
    s1.run(ctx, Opts())
    before = hit_keys(ctx)
    monkeypatch.setitem(s1.FLOOR_SPLITS, "mcp", ["path:/", "extension:json"])
    FakeSearch.queries = []
    stats = s1.run(ctx, Opts())
    assert FakeSearch.queries and stats.units_run == 0  # the lattice is walked again; every node is already journaled
    assert hit_keys(ctx) == before


class Crash(Exception):
    pass


def test_crash_mid_walk_resumes_without_loss_or_duplicates(ctx, monkeypatch):
    fake_github(monkeypatch)
    clean = make_ctx("clean")
    s1.run(clean, Opts())
    real, pages = FakeSearch.search, []

    def dying(self, q, page=1, per_page=100):
        if per_page == 100:
            pages.append(q)
            if len(pages) == 30:
                raise Crash()
        return real(self, q, page, per_page)

    monkeypatch.setattr(FakeSearch, "search", dying)
    with pytest.raises(Crash):
        s1.run(ctx, Opts())
    assert 0 < len(ctx.journal("s1").done_units()) and not read_state(ctx, "s1").get("complete")
    monkeypatch.setattr(FakeSearch, "search", real)
    s1.run(ctx, Opts())
    assert hit_keys(ctx) == hit_keys(clean) and read_state(ctx, "s1")["complete"]


def test_journal_is_read_once_per_run(ctx, monkeypatch):
    from pipeline.journal import Journal

    fake_github(monkeypatch)
    reads = []
    real = Journal.done_units
    monkeypatch.setattr(Journal, "done_units", lambda self: reads.append(1) or real(self))
    s1.run(ctx, Opts())
    assert len(reads) == 1


def test_full_mode_does_not_load_every_hit(ctx, monkeypatch):
    fake_github(monkeypatch)
    monkeypatch.setattr(s1, "_repos_by_seed", lambda ctx: pytest.fail("full mode must not read repo_hits"))
    s1.run(ctx, Opts())
