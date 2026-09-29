import random

from m0.sample import COMPONENTS, quotas, window


def test_quotas_sum_to_total_and_follow_proportions():
    q = quotas(5000, {k: n for k, (_, n) in COMPONENTS.items()})
    assert sum(q.values()) == 5000
    assert q["claude_md"] > q["skill"] > q["plugin"] > 0


def test_quotas_largest_remainder():
    assert quotas(10, {"a": 1, "b": 1, "c": 1}) == {"a": 4, "b": 3, "c": 3}


def test_window_is_a_valid_size_range():
    rng = random.Random(0)
    for _ in range(1000):
        lo, hi = window(rng)
        assert 10 <= lo < hi <= 80_000
