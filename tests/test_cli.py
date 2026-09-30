from pathlib import Path

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
    monkeypatch.setattr(cli, "_stage_run", lambda name: lambda c, o: RunStats("s1"))
    cli.main(["run", "s1", "--edition", "test", "--reset"])
    assert ctx.journal("s1").entries() == []


def test_stopped_stage_exits_nonzero(monkeypatch):
    monkeypatch.setattr(cli, "_stage_run", lambda name: lambda c, o: RunStats("s6", stopped="plan limit"))
    assert cli.main(["run", "s6", "--edition", "test"]) == 2
