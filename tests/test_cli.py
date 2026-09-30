import json
from pathlib import Path

import pytest

from pipeline import cli
from pipeline.runner import RunStats, Unit, run_batched


def test_status_lists_journaled_stages(ctx, capsys):
    run_batched(ctx, "s1", [Unit("k", "o/r")], lambda b: {"repo_hits": [
        {"repo": "o/r", "path": "CLAUDE.md", "component": "claude_md", "query_id": "q", "blob_sha": "s",
         "is_fork": False}]}, batch_size=1, log=lambda m: None)
    assert cli.main(["status", "--edition", "test"]) == 0
    out = capsys.readouterr().out
    assert "s1: units=1 parts=1 repo_hits=1 s1_overflows=0" in out
    assert "s2: not started" in out


def test_status_reports_a_stuck_or_finished_supervisor(ctx, capsys):
    logs = ctx.root / "logs"
    logs.mkdir(parents=True)
    (logs / "supervise-s2.state.json").write_text(json.dumps(
        {"restarts": 7, "no_progress": 3, "last_exit": 1, "last_at": "2026-10-02T03:04:05Z", "backoff_s": 480.0}))
    (logs / "supervise-s1.state.json").write_text(json.dumps(
        {"complete": True, "restarts": 2, "no_progress": 0, "last_exit": 0, "last_at": "2026-10-01T00:00:00Z",
         "backoff_s": 0.0}))
    cli.main(["status", "--edition", "test"])
    out = capsys.readouterr().out
    assert "s2 supervisor: 7 restarts, 3 in a row without progress, last exit 1 at 2026-10-02T03:04:05Z" in out
    assert "s1 supervisor: complete" in out
    assert "s3 supervisor" not in out


def test_fixtures_default_to_offline_models(monkeypatch):
    seen = {}

    def fake_run(ctx, opts):
        seen.update(llm=ctx.llm, embedder=ctx.embedder, fixtures=ctx.fixtures, limit=opts.limit)
        return RunStats("s3")

    monkeypatch.setattr(cli, "_stage_run", lambda name: fake_run)
    assert cli.main(["run", "s3", "--edition", "test", "--fixtures", "tests/fixtures/harnesses",
                     "--limit", "5"]) == 0
    assert seen == {"llm": "fake", "embedder": "hash", "fixtures": Path("tests/fixtures/harnesses"), "limit": 5}


def test_reset_clears_stage_before_running(ctx, monkeypatch):
    run_batched(ctx, "s1", [Unit("k", "o/r")], lambda b: {"repo_hits": []}, batch_size=1, log=lambda m: None)
    seen = []

    def stage(c, o):
        seen.append(c.journal("s1").entries())
        return RunStats("s1")

    monkeypatch.setattr(cli, "_stage_run", lambda name: stage)
    cli.main(["run", "s1", "--edition", "test", "--reset"])
    assert seen == [[]]
    assert ctx.journal("s1").entries() == []


def test_stopped_stage_exits_nonzero(monkeypatch):
    monkeypatch.setattr(cli, "_stage_run", lambda name: lambda c, o: RunStats("s6", stopped="plan limit"))
    assert cli.main(["run", "s6", "--edition", "test"]) == 2


def _recorder(monkeypatch, stopped_at=None):
    calls = []

    def stage_run(name):
        def run(c, o):
            calls.append(name)
            return RunStats(name, stopped="limit" if name == stopped_at else None)
        return run

    monkeypatch.setattr(cli, "_stage_run", stage_run)
    monkeypatch.setattr(cli, "_freeze", lambda c: calls.append("freeze"))
    return calls


def test_run_all_runs_every_stage_then_freezes(monkeypatch):
    calls = _recorder(monkeypatch)
    assert cli.main(["run", "all", "--edition", "test"]) == 0
    assert calls == ["s1", "s2", "s3", "s4", "s5", "s6", "s7", "freeze", "s8", "s9"]


def test_run_all_stops_at_first_stopped_stage(monkeypatch):
    calls = _recorder(monkeypatch, stopped_at="s3")
    assert cli.main(["run", "all", "--edition", "test"]) == 2
    assert calls == ["s1", "s2", "s3"]


def test_run_all_returns_2_when_s8_stops_and_skips_s9(monkeypatch):
    calls = _recorder(monkeypatch, stopped_at="s8")
    assert cli.main(["run", "all", "--edition", "test"]) == 2
    assert calls[-2:] == ["freeze", "s8"]


def test_reset_with_all_is_a_usage_error(monkeypatch):
    _recorder(monkeypatch)
    with pytest.raises(SystemExit) as e:
        cli.main(["run", "all", "--edition", "test", "--reset"])
    assert e.value.code == 2


def test_reredact_reports_how_many_blobs_it_rewrote(fctx, capsys):
    from helpers import run_until

    run_until(fctx, "s2")
    assert cli.main(["reredact", "--edition", "test"]) == 0
    out = capsys.readouterr().out
    assert "re-redacted 0 of" in out
    assert int(out.split(" of ")[1].split()[0]) > 0


def test_audit_exits_nonzero_on_problems(ctx, capsys):
    run_batched(ctx, "s1", [Unit("k", "o/r")], lambda b: {"repo_hits": [
        {"repo": "o/r", "path": "CLAUDE.md", "component": "claude_md", "query_id": "q", "blob_sha": "s",
         "is_fork": False}]}, batch_size=1, log=lambda m: None)
    assert cli.main(["audit", "s1", "--edition", "test"]) == 0
    assert "nothing lost or duplicated" in capsys.readouterr().out
    ctx.tables.delete_part("repo_hits", next(iter(ctx.tables.parts("repo_hits"))))
    assert cli.main(["audit", "s1", "--edition", "test"]) == 1


def test_status_shows_deferred_units_and_s1_families(ctx, capsys):
    from pipeline.runner import Partial, write_state

    run_batched(ctx, "s2", [Unit("a", "o/a"), Unit("b", "o/b")],
                lambda batch: Partial({"repos": [{"repo": "o/a", "missing": False, "canary": False}]}, {"b": "x"}),
                batch_size=2, log=lambda m: None, give_up=lambda u, e: {})
    write_state(ctx, "s1", {"families_done": ["claude_md/nonfork"], "complete": False})
    cli.main(["status", "--edition", "test"])
    out = capsys.readouterr().out
    assert "s2: units=1 parts=1 repos=1 harness_files=0 redactions=0 deferred=1" in out
    assert "s1 families done: claude_md/nonfork; complete: False" in out


def test_supervise_detach_starts_a_new_session(monkeypatch, capsys, isolated_data):
    seen = {}

    class P:
        pid = 777

    def popen(cmd, **kw):
        seen.update(cmd=cmd, kw=kw)
        return P()

    monkeypatch.setattr(cli.subprocess, "Popen", popen)
    assert cli.main(["supervise", "s1", "--edition", "test", "--limit", "5", "--detach"]) == 0
    assert seen["cmd"][1:] == ["-m", "pipeline.cli", "supervise", "s1", "--edition", "test", "--limit", "5"]
    assert seen["kw"]["start_new_session"] is True
    assert "pid 777" in capsys.readouterr().out
