import subprocess
import sys
import time

import pytest

from pipeline.context import make_ctx
from pipeline.runner import (MAX_ATTEMPTS, Partial, StageSession, StopStage, Unit, read_state, reset, run_batched,
                             run_whole, write_state)


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


def test_crash_between_parts_and_journal_line_is_recovered(ctx, monkeypatch):
    from pipeline.journal import Journal

    real = Journal.record
    calls = []

    def flaky(self, part, units, rows):
        calls.append(part)
        if len(calls) == 2:
            raise RuntimeError("killed before the journal line")
        real(self, part, units, rows)

    monkeypatch.setattr(Journal, "record", flaky)
    with pytest.raises(RuntimeError):
        run_batched(ctx, "s1", units(4), hits, batch_size=2, log=quiet)
    orphan = calls[1]
    assert orphan in ctx.tables.parts("repo_hits")
    monkeypatch.setattr(Journal, "record", real)
    stats = run_batched(ctx, "s1", units(4), hits, batch_size=2, log=quiet)
    assert stats.units_run == 2
    assert sorted(r["repo"] for r in ctx.tables.read("repo_hits")) == [f"o/r{i}" for i in range(4)]
    assert len(ctx.tables.parts("repo_hits")) == 2


def test_kill_inside_reset_is_recovered(ctx):
    n = []

    def work():
        n.append(1)
        return hits(units(2))

    run_whole(ctx, "s1", "fp1", work, log=quiet)
    ctx.journal("s1").clear()  # killed mid-reset: journal gone, parts left behind
    run_whole(ctx, "s1", "fp1", work, log=quiet)
    assert len(n) == 2
    assert len(ctx.tables.read("repo_hits")) == 2


def flaky(fail_keys):
    def work(batch):
        bad = {u.key: "boom" for u in batch if u.key in fail_keys}
        return Partial(hits([u for u in batch if u.key not in bad]), bad)
    return work


def lost(u, err):
    return {"s1_overflows": [{"seed": err, "query": u.key, "total": 0, "reachable": 0}]}


def test_deferred_unit_is_not_journaled_and_runs_again(ctx):
    stats = run_batched(ctx, "s1", units(4), flaky({"k1"}), batch_size=2, log=quiet, give_up=lost)
    assert (stats.units_run, stats.units_deferred) == (3, 1)
    assert stats.stopped == "1 units deferred after transient failures; rerun to retry"
    assert "k1" not in ctx.journal("s1").done_units()
    assert ctx.attempts("s1").counts() == {"k1": 1}
    again = run_batched(ctx, "s1", units(4), hits, batch_size=2, log=quiet, give_up=lost)
    assert (again.units_run, again.units_skipped, again.stopped) == (1, 3, None)
    assert sorted(r["repo"] for r in ctx.tables.read("repo_hits")) == [f"o/r{i}" for i in range(4)]


def test_failed_unit_waits_out_the_retry_gap(ctx, monkeypatch):
    monkeypatch.setenv("CENSUS_RETRY_GAP_S", "3600")
    t0 = time.time()
    first = run_batched(ctx, "s1", units(2), flaky({"k1"}), batch_size=2, log=quiet, give_up=lost, now=lambda: t0)
    assert (first.units_run, first.units_deferred) == (1, 1)
    seen = []

    def work(batch):
        seen.append([u.key for u in batch])
        return hits(batch)

    soon = run_batched(ctx, "s1", units(2), work, batch_size=2, log=quiet, give_up=lost, now=lambda: t0 + 60)
    assert seen == [] and (soon.units_run, soon.units_deferred) == (0, 1)
    assert soon.stopped == "1 units deferred after transient failures; rerun to retry"
    assert ctx.attempts("s1").counts() == {"k1": 1}  # a deferred unit is not an attempt
    later = run_batched(ctx, "s1", units(2), work, batch_size=2, log=quiet, give_up=lost, now=lambda: t0 + 3601)
    assert seen == [["k1"]] and (later.units_run, later.units_deferred, later.stopped) == (1, 0, None)


def test_attempts_last_reports_the_newest_attempt_time(tmp_path):
    from pipeline.journal import Attempts

    path = tmp_path / "a.jsonl"
    path.write_text('{"unit": "a", "error": "x", "at": "2026-01-01T00:00:00Z"}\n'
                    '{"unit": "a", "error": "x", "at": "2026-01-01T01:00:00Z"}\n'
                    '{"unit": "b", "error": "x", "at": "2026-01-01T00:30:00Z"}\n')
    assert Attempts(path).last() == {"a": 1767229200.0, "b": 1767227400.0}


def test_unit_is_given_up_after_max_attempts(ctx):
    for n in range(1, MAX_ATTEMPTS):
        stats = run_batched(ctx, "s1", units(2), flaky({"k1"}), batch_size=2, log=quiet, give_up=lost)
        assert stats.units_deferred == 1 and ctx.attempts("s1").counts() == {"k1": n}
    stats = run_batched(ctx, "s1", units(2), flaky({"k1"}), batch_size=2, log=quiet, give_up=lost)
    assert (stats.units_gave_up, stats.units_deferred, stats.stopped) == (1, 0, None)
    assert [(o["seed"], o["query"]) for o in ctx.tables.read("s1_overflows")] == [("boom", "k1")]
    assert run_batched(ctx, "s1", units(2), flaky({"k1"}), batch_size=2, log=quiet, give_up=lost).units_run == 0


def test_batch_with_every_unit_deferred_commits_nothing(ctx):
    stats = run_batched(ctx, "s1", units(2), flaky({"k0", "k1"}), batch_size=2, log=quiet, give_up=lost)
    assert (stats.units_run, stats.units_deferred) == (0, 2)
    assert ctx.journal("s1").entries() == [] and ctx.tables.parts("repo_hits") == set()


def test_deferring_without_give_up_is_an_error(ctx):
    with pytest.raises(ValueError, match="give_up"):
        run_batched(ctx, "s1", units(1), flaky({"k0"}), batch_size=1, log=quiet)


def test_deferring_a_unit_outside_the_batch_is_an_error(ctx):
    with pytest.raises(ValueError, match="not in the batch"):
        run_batched(ctx, "s1", units(1), lambda b: Partial({}, {"nope": "x"}), batch_size=1, log=quiet, give_up=lost)


def test_stop_raised_by_give_up_stops_the_stage(ctx):
    def refuse(u, err):
        raise StopStage("outage")

    for _ in range(MAX_ATTEMPTS - 1):
        run_batched(ctx, "s1", units(1), flaky({"k0"}), batch_size=1, log=quiet, give_up=refuse)
    stats = run_batched(ctx, "s1", units(1), flaky({"k0"}), batch_size=1, log=quiet, give_up=refuse)
    assert stats.stopped == "outage" and ctx.journal("s1").entries() == []


def test_session_reads_the_journal_once_for_many_batches(ctx, monkeypatch):
    from pipeline.journal import Journal

    reads = []
    real = Journal.done_units
    monkeypatch.setattr(Journal, "done_units", lambda self: reads.append(1) or real(self))
    session = StageSession(ctx, "s1")
    for u in units(5):
        run_batched(ctx, "s1", [u], hits, batch_size=1, log=quiet, session=session)
    assert len(reads) == 1 and session.done == {f"k{i}" for i in range(5)}
    assert run_batched(ctx, "s1", units(5), hits, batch_size=1, log=quiet, session=session).units_run == 0


HOLDER = """
import time
from pipeline.context import make_ctx
from pipeline.runner import StageSession
StageSession(make_ctx("lock"), "s1")
print("held", flush=True)
time.sleep(60)
"""


def test_second_process_cannot_run_the_same_stage(isolated_data):
    proc = subprocess.Popen([sys.executable, "-c", HOLDER], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "held"
        with pytest.raises(SystemExit, match="another process"):
            StageSession(make_ctx("lock"), "s1")
    finally:
        proc.kill()
        proc.wait()
    StageSession(make_ctx("lock"), "s1")  # the kernel dropped the lock when the holder died


def test_reset_clears_attempts_and_state(ctx):
    run_batched(ctx, "s1", units(1), flaky({"k0"}), batch_size=1, log=quiet, give_up=lost)
    write_state(ctx, "s1", {"complete": True})
    assert read_state(ctx, "s1") == {"complete": True}
    reset(ctx, "s1")
    assert ctx.attempts("s1").counts() == {} and read_state(ctx, "s1") == {}
