import pytest

from pipeline.runner import StopStage, Unit, run_batched, run_whole


def hits(batch):
    return {"repo_hits": [{"repo": u.payload, "path": "CLAUDE.md", "component": "claude_md", "query_id": "q",
                           "blob_sha": u.key, "is_fork": False} for u in batch]}


def units(n):
    return [Unit(f"k{i}", f"o/r{i}") for i in range(n)]


def quiet(msg):
    pass


def test_rerun_skips_done_units(ctx):
    calls = []

    def work(batch):
        calls.append(len(batch))
        return hits(batch)

    first = run_batched(ctx, "s1", units(5), work, batch_size=2, log=quiet)
    assert (first.units_run, calls) == (5, [2, 2, 1])
    second = run_batched(ctx, "s1", units(7), work, batch_size=2, log=quiet)
    assert (second.units_skipped, second.units_run) == (5, 2)
    assert len(ctx.tables.read("repo_hits")) == 7


def test_orphan_part_from_killed_run_is_removed(ctx):
    ctx.tables.write_part("repo_hits", "p-orphan", hits(units(3))["repo_hits"])
    run_batched(ctx, "s1", units(3), hits, batch_size=10, log=quiet)
    assert len(ctx.tables.read("repo_hits")) == 3
    assert "p-orphan" not in ctx.tables.parts("repo_hits")


def test_partial_journal_line_counts_as_not_done(ctx):
    run_batched(ctx, "s1", units(2), hits, batch_size=1, log=quiet)
    path = ctx.journal("s1").path
    lines = path.read_text().splitlines()
    path.write_text(lines[0] + "\n" + lines[1][:10])  # killed while writing the second line
    again = run_batched(ctx, "s1", units(2), hits, batch_size=1, log=quiet)
    assert again.units_run == 1
    assert sorted(r["repo"] for r in ctx.tables.read("repo_hits")) == ["o/r0", "o/r1"]


def test_stop_stage_leaves_batch_unjournaled(ctx):
    def work(batch):
        if batch[0].key == "k2":
            raise StopStage("limit")
        return hits(batch)

    stats = run_batched(ctx, "s1", units(4), work, batch_size=1, log=quiet)
    assert (stats.stopped, stats.units_run) == ("limit", 2)
    assert run_batched(ctx, "s1", units(4), hits, batch_size=1, log=quiet).units_run == 2


def test_limit_selects_a_stable_prefix_so_reruns_resume(ctx):
    assert run_batched(ctx, "s1", units(5), hits, batch_size=2, limit=3, log=quiet).units_run == 3
    assert run_batched(ctx, "s1", units(5), hits, batch_size=2, limit=3, log=quiet).units_run == 0
    assert run_batched(ctx, "s1", units(5), hits, batch_size=2, limit=5, log=quiet).units_run == 2


def test_duplicate_unit_keys_run_once(ctx):
    stats = run_batched(ctx, "s1", units(2) + units(2), hits, batch_size=10, log=quiet)
    assert stats.units_run == 2


def test_run_whole_recomputes_only_when_fingerprint_changes(ctx):
    n = []

    def work():
        n.append(1)
        return hits(units(2))

    run_whole(ctx, "s1", "fp1", work, log=quiet)
    run_whole(ctx, "s1", "fp1", work, log=quiet)
    assert len(n) == 1
    run_whole(ctx, "s1", "fp2", work, log=quiet)
    assert len(n) == 2
    assert len(ctx.tables.read("repo_hits")) == 2


def test_undeclared_table_is_an_error(ctx):
    with pytest.raises(ValueError, match="undeclared"):
        run_batched(ctx, "s1", units(1), lambda b: {"repos": []}, batch_size=1, log=quiet)
