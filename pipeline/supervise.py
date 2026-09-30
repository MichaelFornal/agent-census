"""census supervise: keep one stage running until it completes (PRD §9 M2: unattended).

The stage runs as a child process. kill -9, a crash, a plan limit, a GitHub outage or deferred units end
only the child; the supervisor waits and starts it again. Progress is whatever the journal says, so a
restart costs nothing. The wait doubles while no new unit is journaled, and resets when one is.

After every child exit the supervisor writes logs/supervise-<stage>.state.json, so `census status` shows a stage
that keeps restarting without progress. SIGTERM stops the child and ends the supervisor with exit code 143.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable

from pipeline.context import Ctx
from pipeline.store import atomic_write

MAX_BACKOFF_S = 1800.0
TERMINATED = 143  # 128 + SIGTERM


def state_path(ctx: Ctx, stage: str):
    return ctx.root / "logs" / f"supervise-{stage}.state.json"


def read_supervisor_state(ctx: Ctx, stage: str) -> dict:
    p = state_path(ctx, stage)
    return json.loads(p.read_text()) if p.exists() else {}


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
    prior = read_supervisor_state(ctx, stage)
    resumed = {} if prior.get("complete") else prior
    restarts, stuck = int(resumed.get("restarts", 0)), int(resumed.get("no_progress", 0))
    current: list = []  # the running child, for the SIGTERM handler
    stopping = False

    def on_term(signum, frame) -> None:
        nonlocal stopping
        stopping = True
        if current:
            current[0].terminate()

    can_signal = threading.current_thread() is threading.main_thread()
    previous = signal.signal(signal.SIGTERM, on_term) if can_signal else None
    try:
        with (logs / f"{stage}.log").open("ab") as log:

            def say(msg: str) -> None:
                log.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} supervise {stage}: {msg}\n".encode())
                log.flush()

            def note(code: int, backoff_s: float, **extra) -> None:
                state = {"restarts": restarts, "no_progress": stuck, "last_exit": code,
                         "last_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "backoff_s": backoff_s, **extra}
                atomic_write(state_path(ctx, stage), json.dumps(state, sort_keys=True).encode())

            def nap(seconds: float) -> None:
                """The real sleep runs in slices of at most a second, so SIGTERM ends a 30-minute backoff at once.
                An injected sleep (tests) is called once with the whole backoff."""
                if sleep is not time.sleep:
                    sleep(seconds)
                    return
                end = time.monotonic() + seconds
                while not stopping and (left := end - time.monotonic()) > 0:
                    sleep(min(1.0, left))

            code = 0
            while not stopping:
                before = len(ctx.journal(stage).done_units())
                child = spawn(cmd, stdout=log, stderr=subprocess.STDOUT)
                current[:] = [child]
                if stopping:  # SIGTERM landed between the flag check and the spawn
                    child.terminate()
                atomic_write(logs / f"{stage}.child.pid", str(child.pid).encode())
                code = child.wait()
                current.clear()
                if stopping:
                    break
                if code == 0:
                    note(0, 0.0, complete=True)
                    say("complete")
                    return 0
                progressed = len(ctx.journal(stage).done_units()) > before
                restarts += 1
                stuck = 0 if progressed else stuck + 1
                backoff = min_backoff if progressed else min(max_backoff, backoff * 2)
                note(code, backoff)
                say(f"exit {code}, {'progress' if progressed else 'no progress'}; restarting in {backoff:.0f}s")
                nap(backoff)
            note(code, 0.0, stopped=True)
            say("stopped by SIGTERM")
            return TERMINATED
    finally:
        if can_signal:
            signal.signal(signal.SIGTERM, previous)
        pidfile.unlink(missing_ok=True)
        (logs / f"{stage}.child.pid").unlink(missing_ok=True)
        if awake is not None:
            awake.terminate()
