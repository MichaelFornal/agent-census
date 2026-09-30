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
