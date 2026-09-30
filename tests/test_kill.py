import os
import signal
import subprocess
import sys
import time

from pipeline.context import make_ctx
from pipeline.runner import Unit, run_batched

HELPER = """
import time
from pipeline.context import make_ctx
from pipeline.runner import Unit, run_batched

def work(batch):
    time.sleep(0.3)
    return {"repo_hits": [{"repo": u.payload, "path": "CLAUDE.md", "component": "claude_md",
                           "query_id": "q", "blob_sha": u.key, "is_fork": False} for u in batch]}

run_batched(make_ctx("kill"), "s1", [Unit(f"k{i}", f"o/r{i}") for i in range(40)], work, batch_size=2,
            log=lambda m: None)
"""


def work(batch):
    return {"repo_hits": [{"repo": u.payload, "path": "CLAUDE.md", "component": "claude_md",
                           "query_id": "q", "blob_sha": u.key, "is_fork": False} for u in batch]}


def test_kill_9_mid_stage_then_resume_loses_and_duplicates_nothing(isolated_data):
    proc = subprocess.Popen([sys.executable, "-c", HELPER])
    journal = isolated_data / "work" / "kill" / "journal" / "s1.jsonl"
    deadline = time.time() + 60
    while time.time() < deadline and (not journal.exists() or len(journal.read_text().splitlines()) < 3):
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    assert proc.poll() is None, f"helper exited on its own (return code {proc.returncode}) before the kill"
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait()
    ctx = make_ctx("kill")
    done_before = len(ctx.journal("s1").done_units())
    assert 0 < done_before < 40
    units = [Unit(f"k{i}", f"o/r{i}") for i in range(40)]
    stats = run_batched(ctx, "s1", units, work, batch_size=2, log=lambda m: None)
    assert stats.units_run == 40 - done_before
    repos = [r["repo"] for r in ctx.tables.read("repo_hits")]
    assert sorted(repos) == sorted(f"o/r{i}" for i in range(40))
