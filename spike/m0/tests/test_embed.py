from m0.embed import merge_results


def test_merge_replaces_rows_for_rerun_models_and_keeps_others():
    old = [{"model": "a", "texts_per_s": 1.0}, {"model": "b", "texts_per_s": 2.0}]
    new = [{"model": "b", "texts_per_s": 3.0}]
    assert merge_results(old, new, {"b"}) == [{"model": "a", "texts_per_s": 1.0}, {"model": "b", "texts_per_s": 3.0}]
