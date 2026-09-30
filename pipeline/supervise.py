"""census supervise: keep one stage running until it completes (PRD §9 M2: unattended).

The stage runs as a child process. kill -9, a crash, a plan limit, a GitHub outage or deferred units end
only the child; the supervisor waits and starts it again. Progress is whatever the journal says, so a
restart costs nothing. The wait doubles while no new unit is journaled, and resets when one is.
"""
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable

from pipeline.context import Ctx
from pipeline.store import atomic_write

MAX_BACKOFF_S = 1800.0


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # the pid exists and belongs to someone else
        return True
    return True


def _keep_awake() -> subprocess.Popen | None:
    """macOS: hold off idle and system sleep for as long as this process lives."""
    exe = shutil.which("caffeinate")
    return subprocess.Popen([exe, "-is", "-w", str(os.getpid())]) if exe else None


def supervise(ctx: Ctx, stage: str, run_args: list[str], *, spawn: Callable = subprocess.Popen,
              sleep: Callable[[float], None] = time.sleep, min_backoff: float | None = None,
              max_backoff: float = MAX_BACKOFF_S) -> int:
    if min_backoff is None:
        min_backoff = float(os.environ.get("CENSUS_SUPERVISE_MIN_S", "60"))
    logs = ctx.root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    pidfile = logs / f"supervise-{stage}.pid"
    if pidfile.exists():
        old = pidfile.read_text().strip()
        if old.isdigit() and int(old) != os.getpid() and _alive(int(old)):
            raise SystemExit(f"{stage} is already supervised by pid {old}")
    atomic_write(pidfile, str(os.getpid()).encode())
    awake = _keep_awake()
    cmd = [sys.executable, "-m", "pipeline.cli", "run", stage, "--edition", ctx.edition, *run_args]
    backoff = min_backoff
    try:
        with (logs / f"{stage}.log").open("ab") as log:

            def say(msg: str) -> None:
                log.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} supervise {stage}: {msg}\n".encode())
                log.flush()

            while True:
                before = len(ctx.journal(stage).done_units())
                child = spawn(cmd, stdout=log, stderr=subprocess.STDOUT)
                atomic_write(logs / f"{stage}.child.pid", str(child.pid).encode())
                code = child.wait()
                if code == 0:
                    say("complete")
                    return 0
                progressed = len(ctx.journal(stage).done_units()) > before
                backoff = min_backoff if progressed else min(max_backoff, backoff * 2)
                say(f"exit {code}, {'progress' if progressed else 'no progress'}; restarting in {backoff:.0f}s")
                sleep(backoff)
    finally:
        pidfile.unlink(missing_ok=True)
        if awake is not None:
            awake.terminate()
