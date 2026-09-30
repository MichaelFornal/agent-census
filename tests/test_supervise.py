import json
import os
import re
import signal
import subprocess
import sys
import time

import pytest

from pipeline import supervise as sup
from pipeline.runner import Unit, run_batched


def hits(batch):
    return {"repo_hits": [{"repo": u.payload, "path": "CLAUDE.md", "component": "claude_md", "query_id": "q",
                           "blob_sha": u.key, "is_fork": False} for u in batch]}


class FakeChild:
    def __init__(self, code, pid=4242):
        self.code, self.pid = code, pid

    def wait(self):
        return self.code

    def terminate(self):
        self.terminated = True


def script(ctx, outcomes):
    """Each outcome is (exit code, units the child journals before it exits)."""
    todo = iter(outcomes)
    commands, n = [], [0]

    def spawn(cmd, stdout=None, stderr=None):
        commands.append(cmd)
        code, units = next(todo)
        for _ in range(units):
            run_batched(ctx, "s1", [Unit(f"k{n[0]}", f"o/r{n[0]}")], hits, batch_size=1, log=lambda m: None)
            n[0] += 1
        return FakeChild(code)

    return spawn, commands


@pytest.fixture(autouse=True)
def no_caffeinate(monkeypatch):
    monkeypatch.setattr(sup, "_keep_awake", lambda: None)


def test_restarts_until_the_stage_exits_zero(ctx):
    spawn, commands = script(ctx, [(-9, 2), (2, 1), (0, 1)])
    sleeps = []
    assert sup.supervise(ctx, "s1", ["--limit", "5"], spawn=spawn, sleep=sleeps.append, min_backoff=1.0) == 0
    assert len(commands) == 3 and commands[0][-6:] == ["run", "s1", "--edition", "test", "--limit", "5"]
    assert sleeps == [1.0, 1.0]  # both failed runs made progress
    log = (ctx.root / "logs" / "s1.log").read_text()
    assert "exit -9" in log and "complete" in log
    assert not (ctx.root / "logs" / "s1.child.pid").exists()  # removed on every exit path
    assert not (ctx.root / "logs" / "supervise-s1.pid").exists()


def test_backoff_doubles_without_progress_and_resets_with_it(ctx):
    spawn, _ = script(ctx, [(1, 0), (1, 0), (1, 0), (2, 1), (1, 0), (0, 0)])
    sleeps = []
    sup.supervise(ctx, "s1", [], spawn=spawn, sleep=sleeps.append, min_backoff=1.0, max_backoff=3.0)
    assert sleeps == [2.0, 3.0, 3.0, 1.0, 2.0]


def state_of(ctx, stage="s1"):
    return json.loads((ctx.root / "logs" / f"supervise-{stage}.state.json").read_text())


def test_state_file_counts_restarts_and_restarts_without_progress(ctx):
    seen = []
    spawn, _ = script(ctx, [(1, 0), (1, 1), (1, 0), (1, 0), (0, 0)])

    def spy(s):
        seen.append(state_of(ctx))

    sup.supervise(ctx, "s1", [], spawn=spawn, sleep=spy, min_backoff=1.0, max_backoff=8.0)
    assert [(s["restarts"], s["no_progress"], s["last_exit"], s["backoff_s"]) for s in seen] == [
        (1, 1, 1, 2.0), (2, 0, 1, 1.0), (3, 1, 1, 2.0), (4, 2, 1, 4.0)]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", seen[0]["last_at"])
    final = state_of(ctx)
    assert final["complete"] is True and final["restarts"] == 4 and final["last_exit"] == 0


def test_sigterm_terminates_the_child_and_the_supervisor_returns_143(ctx):
    children = []
    before = signal.getsignal(signal.SIGTERM)

    class Child(FakeChild):
        terminated = False

        def terminate(self):
            self.terminated = True

        def wait(self):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)  # SIGTERM arrives while the child runs
            return -15

    def spawn(cmd, stdout=None, stderr=None):
        children.append(Child(0))
        return children[-1]

    sleeps = []
    assert sup.supervise(ctx, "s1", [], spawn=spawn, sleep=sleeps.append, min_backoff=1.0) == 143
    assert len(children) == 1 and children[0].terminated and sleeps == []
    assert not (ctx.root / "logs" / "supervise-s1.pid").exists()
    assert signal.getsignal(signal.SIGTERM) == before


def test_sigterm_during_the_backoff_sleep_returns_143_promptly(ctx):
    spawn, commands = script(ctx, [(1, 0), (0, 0)])

    def sleep(s):
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        time.sleep(0.01)

    assert sup.supervise(ctx, "s1", [], spawn=spawn, sleep=sleep, min_backoff=1.0) == 143
    assert len(commands) == 1
    assert state_of(ctx)["stopped"] is True
    assert not (ctx.root / "logs" / "supervise-s1.pid").exists()


HELPER = """
import subprocess, sys
from pipeline import supervise as sup
from pipeline.context import make_ctx

sup._keep_awake = lambda: None
ctx = make_ctx("test")

def spawn(cmd, **kw):
    child = subprocess.Popen([sys.executable, "-c", CHILD])
    print(child.pid, flush=True)
    return child

sys.exit(sup.supervise(ctx, "s1", [], spawn=spawn, min_backoff=1800.0))
"""


def run_helper(child_code):
    return subprocess.Popen([sys.executable, "-c", f"CHILD = {child_code!r}\n{HELPER}"],
                            stdout=subprocess.PIPE, text=True)


def gone(pid, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_real_sigterm_stops_the_supervisor_and_its_running_child(ctx):
    proc = run_helper("import time; time.sleep(120)")
    try:
        child_pid = int(proc.stdout.readline())
        time.sleep(0.3)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) == 143
        assert gone(child_pid)
    finally:
        proc.kill()
    logs = ctx.root / "logs"
    assert not (logs / "supervise-s1.pid").exists() and not (logs / "s1.child.pid").exists()


def test_real_sigterm_ends_a_long_backoff_at_once(ctx):
    proc = run_helper("raise SystemExit(1)")  # the child fails, so the supervisor backs off 3,600 s
    try:
        proc.stdout.readline()
        state = ctx.root / "logs" / "supervise-s1.state.json"
        end = time.time() + 10
        while not state.exists() and time.time() < end:
            time.sleep(0.05)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) == 143
    finally:
        proc.kill()
    logs = ctx.root / "logs"
    assert not (logs / "supervise-s1.pid").exists() and not (logs / "s1.child.pid").exists()


def test_refuses_when_another_supervisor_is_alive(ctx):
    logs = ctx.root / "logs"
    logs.mkdir(parents=True)
    (logs / "supervise-s1.pid").write_text(str(os.getppid()))
    with pytest.raises(SystemExit, match="already supervised"):
        sup.supervise(ctx, "s1", [], spawn=None, sleep=None, min_backoff=1.0)


def test_stale_pidfile_is_replaced(ctx):
    logs = ctx.root / "logs"
    logs.mkdir(parents=True)
    (logs / "supervise-s1.pid").write_text("999999999")
    spawn, _ = script(ctx, [(0, 0)])
    assert sup.supervise(ctx, "s1", [], spawn=spawn, sleep=lambda s: None, min_backoff=1.0) == 0
