from m0.bias import population_share, sample_share


def test_sample_share():
    assert sample_share([100, 500, 999, 1000, 5000], 1000) == 0.8


def test_population_share_counts_leaves_and_overflows_fully_under_threshold():
    leaves = [(0, 500, 300), (501, 1000, 200), (1001, 4000, 400)]
    overflows = [(120, 100)]
    assert population_share(leaves, overflows, root_total=2000, threshold=1000) == (300 + 200 + 100) / 2000
