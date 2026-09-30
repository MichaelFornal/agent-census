import os
import signal
import subprocess
import sys
import time

import pytest
from fake_github import SECRET, FakeGitHub

from pipeline.audit import audit
from pipeline.context import make_ctx
from pipeline.runner import read_state

CENSUS = [sys.executable, "-m", "pipeline.cli"]


@pytest.fixture(scope="module")
def github():
    server = FakeGitHub()
    yield server
    server.close()


def env_for(data, github, token):
    return {**os.environ, "CENSUS_DATA": str(data), "GITHUB_API_URL": github.url, "GITHUB_TOKEN": token,
            "CENSUS_PACE": "0", "CENSUS_SUPERVISE_MIN_S": "0.1"}


def supervise(stage, env, timeout=300):
    done = subprocess.run([*CENSUS, "supervise", stage, "--edition", "e"], env=env, timeout=timeout)
    assert done.returncode == 0


def supervise_with_kills(stage, env, data, kills=3, step=2):
    """Run the stage under the supervisor and SIGKILL the stage process each time `step` more batches are
    journaled. Returns how many kills landed."""
    sup = subprocess.Popen([*CENSUS, "supervise", stage, "--edition", "e"], env=env)
    journal = data / "work" / "e" / "journal" / f"{stage}.jsonl"
    pidfile = data / "work" / "e" / "logs" / f"{stage}.child.pid"
    killed, mark, deadline = 0, 0, time.time() + 300
    while sup.poll() is None and time.time() < deadline and killed < kills:
        lines = len(journal.read_text().splitlines()) if journal.exists() else 0
        if lines >= mark + step and pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
                killed += 1
                mark = lines
            except (ProcessLookupError, ValueError):
                pass  # the child just exited, or its pid file is being replaced
        time.sleep(0.01)
    assert sup.wait(timeout=300) == 0
    return killed


def ctx_at(data, monkeypatch):
    monkeypatch.setenv("CENSUS_DATA", str(data))
    monkeypatch.delenv("CENSUS_BLOBS", raising=False)
    return make_ctx("e")


def table(ctx, name, cols):
    return sorted(tuple(str(r[c]) for c in cols) for r in ctx.tables.read(name))


HITS = ("repo", "path", "component", "query_id", "blob_sha", "is_fork")
REPOS = ("repo", "missing", "error", "stars", "is_fork", "head_oid", "tree_truncated")
FILES = ("repo", "path", "kind", "blob_sha", "size", "fetched", "skip_reason")


@pytest.fixture(scope="module")
def reference(github, tmp_path_factory):
    """An uninterrupted S1 and S2 run: what the killed runs must reproduce."""
    data = tmp_path_factory.mktemp("ref")
    env = env_for(data, github, "ref")
    supervise("s1", env)
    supervise("s2", env)
    return data


def test_s1_killed_three_times_matches_an_uninterrupted_run(github, reference, tmp_path, monkeypatch):
    data = tmp_path / "killed"
    assert supervise_with_kills("s1", env_for(data, github, "k1"), data) >= 1
    got = ctx_at(data, monkeypatch)
    hits = table(got, "repo_hits", HITS)
    assert audit(got, "s1") == [] and read_state(got, "s1")["complete"]
    want = ctx_at(reference, monkeypatch)
    assert hits == table(want, "repo_hits", HITS) and len(hits) > 3000
    assert "restarting" in (data / "work" / "e" / "logs" / "s1.log").read_text()


def test_s1_survives_the_supervisor_being_killed_too(github, reference, tmp_path, monkeypatch):
    data = tmp_path / "both"
    env = env_for(data, github, "k2")
    sup = subprocess.Popen([*CENSUS, "supervise", "s1", "--edition", "e"], env=env)
    journal = data / "work" / "e" / "journal" / "s1.jsonl"
    pidfile = data / "work" / "e" / "logs" / "s1.child.pid"
    deadline = time.time() + 120
    while time.time() < deadline and not (journal.exists() and len(journal.read_text().splitlines()) >= 3):
        assert sup.poll() is None
        time.sleep(0.01)
    child = int(pidfile.read_text())
    os.kill(sup.pid, signal.SIGKILL)
    os.kill(child, signal.SIGKILL)
    sup.wait()
    supervise("s1", env)  # the stale pid file and the dead child's lock do not block a new supervisor
    got = ctx_at(data, monkeypatch)
    hits = table(got, "repo_hits", HITS)
    assert audit(got, "s1") == []
    assert hits == table(ctx_at(reference, monkeypatch), "repo_hits", HITS)


def test_s2_killed_three_times_matches_an_uninterrupted_run(github, reference, tmp_path, monkeypatch):
    data = tmp_path / "killed"
    env = env_for(data, github, "k3")
    supervise("s1", env)
    assert supervise_with_kills("s2", env, data) >= 1
    got = ctx_at(data, monkeypatch)
    repos, files = table(got, "repos", REPOS), table(got, "harness_files", FILES)
    redactions = sorted({(r["blob_sha"], r["rule"], r["n"]) for r in got.tables.read("redactions")})
    blobs = {f["blob_sha"]: got.blobs.get(f["blob_sha"]) for f in got.tables.read("harness_files") if f["fetched"]}
    assert audit(got, "s2") == []
    assert got.attempts("s2").counts()  # the fake's one-time failures were deferred, then harvested
    assert not any(r["error"] and r["error"].startswith(("unreachable", "partial")) for r in got.tables.read("repos"))
    assert not any(SECRET in text for text in blobs.values())

    want = ctx_at(reference, monkeypatch)
    assert repos == table(want, "repos", REPOS) and len(repos) == 1300
    assert sum(1 for r in want.tables.read("repos") if r["error"] == "not_found") == 14
    assert files == table(want, "harness_files", FILES)
    assert redactions == sorted({(r["blob_sha"], r["rule"], r["n"]) for r in want.tables.read("redactions")})
    assert blobs == {f["blob_sha"]: want.blobs.get(f["blob_sha"]) for f in want.tables.read("harness_files")
                     if f["fetched"]}
    assert "restarting" in (data / "work" / "e" / "logs" / "s2.log").read_text()
