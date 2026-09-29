import re

from m0.lattice import walk


def histogram_count(hist: dict[int, int]):
    """Fake code search: files by byte size; answers 'seed size:a..b' queries."""
    def count(q: str) -> tuple[int, bool]:
        m = re.search(r"size:(\d+)\.\.(\d+)", q)
        if not m:
            return sum(hist.values()), False
        a, b = int(m[1]), int(m[2])
        return sum(n for s, n in hist.items() if a <= s <= b), False
    return count


def test_splits_once_when_root_window_is_over_cap():
    r = walk("filename:CLAUDE.md", histogram_count({0: 600, 3: 600}), lo=0, hi=3, cap=1000)
    assert r.root_total == 1200
    assert r.nodes == 4  # unconstrained root + 0..3 + 0..1 + 2..3
    assert r.leaves == [(0, 1, 600), (2, 3, 600)]
    assert r.fetch_requests == 12
    assert r.overflows == []


def test_every_leaf_is_under_cap_and_leaves_cover_the_root():
    r = walk("s", histogram_count({s: 1 for s in range(3000)}), lo=0, hi=4095, cap=1000)
    assert r.root_total == 3000
    assert r.leaf_sum == 3000
    assert all(t <= 1000 for _, _, t in r.leaves)
    assert r.nodes > len(r.leaves)


def test_single_size_over_cap_is_recorded_as_overflow_and_terminates():
    r = walk("s", histogram_count({100: 2500, 5: 10}), lo=0, hi=127, cap=1000)
    assert r.overflows == [(100, 2500)]
    d = r.to_dict()
    assert d["unreachable"] == 1500
    assert d["leaf_sum"] == 2510
    assert r.fetch_requests == 1 + 10


def test_incomplete_results_are_counted():
    r = walk("s", lambda q: (5, True), lo=0, hi=10, cap=1000)
    assert r.incomplete == r.nodes == 2
