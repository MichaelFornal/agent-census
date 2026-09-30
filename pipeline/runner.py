"""Idempotent, resumable stage execution (PRD §4).

A batch of units writes one Parquet part per output table, then one journal line naming the part
and its units. A part with no journal line is an orphan from a killed run; it is deleted before
the next run starts, so kill -9 at any point loses nothing and duplicates nothing.

A unit that hits a transient failure is deferred: not journaled, tried again on later runs, and
journaled with a terminal reason only after MAX_ATTEMPTS failures.
"""
import fcntl
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pipeline.journal import part_name, unit_key
from pipeline.store import atomic_write

MAX_ATTEMPTS = 5
RETRY_GAP_S = 3600.0  # a failed unit is not tried again sooner; quick reruns must not burn all its attempts

STAGE_TABLES: dict[str, list[str]] = {
    "s1": ["repo_hits", "s1_overflows"],
    "s2": ["repos", "harness_files", "redactions"],
    "s3": ["artifacts"],
    "s4": ["clusters", "membership", "lineage", "mutations"],
    "s5": ["features", "glyphs"],
    "s6": ["semantics", "semantics_rejects", "llm_calls"],
    "s7": ["use_cases", "uc_membership", "technique_candidates", "uncharted"],
}

Rows = dict[str, list[dict]]


@dataclass(frozen=True)
class Unit:
    key: str
    payload: Any = None


class StopStage(Exception):
    """Raised by work to end a stage early and cleanly (e.g. a plan limit). The batch is not journaled."""


@dataclass
class Partial:
    """Work output for a batch in which some units hit a transient failure. `deferred` maps unit key
    to error. `rows` holds rows for the other units only; if every unit is deferred, rows are discarded."""

    rows: Rows
    deferred: dict[str, str]


@dataclass
class RunStats:
    stage: str
    units_total: int = 0
    units_skipped: int = 0
    units_run: int = 0
    units_deferred: int = 0
    units_gave_up: int = 0
    rows: dict[str, int] = field(default_factory=dict)
    stopped: str | None = None


def merge_stats(total: RunStats, part: RunStats) -> None:
    total.units_total += part.units_total
    total.units_skipped += part.units_skipped
    total.units_run += part.units_run
    total.units_deferred += part.units_deferred
    total.units_gave_up += part.units_gave_up
    for t, n in part.rows.items():
        total.rows[t] = total.rows.get(t, 0) + n
    total.stopped = total.stopped or part.stopped


def _clean_orphans(ctx, stage: str) -> None:
    keep = ctx.journal(stage).parts()
    for t in STAGE_TABLES[stage]:
        for p in ctx.tables.parts(t) - keep:
            ctx.tables.delete_part(t, p)


def _commit(ctx, stage: str, keys: list[str], out: Rows, stats: RunStats) -> None:
    unknown = set(out) - set(STAGE_TABLES[stage])
    if unknown:
        raise ValueError(f"{stage} wrote undeclared tables {sorted(unknown)}")
    part = part_name(keys)
    rows = {t: ctx.tables.write_part(t, part, out.get(t, [])) for t in STAGE_TABLES[stage]}
    ctx.journal(stage).record(part, keys, rows)  # the commit point
    stats.units_run += len(keys)
    for t, n in rows.items():
        stats.rows[t] = stats.rows.get(t, 0) + n


_LOCKS: dict[str, int] = {}


def _lock(ctx, stage: str) -> None:
    """One process per (edition, stage). A second runner would delete the first one's uncommitted
    parts as orphans. The kernel drops the lock when the process dies, kill -9 included."""
    path = ctx.root / "journal" / f"{stage}.lock"
    if str(path) in _LOCKS:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise SystemExit(f"{stage}: another process is running this stage for edition {ctx.edition}") from None
    _LOCKS[str(path)] = fd


class StageSession:
    """One stage run in this process: takes the stage lock, removes orphan parts, and reads the journal
    once. S1 commits tens of thousands of one-unit batches; re-reading the journal for each is quadratic."""

    def __init__(self, ctx, stage: str) -> None:
        self.ctx, self.stage = ctx, stage
        _lock(ctx, stage)
        _clean_orphans(ctx, stage)
        self.done: set[str] = ctx.journal(stage).done_units()
        self.attempts: dict[str, int] = ctx.attempts(stage).counts()
        self.last_attempt: dict[str, float] = ctx.attempts(stage).last()
        self.retry_gap = float(os.environ.get("CENSUS_RETRY_GAP_S", RETRY_GAP_S))

    def commit(self, keys: list[str], out: Rows, stats: RunStats) -> None:
        _commit(self.ctx, self.stage, keys, out, stats)
        self.done.update(keys)

    def failed(self, key: str, error: str) -> int:
        self.ctx.attempts(self.stage).record(key, error)
        self.attempts[key] = self.attempts.get(key, 0) + 1
        self.last_attempt[key] = time.time()
        return self.attempts[key]


def _settle(session: StageSession, batch: list[Unit], rows: Rows, failed: dict[str, str],
            give_up: Callable[[Unit, str], Rows] | None, stats: RunStats) -> list[str]:
    """Record failed attempts and return the keys to journal. A unit at MAX_ATTEMPTS is given up on:
    its terminal rows join this commit."""
    unknown = set(failed) - {u.key for u in batch}
    if unknown:
        raise ValueError(f"{session.stage} deferred units that were not in the batch: {sorted(unknown)}")
    if failed and give_up is None:
        raise ValueError(f"{session.stage} deferred units but passed no give_up")
    keys = []
    for u in batch:
        if u.key not in failed:
            keys.append(u.key)
        elif session.failed(u.key, failed[u.key]) >= MAX_ATTEMPTS:
            for t, extra in give_up(u, failed[u.key]).items():
                rows[t] = rows.get(t, []) + extra
            stats.units_gave_up += 1
            keys.append(u.key)
        else:
            stats.units_deferred += 1
    return keys


def run_batched(ctx, stage: str, units: list[Unit], work: Callable[[list[Unit]], Rows | Partial], batch_size: int,
                limit: int | None = None, log: Callable[[str], None] = print,
                give_up: Callable[[Unit, str], Rows] | None = None,
                session: StageSession | None = None, now: Callable[[], float] = time.time) -> RunStats:
    session = session or StageSession(ctx, stage)
    if limit is not None:
        units = units[:limit]  # a stable prefix: rerunning with the same limit resumes the same slice
    done = session.done
    all_keys = {u.key for u in units}
    todo, seen, waiting = [], set(), 0
    t = now()
    for u in units:
        if u.key not in done and u.key not in seen:
            seen.add(u.key)
            if t - session.last_attempt.get(u.key, -session.retry_gap) < session.retry_gap:
                waiting += 1  # failed too recently: leave its attempts for a later run
            else:
                todo.append(u)
    stats = RunStats(stage, units_total=len(all_keys), units_skipped=len(all_keys & done), units_deferred=waiting)
    for i in range(0, len(todo), batch_size):
        batch = todo[i:i + batch_size]
        try:
            out = work(batch)
            rows, failed = (out.rows, out.deferred) if isinstance(out, Partial) else (out, {})
            keys = _settle(session, batch, rows, failed, give_up, stats)
        except StopStage as e:
            stats.stopped = str(e)
            break
        if keys:
            session.commit(keys, rows, stats)
        log(f"{stage}: {stats.units_run}/{len(todo)} units"
            + (f", {stats.units_deferred} deferred" if stats.units_deferred else ""))
    if stats.units_deferred and not stats.stopped:
        stats.stopped = f"{stats.units_deferred} units deferred after transient failures; rerun to retry"
    return stats


def run_whole(ctx, stage: str, fingerprint: str, work: Callable[[], Rows],
              log: Callable[[str], None] = print) -> RunStats:
    """For stages computed over whole tables (dedup, taxonomy): recompute only when inputs change."""
    _lock(ctx, stage)
    key = unit_key(stage, fingerprint)
    stats = RunStats(stage, units_total=1)
    if key in ctx.journal(stage).done_units():
        stats.units_skipped = 1
        return stats
    reset(ctx, stage)
    try:
        out = work()
    except StopStage as e:
        stats.stopped = str(e)
        return stats
    _commit(ctx, stage, [key], out, stats)
    log(f"{stage}: recomputed")
    return stats


def reset(ctx, stage: str) -> None:
    _lock(ctx, stage)
    ctx.journal(stage).clear()  # journal first: a kill after this leaves orphans, which are cleaned
    ctx.attempts(stage).clear()
    ctx.state_path(stage).unlink(missing_ok=True)
    for t in STAGE_TABLES[stage]:
        ctx.tables.clear(t)


def read_state(ctx, stage: str) -> dict:
    """Small per-stage progress facts that are not rows (e.g. which S1 families are fully walked)."""
    p = ctx.state_path(stage)
    return json.loads(p.read_text()) if p.exists() else {}


def write_state(ctx, stage: str, state: dict) -> None:
    atomic_write(ctx.state_path(stage), json.dumps(state, sort_keys=True).encode())
