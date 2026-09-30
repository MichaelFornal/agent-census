# M2 Full Discover and Harvest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make S1 (discover) and S2 (harvest) able to enumerate and harvest the whole repo universe unattended, with `kill -9` resume verified mid-S1 and mid-S2, after first landing the two fixes M1's final review deferred (PRD §9, row M2).

**Architecture:** The runner gains a third outcome for a unit besides done and not-done: deferred. A unit that hit a transient failure is not journaled, is retried on later runs, and only after five failed attempts is it journaled with a terminal reason. S2 stops asking GraphQL for a depth-4 tree (the cause of M1's 25% 5xx rate): it takes repo metadata and the top level of `.claude` from a light batched GraphQL query, and lists the full tree with one REST git-trees call per repo that has subdirectories. Blobs carry their redaction version and counts in a header inside the compressed file, and a blob written under older rules is re-redacted when it is next read. A `census supervise` process runs a stage as a child, restarts it after any exit that is not success, and survives the session that started it.

**Tech Stack:** Python 3.12 with `uv`; httpx; DuckDB and pyarrow; zstandard; pytest; `http.server` for the fake GitHub used by the kill tests. No new dependencies.

**Spec:** `docs/PRD.md` (§4 S1, S2, Storage; §9 row M2; §10 Coverage, Rate limits). Measured inputs: `docs/m1/slice-run.md`, section "Inputs for M2". M1's rulings and deferred minors: `data/work/m1-slice/sdd/progress.md` (gitignored, local). Also read `CLAUDE.md`.

## Measured inputs this plan is built on

| Input | Value | Source |
|---|---|---|
| Code search sustained rate | 2.6 successful requests/min, 42% of requests rate-limited | slice-run.md |
| Seeds walked | each seed twice: plain (non-forks) and `fork:only` | slice-run.md |
| `path:.claude` root count | 2,785,280 files | `data/m0/search_cache.jsonl`, key `path:.claude size:0..393216\|1\|1` |
| `filename:CLAUDE.md` | 790,528 files; 2,340 lattice + 8,266 page requests projected | PRD §9.1 |
| GraphQL at batch 25 with a depth-4 tree | 25% of posts 5xx; about 480 repos/hr; 2.6% of repos lost as single-repo timeouts; 15.6% of repos had subtrees cut | slice-run.md |
| GraphQL at batch 25 without a tree | no retries, 3.71 s median | PRD §9.1 |
| Blob store | 12,456 blobs per 1,000 repos; mean 4,045 bytes compressed; 6.5 KB on disk per blob plus a 4 KB sidecar, and 516 of 12,456 sidecars were non-empty | measured on `data/blobs` 2026-09-30 |
| Free disk | 15 GiB on the data volume | `df` 2026-09-30 |

Derived: a full S1 is about 55,000 search requests (48,000 for the plain families, plus about 14% for forks), so about two weeks at 2.6 requests/min. `path:.claude` alone is about 36,000 of them.

## Decisions needed from Michael (none blocks Tasks 1–9)

1. **The M0 lattice is still running** (`uv run --directory spike/m0 python -m m0.lattice --seed claude_md --seed plugin --seed claude_dir`). It shares the code-search budget, so Task 10 cannot start any live S1 until it is stopped. It resumes from its cache. Task 10 asks before stopping it.
2. **Disk.** The full harvest needs an estimated 35–80 GiB for blobs (5–12 million blobs at 6.5 KB on disk). The data volume has 15 GiB free. Task 7 adds `CENSUS_BLOBS` so the blob store can live on another volume, and a guard that stops S2 when less than 5 GiB is free. Task 10 launches the full S1 (well under 1 GiB) and does not launch the full S2 until Michael says where the blobs go.
3. **`path:.claude` costs about nine of the fourteen days.** Narrower seeds (the six component queries in PRD §2, 1.13M files) would cut it to about four, and miss repos whose `.claude` holds only files the pipeline does not parse. The PRD names `path:.claude` as the seed, so this plan keeps it. The families run in the order claude_md, claude_dir, mcp, plugin, so the choice can still be changed during the first three days.

## Global Constraints

- Python 3.12 managed with `uv`. Run tests with `uv run pytest -q`. The suite had 294 tests passing before this plan.
- "Every stage is idempotent and resumable: work units are keyed by an input hash, and a per-stage journal records completion. `kill -9` at any point, then rerun, loses nothing and duplicates nothing" (PRD §4). "Test resume by killing it, not by reasoning" (CLAUDE.md).
- "Redact secrets before anything is stored" (CLAUDE.md). Blob text reaches disk only through `BlobStore`, which redacts first. Nothing else writes blob text.
- "No silent catches or TODO stubs in shipped code" (PRD §8). Every `except` records, counts or re-raises.
- One token, official APIs only (PRD §10). Code search: 10 requests/min nominal, back off with decay, at least 60 s on a hint-less secondary limit, sleep until reset when `x-ratelimit-remaining` is 0.
- Tests never reach the network. Only Task 10 talks to GitHub.
- "Every number the site prints comes from `facts.json`" (CLAUDE.md). This plan changes no page copy.
- The README and the launch post are Michael's. Do not create a README anywhere.
- A unit key includes its inputs' fingerprint. When an upstream stage's output changes, run `census run <downstream> --reset` before rerunning the downstream stage.
- Every commit message ends with these two lines. The commit steps below show only the subject line.
  ```
  Assisted-by: Claude
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  ```
- Do not push. Pushing is Michael's call.
- Out of scope, owned by later milestones: S3–S5 at full scale, including loading `harness_files` without reading every row into Python (M3); redaction-rule tuning (M3); canaries (M3); S6 at full scale (M4).

## Rulings this plan makes (record them in the ledger when executing)

- **Transient means "GitHub answered, but not for this unit".** A unit is deferred, not failed, when its own request fails while a health probe succeeds. When the probe fails too, the stage stops and nothing is recorded. After `MAX_ATTEMPTS = 5` deferrals the unit is journaled with a terminal reason (`unreachable:<error>` for a repo, `call_error` for an S6 cluster). One attempt is spent per run, and the supervisor's backoff spaces the runs.
- **S2 lists `.claude` with REST git trees, not nested GraphQL.** The PRD names REST git trees as the fallback. M1 measured the nested tree failing a quarter of the time, so the fallback becomes the path. Task 10 measures the new rate before the full run and updates PRD §4 S2.
- **Blob format.** The redaction version and counts live in a header line inside the `.zst` file, not in a `.json` sidecar. The M1 review asked for the version in the sidecar. One file per blob makes a re-redaction a single atomic write, and saves 4 KB of disk per blob (about 48 GiB at 12 million blobs). M1 blobs with sidecars stay readable.
- **`REDACT_VERSION = 1` names the M1 rule set.** Task 5 also stops a later rule from relabelling an earlier rule's placeholder. That changes a label and a count in a rare case, never which text is removed, so it stays version 1 and M1's blobs and manifest stay valid.
- **Re-redaction can only tighten.** Original text is never stored, so a blob redacted under older rules is re-redacted by applying the current rules to its stored text. A rule that is loosened later needs those blobs fetched again. The per-blob counts say which blobs a rule touched. M3 owns that procedure.
- **S3's unit key includes `REDACT_VERSION`.** After a version bump S3 must be run with `--reset`. `census freeze` already refuses duplicated artifacts.
- **Search result pages are not cached.** Only lattice counts are. A page is committed by the runner as part of its node, so a kill costs at most one node's pages (ten requests). Caching every page would load several GiB of JSONL into memory at full scale.
- **S2 may run while S1 is still running**, once both `claude_md` families are complete, because nested CLAUDE.md paths come only from those families. S2 exits with "S1 is still discovering" until S1 is complete, and the supervisor reruns it.

## Review Focus

1. **Code search answers `incomplete_results: true` with a low or zero count.** Expected: the request is retried, an incomplete count is never cached, and a subtree is not pruned on the first incomplete answer. Test: Task 2, `test_incomplete_results_are_retried_and_never_cached`.
2. **The GraphQL primary rate limit arrives as HTTP 200 with a `RATE_LIMITED` error.** Expected: the client waits for the reset and retries; no repo is recorded as missing. Test: Task 2, `test_graphql_rate_limited_200_waits_for_reset_then_retries`.
3. **A monorepo with hundreds of nested CLAUDE.md files.** Expected: the paths beyond the first 20 go in follow-up queries of 60; the repo is not lost to one oversized query. Test: Task 3, `test_nested_paths_beyond_the_cap_go_in_follow_up_queries`.
4. **The disk fills during a multi-day harvest.** Expected: S2 stops before writing, with a message naming the volume, and resumes when space is back. Test: Task 7, `test_low_disk_stops_the_stage_before_any_request`.
5. **Someone runs a stage by hand while the supervisor is running it.** Expected: the second process refuses to start; it must not delete the first one's uncommitted parts as orphans. Test: Task 1, `test_second_process_cannot_run_the_same_stage`.

---

## File Structure

```
pipeline/journal.py        + Attempts: per-stage log of transient failures
pipeline/context.py        + Ctx.attempts(stage), Ctx.state_path(stage)
pipeline/runner.py         + Partial, MAX_ATTEMPTS, StageSession (lock, one journal read), give_up, read_state/write_state
pipeline/gh.py             + api_base(), CENSUS_PACE, RestClient, GraphQLClient.healthy(); count-only search cache
pipeline/s2_harvest.py     rewritten: light meta query, REST tree, deferral, final pass, SQL unit discovery, S1 gate, disk guard
pipeline/s6_extract.py     single-artifact call errors deferred, then rejected after MAX_ATTEMPTS
pipeline/redact.py         + REDACT_VERSION, rules_fingerprint(); placeholders are never re-matched
pipeline/store.py          blob header (version + counts), re-redaction on read, legacy sidecar reading
pipeline/freeze.py         + redact_version in the manifest and digest; stale-blob check; reredact()
pipeline/s8_facts.py       refuses an edition frozen under another redaction version
pipeline/s3_parse.py       unit key includes REDACT_VERSION
pipeline/s1_discover.py    one StageSession per run, family progress state, no full-table load in full mode
pipeline/paths.py          + CENSUS_BLOBS
pipeline/supervise.py      new: run a stage as a child until it completes
pipeline/audit.py          new: journal/part/row consistency and natural-key uniqueness for a stage
pipeline/cli.py            + supervise, audit, reredact; status shows deferred units and S1 families
facts/harnesses_total.sql  comment corrected
tests/fake_github.py       new: local HTTP server for /search/code, /graphql, /repos/.../git/trees
tests/test_kill_stages.py  new: kill -9 of real S1 and S2 processes under the supervisor
tests/test_*.py            updated and extended per task
docs/m2/rehearsal.md       new (Task 10): measured rehearsal
docs/m2/runbook.md         new (Task 10): how to watch, stop, restart and kill-test the full run
docs/PRD.md                §4 S1/S2 mechanics, §8 CLI, §9.1 measured numbers (Task 10)
```

---

### Task 1: Runner — deferred units, one session per run, a stage lock

First half of deferred M1 fix 1: the runner can hold a unit back instead of journaling its failure.

**Files:**
- Modify: `pipeline/journal.py`, `pipeline/context.py`, `pipeline/runner.py`, `pipeline/cli.py:24-29`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `Journal`, `read_jsonl`, `append_jsonl`, `atomic_write` (existing).
- Produces:
  - `pipeline.journal.Attempts(path)` with `counts() -> dict[str, int]`, `record(unit: str, error: str) -> None`, `clear() -> None`.
  - `Ctx.attempts(stage) -> Attempts` (file `journal/<stage>.attempts.jsonl`); `Ctx.state_path(stage) -> Path` (file `journal/<stage>.state.json`).
  - `pipeline.runner.MAX_ATTEMPTS = 5`.
  - `pipeline.runner.Partial(rows: Rows, deferred: dict[str, str])` — what `work` returns when some units hit a transient failure. `deferred` maps unit key to error. `rows` must hold rows only for the units that are not deferred.
  - `pipeline.runner.StageSession(ctx, stage)` with `.done: set[str]`, `.attempts: dict[str, int]`.
  - `run_batched(ctx, stage, units, work, batch_size, limit=None, log=print, give_up=None, session=None) -> RunStats`. `give_up(unit: Unit, error: str) -> Rows` returns the terminal rows for a unit that has failed `MAX_ATTEMPTS` times.
  - `RunStats.units_deferred: int`, `RunStats.units_gave_up: int`. When units remain deferred and nothing else stopped the stage, `RunStats.stopped` is `"<n> units deferred after transient failures; rerun to retry"`.
  - `read_state(ctx, stage) -> dict`, `write_state(ctx, stage, state: dict) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runner.py`. Change its import line to the one below and add the new imports at the top of the file.

```python
import subprocess
import sys

import pytest

from pipeline.context import make_ctx
from pipeline.runner import (MAX_ATTEMPTS, Partial, StageSession, StopStage, Unit, read_state, reset, run_batched,
                             run_whole, write_state)
```

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_runner.py -q`
Expected: FAIL at import with `ImportError: cannot import name 'MAX_ATTEMPTS'`.

- [ ] **Step 3: Add `Attempts` to `pipeline/journal.py`**

Append:

```python
class Attempts:
    """Transient failures per unit, one JSONL line per failed attempt. A unit that keeps failing is
    journaled as a terminal failure after runner.MAX_ATTEMPTS instead of being retried forever."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in read_jsonl(self.path):
            if "unit" in e:
                out[e["unit"]] = out.get(e["unit"], 0) + 1
        return out

    def record(self, unit: str, error: str) -> None:
        append_jsonl(self.path, {"unit": unit, "error": error,
                                 "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
```

- [ ] **Step 4: Add the two accessors to `Ctx` in `pipeline/context.py`**

Change the import to `from pipeline.journal import Attempts, Journal` and add after `Ctx.journal`:

```python
    def attempts(self, stage: str) -> Attempts:
        return Attempts(self.root / "journal" / f"{stage}.attempts.jsonl")

    def state_path(self, stage: str) -> Path:
        return self.root / "journal" / f"{stage}.state.json"
```

- [ ] **Step 5: Rewrite the runner**

In `pipeline/runner.py`, replace the module docstring and imports, keep `STAGE_TABLES`, `Rows`, `Unit`, `StopStage`, `_clean_orphans` and `_commit` as they are, and replace `RunStats`, `merge_stats`, `run_batched`, `run_whole` and `reset` with the code below.

```python
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
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pipeline.journal import part_name, unit_key
from pipeline.store import atomic_write

MAX_ATTEMPTS = 5
```

```python
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

    def commit(self, keys: list[str], out: Rows, stats: RunStats) -> None:
        _commit(self.ctx, self.stage, keys, out, stats)
        self.done.update(keys)

    def failed(self, key: str, error: str) -> int:
        self.ctx.attempts(self.stage).record(key, error)
        self.attempts[key] = self.attempts.get(key, 0) + 1
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
                session: StageSession | None = None) -> RunStats:
    session = session or StageSession(ctx, stage)
    if limit is not None:
        units = units[:limit]  # a stable prefix: rerunning with the same limit resumes the same slice
    done = session.done
    all_keys = {u.key for u in units}
    todo, seen = [], set()
    for u in units:
        if u.key not in done and u.key not in seen:
            seen.add(u.key)
            todo.append(u)
    stats = RunStats(stage, units_total=len(all_keys), units_skipped=len(all_keys & done))
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
```

- [ ] **Step 6: Show deferred and given-up units in the stage summary**

In `pipeline/cli.py`, `run_stage`, replace the `line = (...)` assignment with:

```python
    line = (f"{stats.stage}: ran {stats.units_run}, skipped {stats.units_skipped} of {stats.units_total} units; "
            f"rows {dict(sorted(stats.rows.items()))}")
    if stats.units_deferred or stats.units_gave_up:
        line += f"; deferred {stats.units_deferred}, gave up {stats.units_gave_up}"
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest -q`
Expected: PASS, 294 + 9 = 303 tests.

- [ ] **Step 8: Commit**

```bash
git add pipeline/journal.py pipeline/context.py pipeline/runner.py pipeline/cli.py tests/test_runner.py
git commit -m "m2: runner defers transiently failed units, gives up after five attempts, and locks a stage to one process"
```

---

### Task 2: GitHub clients for unattended runs

**Files:**
- Modify: `pipeline/gh.py`
- Test: `tests/test_gh.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `pipeline.gh.api_base() -> str` — `GITHUB_API_URL` or `https://api.github.com`, no trailing slash. All three clients use it.
  - `Pacer.scale: float` — read from `CENSUS_PACE` (default 1). Every sleep is multiplied by it; 0 disables pacing against a fake server.
  - `SearchClient.search(q, page=1, per_page=100) -> dict` — unchanged shape. Count queries (`per_page == 1`) return `items: []` and are the only ones cached. Stats gain `incomplete_retries`.
  - `GraphQLClient(token, transport=None, timeout=60.0, pacer=None, max_attempts=6, wall=time.time)`; `post(query) -> (status, body, latency_s)` returns status 0 for any transport error, timeouts included; `healthy() -> bool`.
  - `RestClient(token, transport=None, pacer=None, max_attempts=4, wall=time.time)`; `get(path, params=None) -> (status, body: dict)`; `.stats = {"requests", "rate_limited", "server_errors"}`. Status 0 or >= 500 means GitHub kept failing.

- [ ] **Step 1: Write the failing tests**

In `tests/test_gh.py`, replace the first import line with:

```python
from pipeline.gh import GraphQLClient, Pacer, RestClient, SearchClient, api_base, rate_limit_wait
from pipeline.jsonl import read_jsonl
```

Delete the later `from pipeline.gh import GraphQLClient  # noqa: E402` line.

In `test_search_retries_after_429_then_caches`, a count query now returns no items and the stats have one more key. Replace its two assertions on `body` and `client.stats` with:

```python
    assert body == {"total_count": 5, "incomplete_results": False, "items": []}
    assert client.stats == {"http_requests": 2, "cache_hits": 0, "rate_limited": 1, "server_errors": 0,
                            "incomplete_retries": 0}
```

Append:

```python
def still():
    return Pacer(base_interval=0.0, sleep=lambda s: None)


def test_only_counts_are_cached(tmp_path):
    cache = tmp_path / "c.jsonl"
    client = SearchClient("t", cache, pacer=still(),
                          transport=httpx.MockTransport(lambda req: httpx.Response(200, json=SEARCH_OK)))
    assert client.search("q", per_page=1)["items"] == []
    assert client.search("q", page=1, per_page=100)["items"][0]["repo"] == "o/r"
    client.search("q", page=1, per_page=100)
    assert [r["key"] for r in read_jsonl(cache)] == ["q|1|1"]
    assert (client.stats["http_requests"], client.stats["cache_hits"]) == (3, 0)


def test_incomplete_results_are_retried_and_never_cached(tmp_path):
    partial = {**SEARCH_OK, "incomplete_results": True, "total_count": 0}
    bodies = iter([partial, SEARCH_OK])
    cache = tmp_path / "c.jsonl"
    client = SearchClient("t", cache, pacer=still(),
                          transport=httpx.MockTransport(lambda req: httpx.Response(200, json=next(bodies))))
    assert client.search("q", per_page=1)["total_count"] == 5
    assert client.stats["incomplete_retries"] == 1
    stuck = SearchClient("t", tmp_path / "d.jsonl", pacer=still(),
                         transport=httpx.MockTransport(lambda req: httpx.Response(200, json=partial)))
    assert stuck.search("q", per_page=1)["incomplete_results"] is True
    assert stuck.stats["http_requests"] == 4  # one request and three retries
    assert read_jsonl(tmp_path / "d.jsonl") == []


def test_graphql_rate_limited_200_waits_for_reset_then_retries(clock):
    responses = iter([
        httpx.Response(200, json={"errors": [{"type": "RATE_LIMITED"}]}, headers={"x-ratelimit-reset": "1000"}),
        httpx.Response(200, json={"data": {"ok": 1}})])
    client = GraphQLClient("t", pacer=Pacer(base_interval=1.0, clock=clock.now, sleep=clock.sleep),
                           transport=httpx.MockTransport(lambda req: next(responses)), wall=lambda: 940.0)
    status, body, _ = client.post("query { viewer { login } }")
    assert (status, body) == (200, {"data": {"ok": 1}})
    assert clock.sleeps == [61.0] and client.stats["rate_limited"] == 1


def test_graphql_transport_error_is_status_zero():
    def down(req):
        raise httpx.ConnectError("down", request=req)

    client = GraphQLClient("t", pacer=still(), transport=httpx.MockTransport(down))
    status, body, _ = client.post("query { viewer { login } }")
    assert status == 0 and body["errors"][0]["type"] == "TRANSPORT"
    assert client.stats["server_errors"] == 1


def test_graphql_health_probe():
    up = GraphQLClient("t", pacer=still(), transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"data": {"rateLimit": {"remaining": 4999}}})))
    down = GraphQLClient("t", pacer=still(), transport=httpx.MockTransport(
        lambda req: httpx.Response(502, text="Bad Gateway")))
    assert up.healthy() is True and down.healthy() is False


def test_rest_get_returns_status_and_body_and_retries_5xx():
    responses = iter([httpx.Response(502, text="Bad Gateway"), httpx.Response(200, json={"tree": []})])
    client = RestClient("t", pacer=still(), transport=httpx.MockTransport(lambda req: next(responses)))
    assert client.get("/repos/o/r/git/trees/abc", {"recursive": "1"}) == (200, {"tree": []})
    assert client.stats == {"requests": 2, "rate_limited": 0, "server_errors": 1}


def test_rest_get_returns_404_without_retrying():
    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(404, json={"message": "Not Found"})

    client = RestClient("t", pacer=still(), transport=httpx.MockTransport(handler))
    assert client.get("/repos/o/r/git/trees/abc") == (404, {"message": "Not Found"})
    assert len(calls) == 1


def test_rest_get_gives_up_on_a_persistent_5xx():
    client = RestClient("t", pacer=still(), transport=httpx.MockTransport(lambda req: httpx.Response(503, text="x")))
    status, _ = client.get("/repos/o/r/git/trees/abc")
    assert status == 503 and client.stats["requests"] == 4


def test_rest_get_waits_out_a_rate_limit(clock):
    responses = iter([httpx.Response(429, headers={"retry-after": "30"}), httpx.Response(200, json={"ok": True})])
    client = RestClient("t", pacer=Pacer(base_interval=1.0, clock=clock.now, sleep=clock.sleep),
                        transport=httpx.MockTransport(lambda req: next(responses)))
    assert client.get("/x") == (200, {"ok": True})
    assert clock.sleeps == [30.0] and client.stats["rate_limited"] == 1


def test_api_base_follows_github_api_url(monkeypatch):
    monkeypatch.setenv("GITHUB_API_URL", "http://127.0.0.1:9/")
    assert api_base() == "http://127.0.0.1:9"
    assert str(RestClient("t").http.base_url).rstrip("/") == "http://127.0.0.1:9"


def test_census_pace_zero_disables_sleeping(monkeypatch, clock):
    monkeypatch.setenv("CENSUS_PACE", "0")
    p = Pacer(base_interval=6.0, clock=clock.now, sleep=clock.sleep)
    p.wait()
    p.wait()
    p.on_rate_limit(0.0)
    assert sum(clock.sleeps) == 0.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_gh.py -q`
Expected: FAIL at import with `ImportError: cannot import name 'RestClient'`.

- [ ] **Step 3: Implement**

In `pipeline/gh.py`:

Replace `API = "https://api.github.com"` with:

```python
INCOMPLETE_RETRIES = 3


def api_base() -> str:
    """GITHUB_API_URL points the clients at GitHub Enterprise, or at the fake server in the kill tests."""
    return os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")


def _pace() -> float:
    return float(os.environ.get("CENSUS_PACE", "1"))
```

Change the dataclass import to `from dataclasses import dataclass, field`. In `Pacer`, add the field `scale: float = field(default_factory=_pace)` after `_last`, extend the class docstring with `CENSUS_PACE scales every sleep; 0 disables pacing (tests against a fake server).`, and replace `wait` and `on_rate_limit` with:

```python
    def wait(self) -> None:
        if self._last is not None:
            due = self._last + self.base_interval * self.penalty * self.scale
            now = self.clock()
            if due > now:
                self.sleep(due - now)
        self._last = self.clock()

    def on_rate_limit(self, retry_after: float | None) -> None:
        """retry_after: seconds GitHub asked for; 0.0 = rate-limited with no hint; None = server error."""
        self.penalty = min(self.max_penalty, self.penalty * 2)
        floor = SECONDARY_FLOOR_S if retry_after == 0.0 else 0.0  # GitHub: wait >= 1 min when no hint
        self.sleep(max(retry_after or 0.0, floor, self.base_interval * self.penalty) * self.scale)
        self._last = None
```

Add below `rate_limit_wait`:

```python
def _wait_for_window(pacer: Pacer, resp: httpx.Response, wall: Callable[[], float]) -> None:
    """Window exhausted: wait for the reset instead of spending a request on a 403."""
    if resp.headers.get("x-ratelimit-remaining") == "0" and "x-ratelimit-reset" in resp.headers:
        pacer.sleep((max(0.0, float(resp.headers["x-ratelimit-reset"]) - wall()) + 1.0) * pacer.scale)
```

Replace `SearchClient` with:

```python
class SearchClient:
    """REST code search. Lattice counts (per_page == 1) are cached in JSONL, so a killed walk replays
    without requests. Result pages are not cached: the runner commits them with their node, and caching
    every page would hold the whole result set in memory at full scale."""

    def __init__(self, token: str, cache_path: Path, pacer: Pacer | None = None,
                 transport: httpx.BaseTransport | None = None, max_attempts: int = 10,
                 wall: Callable[[], float] = time.time) -> None:
        self.http = httpx.Client(base_url=api_base(), headers=_headers(token), timeout=60.0, transport=transport)
        self.pacer = pacer or Pacer()
        self.wall = wall
        self.cache_path = cache_path
        self.max_attempts = max_attempts
        self.cache = {r["key"]: r["body"] for r in read_jsonl(cache_path)}
        self.stats = {"http_requests": 0, "cache_hits": 0, "rate_limited": 0, "server_errors": 0,
                      "incomplete_retries": 0}

    def search(self, q: str, page: int = 1, per_page: int = 100) -> dict:
        key = f"{q}|{page}|{per_page}"
        if key in self.cache:
            self.stats["cache_hits"] += 1
            return self.cache[key]
        incomplete = 0
        for _ in range(self.max_attempts):
            self.pacer.wait()
            try:
                resp = self.http.get("/search/code", params={"q": q, "page": page, "per_page": per_page})
            except httpx.TransportError:
                self.stats["server_errors"] += 1
                self.pacer.on_rate_limit(None)
                continue
            self.stats["http_requests"] += 1
            wait = rate_limit_wait(resp)
            if wait is not None:
                self.stats["rate_limited"] += 1
                self.pacer.on_rate_limit(wait)
                continue
            if resp.status_code >= 500 or resp.status_code == 408:
                self.stats["server_errors"] += 1
                self.pacer.on_rate_limit(None)
                continue
            resp.raise_for_status()
            raw = resp.json()
            self.pacer.on_success()
            _wait_for_window(self.pacer, resp, self.wall)
            if raw["incomplete_results"] and incomplete < INCOMPLETE_RETRIES:
                incomplete += 1  # GitHub timed out its own search: the count or the page may be short
                self.stats["incomplete_retries"] += 1
                continue
            body = {
                "total_count": raw["total_count"],
                "incomplete_results": raw["incomplete_results"],
                "items": [] if per_page == 1 else [
                    {"repo": it["repository"]["full_name"], "fork": it["repository"]["fork"],
                     "path": it["path"], "sha": it["sha"]}
                    for it in raw["items"]
                ],
            }
            if per_page == 1 and not body["incomplete_results"]:
                self.cache[key] = body
                append_jsonl(self.cache_path, {"key": key, "body": body})
            return body
        raise RuntimeError(f"code search failed after {self.max_attempts} attempts: {key}")
```

Replace `GraphQLClient` with:

```python
class GraphQLClient:
    def __init__(self, token: str, transport: httpx.BaseTransport | None = None, timeout: float = 60.0,
                 pacer: Pacer | None = None, max_attempts: int = 6, wall: Callable[[], float] = time.time) -> None:
        self.http = httpx.Client(base_url=api_base(), headers=_headers(token), timeout=timeout, transport=transport)
        self.pacer = pacer or Pacer(base_interval=1.0)
        self.max_attempts = max_attempts
        self.wall = wall
        self.stats = {"posts": 0, "rate_limited": 0, "server_errors": 0, "latency_s": 0.0}

    def post(self, query: str) -> tuple[int, dict, float]:
        """Returns (status, body, latency_s). Status 0 is a transport error or a client timeout.

        A 5xx or a transport error is returned after a backoff (the caller shrinks the batch); it never
        counts as success. Rate limits are waited out here, including the primary limit, which GitHub
        reports as HTTP 200 with a RATE_LIMITED error.
        """
        for _ in range(self.max_attempts):
            self.pacer.wait()
            t0 = time.monotonic()
            try:
                resp = self.http.post("/graphql", json={"query": query})
            except httpx.TransportError as e:
                self.stats["server_errors"] += 1
                self.pacer.on_rate_limit(None)
                return 0, {"errors": [{"type": "TRANSPORT", "message": str(e)[:300]}]}, time.monotonic() - t0
            latency = time.monotonic() - t0
            self.stats["posts"] += 1
            self.stats["latency_s"] += latency
            wait = rate_limit_wait(resp)
            if wait is not None:
                self.stats["rate_limited"] += 1
                self.pacer.on_rate_limit(wait)
                continue
            try:
                body = resp.json()
            except ValueError:
                body = None
            if not isinstance(body, dict):
                body = {"errors": [{"type": "HTTP", "message": resp.text[:300]}]}
            if resp.status_code == 200 and any(e.get("type") == "RATE_LIMITED" for e in body.get("errors") or []):
                self.stats["rate_limited"] += 1
                reset = resp.headers.get("x-ratelimit-reset")
                self.pacer.on_rate_limit(max(0.0, float(reset) - self.wall()) + 1.0 if reset else 0.0)
                continue
            if resp.status_code >= 500:
                self.stats["server_errors"] += 1
                self.pacer.on_rate_limit(None)
            else:
                self.pacer.on_success()
            return resp.status_code, body, latency
        raise RuntimeError(f"graphql rate-limited on all {self.max_attempts} attempts")

    def healthy(self) -> bool:
        """One cheap query. It tells a GitHub outage (stop the stage) from a repo GitHub cannot serve."""
        status, body, _ = self.post("query { rateLimit { remaining } }")
        return status == 200 and bool((body.get("data") or {}).get("rateLimit"))


class RestClient:
    """Paced REST GETs (git trees). 0.75 s apart keeps one caller under the 5,000 requests/hour core limit."""

    def __init__(self, token: str, transport: httpx.BaseTransport | None = None, pacer: Pacer | None = None,
                 max_attempts: int = 4, wall: Callable[[], float] = time.time) -> None:
        self.http = httpx.Client(base_url=api_base(), headers=_headers(token), timeout=60.0, transport=transport)
        self.pacer = pacer or Pacer(base_interval=0.75)
        self.max_attempts = max_attempts
        self.wall = wall
        self.stats = {"requests": 0, "rate_limited": 0, "server_errors": 0}

    def get(self, path: str, params: dict | None = None) -> tuple[int, dict]:
        """(status, body). Rate limits are waited out here. Status 0 or >= 500 means GitHub kept failing."""
        status, body = 0, {"message": "no attempt"}
        for _ in range(self.max_attempts):
            self.pacer.wait()
            try:
                resp = self.http.get(path, params=params)
            except httpx.TransportError as e:
                self.stats["server_errors"] += 1
                self.pacer.on_rate_limit(None)
                status, body = 0, {"message": str(e)[:300]}
                continue
            self.stats["requests"] += 1
            wait = rate_limit_wait(resp)
            if wait is not None:
                self.stats["rate_limited"] += 1
                self.pacer.on_rate_limit(wait)
                status, body = 0, {"message": "rate limited"}
                continue
            try:
                parsed = resp.json()
            except ValueError:
                parsed = None
            status = resp.status_code
            body = parsed if isinstance(parsed, dict) else {"message": resp.text[:300]}
            if status >= 500:
                self.stats["server_errors"] += 1
                self.pacer.on_rate_limit(None)
                continue
            self.pacer.on_success()
            _wait_for_window(self.pacer, resp, self.wall)
            return status, body
        return status, body
```

The old `GraphQLClient` kept an unused `self.timeout`; it is gone.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q`
Expected: every test in `tests/test_gh.py` passes (11 new). `tests/test_s2_harvest.py::test_client_timeout_maps_to_status_zero_and_shrinks` still passes: the client now maps the timeout to status 0 itself. Total 314.

- [ ] **Step 5: Commit**

```bash
git add pipeline/gh.py tests/test_gh.py
git commit -m "m2: REST client, GraphQL health probe and RATE_LIMITED handling, count-only search cache, incomplete-result retries"
```

---

### Task 3: S2 harvest — REST tree, deferral, final pass

Second half of deferred M1 fix 1 for S2. M1 lost 2.6% of repos to depth-4 tree timeouts and cut subtrees in 15.6%. This task removes the deep tree from GraphQL and stops recording transient failures as `missing`.

**Files:**
- Modify: `pipeline/s2_harvest.py` (whole file except `_run_fixtures`)
- Test: `tests/test_s2_harvest.py` (whole file)

**Interfaces:**
- Consumes: `Partial`, `StopStage`, `Unit`, `run_batched(..., give_up=)`, `write_state` (Task 1); `GraphQLClient.post/healthy`, `RestClient.get` (Task 2).
- Produces:
  - `s2_harvest.VERSION = 2`, `MAX_EXTRA = 20`, `EXTRA_BATCH = 60`.
  - `harvest_batch(ctx, gql, rest, units: list[Unit], final: bool = False) -> Partial`. A unit's payload is `(repo: str, nested_claude_md_paths: list[str])`.
  - `repos.error` values: `not_found` (final, `missing=True`); `unreachable:<error>` (final after five attempts, `missing=True`); `partial:<error>` (final after five attempts, `missing=False`, some files or blobs not reachable).
  - `repos.tree_truncated`: 0 = the whole `.claude` tree is listed; 1 = GitHub truncated the REST listing; n = the final pass could not list n subdirectories.
  - `harness_files.skip_reason` values are unchanged: `not_fetched_kind`, `binary`, `too_large`, `truncated`, `missing`, `fetch_error` (now only written by the final pass).

- [ ] **Step 1: Replace `tests/test_s2_harvest.py` with the tests below**

```python
import json
import re

import httpx
import pytest

from pipeline import s2_harvest as s2
from pipeline.context import Opts
from pipeline.gh import GraphQLClient, Pacer, RestClient
from pipeline.runner import MAX_ATTEMPTS, StopStage, Unit, write_state

ALIAS = re.compile(r'repository\(owner: "([^"]+)", name: "([^"]+)"\)')
EXPR = re.compile(r'(f\d+): object\(expression: "HEAD:([^"]+)"\)')
OID = re.compile(r'(b\d+): object\(oid: "([0-9a-f]{40})"\)')
SECRET = "Zq8Xv2Lm9Pw4Rt7Ky3Nb"
DEEP = ".claude/skills/x/scripts/lib/util.py"


def pacer():
    return Pacer(base_interval=0.0, sleep=lambda s: None)


def blob(name, oid, size=10):
    return {"name": name, "type": "blob", "oid": oid, "object": {"byteSize": size, "isBinary": False}}


def subdir(name):
    return {"name": name, "type": "tree", "oid": "e" * 40, "object": {}}


def node(entries=None):
    return {"nameWithOwner": "x", "stargazerCount": 3, "isFork": False, "isTemplate": False,
            "createdAt": "2025-01-01T00:00:00Z", "pushedAt": "2026-01-01T00:00:00Z",
            "primaryLanguage": {"name": "Python"}, "licenseInfo": None,
            "defaultBranchRef": {"target": {"oid": "f" * 40}},
            "claude": {"oid": "d" * 40, "entries": entries} if entries is not None else None}


def unit(repo, extra=()):
    return Unit(f"k:{repo}", (repo, list(extra)))


class Hub:
    """A scripted GitHub. repos: repo -> meta node (None = deleted). paths: (repo, path) -> blob oid for a
    HEAD:<path> lookup. texts: blob oid -> text, or a dict returned as the blob, or None for a null blob.
    trees: repo -> [(path under .claude, oid, size)] for the REST call; a repo not listed answers 502."""

    def __init__(self, repos, paths=None, texts=None, trees=None):
        self.repos, self.paths, self.texts, self.trees = repos, paths or {}, texts or {}, trees or {}
        self.healthy = True
        self.fail_meta: dict[str, int] = {}  # repo -> HTTP status for any meta query that names it
        self.fail_blobs: set[str] = set()  # a blob query naming one of these oids answers 502
        self.not_found_blobs: set[str] = set()
        self.truncated: set[str] = set()  # repos whose REST tree comes back truncated
        self.meta_sizes: list[int] = []
        self.path_queries: list[str] = []
        self.blob_queries: list[str] = []
        self.tree_calls: list[str] = []

    def gql(self, req):
        q = json.loads(req.content)["query"]
        names = [f"{o}/{n}" for o, n in ALIAS.findall(q)]
        if not names:  # the health probe
            return httpx.Response(200 if self.healthy else 502, json={"data": {"rateLimit": {"remaining": 1}}})
        if not self.healthy:
            return httpx.Response(502, text="Bad Gateway")
        is_meta = "HEAD:.claude" in q
        if is_meta:
            self.meta_sizes.append(len(names))
            bad = [self.fail_meta[n] for n in names if n in self.fail_meta]
            if bad:
                return httpx.Response(bad[0], text="Bad Gateway")
        elif OID.search(q):
            self.blob_queries.append(q)
            if self.fail_blobs & {o for _, o in OID.findall(q)}:
                return httpx.Response(502, text="Bad Gateway")
        else:
            self.path_queries.append(q)
        data, errors = {}, []
        for i, (name, block) in enumerate(zip(names, q.split("repository(")[1:])):
            if is_meta and self.repos[name] is None:
                data[f"r{i}"] = None
                errors.append({"type": "NOT_FOUND", "path": [f"r{i}"]})
                continue
            r = dict(self.repos[name]) if is_meta else {}
            for alias, path in EXPR.findall(block):
                oid = self.paths.get((name, path))
                r[alias] = {"oid": oid, "byteSize": 20, "isBinary": False} if oid else None
            for alias, oid in OID.findall(block):
                text = self.texts.get(oid)
                if isinstance(text, str):
                    r[alias] = {"oid": oid, "isBinary": False, "isTruncated": False, "text": text}
                else:
                    r[alias] = text
                    if oid in self.not_found_blobs:
                        errors.append({"type": "NOT_FOUND", "path": [f"r{i}", alias]})
            data[f"r{i}"] = r
        return httpx.Response(200, json={"data": data, **({"errors": errors} if errors else {})})

    def rest(self, req):
        repo = re.fullmatch(r"/repos/(.+)/git/trees/[0-9a-f]{40}", req.url.path)[1]
        self.tree_calls.append(repo)
        if repo not in self.trees:
            return httpx.Response(502, text="Bad Gateway")
        tree = [{"path": p, "type": "blob", "sha": oid, "size": size} for p, oid, size in self.trees[repo]]
        tree.append({"path": "skills", "type": "tree", "sha": "e" * 40})
        return httpx.Response(200, json={"sha": "d" * 40, "tree": tree, "truncated": repo in self.truncated})

    def clients(self):
        return (GraphQLClient("t", transport=httpx.MockTransport(self.gql), pacer=pacer()),
                RestClient("t", transport=httpx.MockTransport(self.rest), pacer=pacer()))


def harvest(ctx, hub, units, final=False):
    return s2.harvest_batch(ctx, *hub.clients(), units, final=final)


def tree_hub():
    skill = "b" * 40
    return Hub({"o/a": node([blob("settings.json", "a" * 40), subdir("skills")])},
               texts={"a" * 40: "{}\n", skill: "---\nname: x\n---\nbody\n"},
               trees={"o/a": [("settings.json", "a" * 40, 3), ("skills/x/SKILL.md", skill, 20),
                              ("skills/x/scripts/lib/util.py", "c" * 40, 5)]})


def test_meta_query_lists_only_the_top_of_dot_claude_and_caps_nested_paths():
    extra = [f"pkg{i}/CLAUDE.md" for i in range(s2.MAX_EXTRA + 5)]
    q = s2.meta_query([("o/r", extra)])
    assert q.count("... on Tree") == 1
    assert '"HEAD:pkg0/CLAUDE.md"' in q and '"HEAD:.mcp.json"' in q
    assert f'"HEAD:pkg{s2.MAX_EXTRA}/CLAUDE.md"' not in q


def test_deleted_repo_is_final_and_the_rest_of_the_batch_is_kept(ctx):
    hub = Hub({"gone/repo": None, "o/r": node([blob("settings.json", "a" * 40)])},
              paths={("o/r", "CLAUDE.md"): "b" * 40}, texts={"a" * 40: "{}\n", "b" * 40: "# hi\n"})
    out = harvest(ctx, hub, [unit("gone/repo"), unit("o/r")])
    assert out.deferred == {}
    assert out.rows["repos"][0] == {"repo": "gone/repo", "missing": True, "error": "not_found", "canary": False}
    assert out.rows["repos"][1]["head_oid"] == "f" * 40 and out.rows["repos"][1]["tree_truncated"] == 0
    assert sorted(f["path"] for f in out.rows["harness_files"]) == [".claude/settings.json", "CLAUDE.md"]
    assert hub.tree_calls == []  # no subdirectories, so no REST call


def test_null_repo_without_not_found_is_transient():
    body = {"data": {"r0": None, "r1": None},
            "errors": [{"type": "NOT_FOUND", "path": ["r0"]}, {"type": "RESOURCE_LIMITS_EXCEEDED", "path": ["r1"]}]}
    found, failed = s2.parse_meta([("a/gone", []), ("b/slow", [])], body)
    assert found["a/gone"].row["error"] == "not_found"
    assert failed == {"b/slow": "graphql_error:RESOURCE_LIMITS_EXCEEDED"}
    _, failed = s2.parse_meta([("c/x", [])], {"data": {"r0": None}})
    assert failed == {"c/x": "graphql_error:unknown"}


def test_whole_batch_failure_halves_until_it_fits(ctx):
    hub = Hub({"o/a": node(), "o/b": node()})
    real, sizes = hub.gql, []

    def flaky(req):
        q = json.loads(req.content)["query"]
        n = len(ALIAS.findall(q))
        if "HEAD:.claude" in q:
            sizes.append(n)
            if n > 1:
                return httpx.Response(502, text="Bad Gateway")
        return real(req)

    hub.gql = flaky
    out = harvest(ctx, hub, [unit("o/a"), unit("o/b")])
    assert sizes == [2, 1, 1] and out.deferred == {}
    assert [r["repo"] for r in out.rows["repos"]] == ["o/a", "o/b"]


def test_client_timeout_halves_the_batch(ctx):
    hub = Hub({"o/a": node(), "o/b": node()})
    real = hub.gql

    def slow(req):
        q = json.loads(req.content)["query"]
        if "HEAD:.claude" in q and len(ALIAS.findall(q)) > 1:
            raise httpx.ReadTimeout("slow", request=req)
        return real(req)

    hub.gql = slow
    out = harvest(ctx, hub, [unit("o/a"), unit("o/b")])
    assert [r["repo"] for r in out.rows["repos"]] == ["o/a", "o/b"] and out.deferred == {}


def test_repo_github_cannot_serve_is_deferred_not_recorded_missing(ctx):
    hub = Hub({"o/a": node(), "o/b": node()})
    hub.fail_meta["o/a"] = 502
    out = harvest(ctx, hub, [unit("o/a"), unit("o/b")])
    assert out.deferred == {"k:o/a": "graphql_502"}
    assert [r["repo"] for r in out.rows["repos"]] == ["o/b"]


def test_outage_stops_the_stage_instead_of_deferring(ctx):
    hub = Hub({"o/a": node()})
    hub.healthy = False
    with pytest.raises(StopStage, match="GitHub GraphQL is failing"):
        harvest(ctx, hub, [unit("o/a")])


def test_final_pass_records_an_unreachable_repo(ctx):
    hub = Hub({"o/a": node()})
    hub.fail_meta["o/a"] = 504
    out = harvest(ctx, hub, [unit("o/a")], final=True)
    assert out.deferred == {}
    assert out.rows["repos"] == [{"repo": "o/a", "missing": True, "error": "unreachable:graphql_504",
                                  "canary": False}]


def test_rest_lists_the_whole_tree_at_any_depth(ctx):
    hub = tree_hub()
    out = harvest(ctx, hub, [unit("o/a")])
    files = {f["path"]: f for f in out.rows["harness_files"]}
    assert set(files) == {".claude/settings.json", ".claude/skills/x/SKILL.md", DEEP}
    assert (files[DEEP]["kind"], files[DEEP]["skip_reason"]) == ("skill_file", "not_fetched_kind")
    assert files[".claude/skills/x/SKILL.md"]["fetched"]
    assert out.rows["repos"][0]["tree_truncated"] == 0 and hub.tree_calls == ["o/a"]


def test_truncated_rest_tree_is_recorded(ctx):
    hub = tree_hub()
    hub.truncated.add("o/a")
    assert harvest(ctx, hub, [unit("o/a")]).rows["repos"][0]["tree_truncated"] == 1


def test_rest_tree_failure_defers_then_the_final_pass_keeps_the_top_level(ctx):
    hub = tree_hub()
    hub.trees = {}
    out = harvest(ctx, hub, [unit("o/a")])
    assert out.deferred == {"k:o/a": "rest_tree_502"} and out.rows["repos"] == []
    last = harvest(ctx, hub, [unit("o/a")], final=True)
    repo = last.rows["repos"][0]
    assert (repo["missing"], repo["error"], repo["tree_truncated"]) == (False, "partial:rest_tree_502", 1)
    assert [f["path"] for f in last.rows["harness_files"]] == [".claude/settings.json"]


def test_every_tree_call_failing_stops_the_stage(ctx):
    hub = Hub({f"o/r{i}": node([subdir("skills")]) for i in range(3)})
    with pytest.raises(StopStage, match="REST tree"):
        harvest(ctx, hub, [unit(f"o/r{i}") for i in range(3)])


def test_nested_paths_beyond_the_cap_go_in_follow_up_queries(ctx):
    extra = [f"pkg{i:03d}/CLAUDE.md" for i in range(s2.MAX_EXTRA + s2.EXTRA_BATCH + 1)]
    hub = Hub({"o/mono": node()}, paths={("o/mono", p): f"{i:040x}" for i, p in enumerate(extra)},
              texts={f"{i:040x}": f"# pkg {i}\n" for i in range(len(extra))})
    out = harvest(ctx, hub, [unit("o/mono", extra)])
    assert len(hub.path_queries) == 2
    assert sorted(f["path"] for f in out.rows["harness_files"]) == extra
    assert all(f["fetched"] for f in out.rows["harness_files"])


def test_plan_blob_batches_respects_count_and_bytes():
    wanted = [{"repo": "o/r", "oid": f"{i:040x}", "size": 150_000} for i in range(5)]
    assert [len(b) for b in s2.plan_blob_batches(wanted)] == [2, 2, 1]


def test_each_new_blob_is_fetched_once_and_stored_redacted(ctx):
    shared, stored, secret_oid = "a" * 40, "b" * 40, "c" * 40
    ctx.blobs.put(stored, "already here\n")
    hub = Hub({"o/a": node([blob("settings.json", shared), blob("settings.local.json", secret_oid)]),
               "o/b": node([blob("settings.json", shared)])},
              paths={("o/b", "CLAUDE.md"): stored},
              texts={shared: '{"model": "sonnet"}\n', secret_oid: '{"env": {"CLOUD_API_KEY": "%s"}}\n' % SECRET})
    out = harvest(ctx, hub, [unit("o/a"), unit("o/b")])
    assert sorted(o for _, o in OID.findall("".join(hub.blob_queries))) == sorted([shared, secret_oid])
    assert len(out.rows["harness_files"]) == 4 and all(f["fetched"] for f in out.rows["harness_files"])
    assert SECRET not in ctx.blobs.get(secret_oid)
    assert out.rows["redactions"] == [{"blob_sha": secret_oid, "rule": "assigned_secret", "n": 1}]


def test_oversized_and_non_harness_files_are_recorded_not_fetched(ctx):
    hub = Hub({"o/a": node([blob("notes.txt", "d" * 40), blob("settings.json", "e" * 40, size=300_000)])})
    out = harvest(ctx, hub, [unit("o/a")])
    reasons = {f["path"]: f["skip_reason"] for f in out.rows["harness_files"]}
    assert reasons == {".claude/notes.txt": "not_fetched_kind", ".claude/settings.json": "too_large"}
    assert not any(f["fetched"] for f in out.rows["harness_files"]) and hub.blob_queries == []


def test_final_blob_statuses_reach_skip_reason(ctx):
    bin_, trunc, gone = "1" * 40, "2" * 40, "3" * 40
    hub = Hub({"o/a": node([blob("settings.json", bin_), blob("settings.local.json", trunc)])},
              paths={("o/a", "CLAUDE.md"): gone},
              texts={bin_: {"oid": bin_, "isBinary": True, "isTruncated": False, "text": None},
                     trunc: {"oid": trunc, "isBinary": False, "isTruncated": True, "text": "x"}, gone: None})
    hub.not_found_blobs.add(gone)
    out = harvest(ctx, hub, [unit("o/a")])
    reasons = {f["path"]: f["skip_reason"] for f in out.rows["harness_files"]}
    assert reasons == {".claude/settings.json": "binary", ".claude/settings.local.json": "truncated",
                       "CLAUDE.md": "missing"}
    assert out.deferred == {} and not any(f["fetched"] for f in out.rows["harness_files"])


def test_transient_blob_failure_defers_the_repo_and_the_final_pass_records_it(ctx):
    lost = "4" * 40
    hub = Hub({"o/a": node([blob("settings.json", lost)]), "o/b": node([blob("settings.json", "a" * 40)])},
              texts={"a" * 40: "{}\n", lost: None})
    out = harvest(ctx, hub, [unit("o/a"), unit("o/b")])
    assert out.deferred == {"k:o/a": "blob_fetch_error"}
    assert [r["repo"] for r in out.rows["repos"]] == ["o/b"]
    assert {f["repo"] for f in out.rows["harness_files"]} == {"o/b"}
    last = harvest(ctx, hub, [unit("o/a")], final=True)
    assert (last.rows["repos"][0]["missing"], last.rows["repos"][0]["error"]) == (False, "partial:blob_fetch_error")
    assert [(f["skip_reason"], f["fetched"]) for f in last.rows["harness_files"]] == [("fetch_error", False)]


def test_blob_query_failure_halves_and_still_stores_both(ctx):
    a, b = "a" * 40, "b" * 40
    hub = Hub({}, texts={a: "one\n", b: "two\n"})
    real, seen = hub.gql, []

    def flaky(req):
        oids = OID.findall(json.loads(req.content)["query"])
        if oids:
            seen.append(len(oids))
        if len(oids) > 1:
            return httpx.Response(502, text="Bad Gateway")
        return real(req)

    hub.gql = flaky
    blobs = [{"repo": "o/r", "oid": a, "size": 4}, {"repo": "o/r", "oid": b, "size": 4}]
    status = s2.fetch_blobs(hub.clients()[0], blobs, ctx.blobs)
    assert seen == [2, 1, 1] and status == {a: "", b: ""}
    assert ctx.blobs.get(a) == "one\n" and ctx.blobs.get(b) == "two\n"


def test_redaction_counts_survive_a_kill_after_blob_write(ctx):
    oid = "c" * 40
    ctx.blobs.put(oid, '{"env": {"CLOUD_API_KEY": "%s"}}\n' % SECRET)  # killed before the commit
    hub = Hub({"o/a": node([blob("settings.json", oid)])})
    out = harvest(ctx, hub, [unit("o/a")])
    assert hub.blob_queries == []
    assert out.rows["redactions"] == [{"blob_sha": oid, "rule": "assigned_secret", "n": 1}]


def test_fixture_mode_stores_redacted_blobs(fctx):
    s2.run(fctx, Opts())
    assert len(fctx.tables.read("repos")) == 8
    files = fctx.tables.read("harness_files")
    assert all(fctx.blobs.has(f["blob_sha"]) for f in files if f["fetched"])
    assert not any(SECRET in fctx.blobs.get(f["blob_sha"]) for f in files if f["fetched"])
    assert s2.run(fctx, Opts()).units_skipped == 1


def seed_hits(ctx, hits):
    ctx.tables.write_part("repo_hits", "p-seed", [
        {"repo": r, "path": p, "component": "claude_md", "query_id": "q", "blob_sha": "s", "is_fork": False}
        for r, p in hits])
    write_state(ctx, "s1", {"families_done": ["claude_md/nonfork", "claude_md/fork"], "complete": True})


def wire(monkeypatch, hub):
    gql, rest = hub.clients()
    monkeypatch.setattr(s2, "GraphQLClient", lambda token: gql)
    monkeypatch.setattr(s2, "RestClient", lambda token: rest)
    monkeypatch.setattr(s2, "github_token", lambda: "t")


def test_run_defers_then_harvests_on_a_later_run(ctx, monkeypatch):
    hub = Hub({"o/a": node(), "o/b": node()},
              paths={("o/a", "CLAUDE.md"): "a" * 40, ("o/b", "CLAUDE.md"): "b" * 40,
                     ("o/b", "docs/CLAUDE.md"): "c" * 40},
              texts={"a" * 40: "# a\n", "b" * 40: "# b\n", "c" * 40: "# c\n"})
    hub.fail_meta["o/a"] = 502
    seed_hits(ctx, [("o/a", "CLAUDE.md"), ("o/b", "CLAUDE.md"), ("o/b", "docs/CLAUDE.md")])
    wire(monkeypatch, hub)
    stats = s2.run(ctx, Opts())
    assert (stats.units_run, stats.units_deferred) == (1, 1) and "deferred" in stats.stopped
    assert [r["repo"] for r in ctx.tables.read("repos")] == ["o/b"]
    assert sorted(f["path"] for f in ctx.tables.read("harness_files")) == ["CLAUDE.md", "docs/CLAUDE.md"]
    hub.fail_meta.clear()
    stats = s2.run(ctx, Opts())
    assert (stats.units_run, stats.stopped) == (1, None)
    assert sorted(r["repo"] for r in ctx.tables.read("repos")) == ["o/a", "o/b"]
    assert len(ctx.tables.read("harness_files")) == 3


def test_run_gives_up_after_max_attempts_and_records_why(ctx, monkeypatch):
    hub = Hub({"o/a": node()})
    hub.fail_meta["o/a"] = 502
    seed_hits(ctx, [("o/a", "CLAUDE.md")])
    wire(monkeypatch, hub)
    for _ in range(MAX_ATTEMPTS - 1):
        assert s2.run(ctx, Opts()).units_deferred == 1
    stats = s2.run(ctx, Opts())
    assert (stats.units_gave_up, stats.stopped) == (1, None)
    row = ctx.tables.read("repos")[0]
    assert (row["repo"], row["missing"], row["error"]) == ("o/a", True, "unreachable:graphql_502")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_s2_harvest.py -q`
Expected: FAIL. Most tests fail with `TypeError: harvest_batch() got an unexpected keyword argument 'final'`; the first fails with `AttributeError: module 'pipeline.s2_harvest' has no attribute 'MAX_EXTRA'`.

- [ ] **Step 3: Rewrite `pipeline/s2_harvest.py`**

Keep `_run_fixtures` exactly as it is. Replace everything else with:

```python
"""S2 harvest (PRD §4 S2). Repo metadata and the top level of `.claude` come from batched GraphQL (25 repos,
halved on a whole-batch failure). A repo whose `.claude` has subdirectories gets one REST git-trees call,
which lists the whole tree at any depth. Blobs not yet stored are fetched by batched GraphQL and redacted
on write.

Nothing transient is recorded as final. A repo GitHub could not serve is deferred (runner.Partial) and tried
again on later runs. After runner.MAX_ATTEMPTS the final pass records what is reachable and why the rest is
not. When GitHub itself is failing (the health probe fails too), the stage stops instead.
"""
import json
from dataclasses import dataclass

from pipeline.context import Ctx, Opts
from pipeline.fixtures import fixture_files, fixture_repos, git_blob_sha
from pipeline.gh import GraphQLClient, RestClient, github_token
from pipeline.journal import unit_key
from pipeline.kinds import PARSED_KINDS, classify
from pipeline.runner import Partial, RunStats, StopStage, Unit, run_batched, run_whole
from pipeline.store import BlobStore

VERSION = 2  # 2: the .claude tree comes from REST git trees (was GraphQL nested to depth 4); transients are deferred
BATCH = 25
ROOT_FILES = ["CLAUDE.md", ".mcp.json", ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json"]
MAX_EXTRA = 20  # nested CLAUDE.md paths looked up in the meta query; the rest go in follow-up queries
EXTRA_BATCH = 60
MAX_FETCH_BYTES = 200_000
BLOB_BATCH = 40
BLOB_BATCH_BYTES = 400_000
REPO_FIELDS = ("nameWithOwner stargazerCount isFork isTemplate createdAt pushedAt "
               "primaryLanguage { name } licenseInfo { spdxId } defaultBranchRef { target { oid } }")
BLOB_META = "... on Blob { oid byteSize isBinary }"
CLAUDE_TOP = ('claude: object(expression: "HEAD:.claude") { ... on Tree { oid entries { name type oid '
              'object { ... on Blob { byteSize isBinary } } } } }')


@dataclass
class Found:
    row: dict  # the repos row
    files: list[dict]  # {path, oid, size, binary}
    tree_oid: str | None = None  # the .claude tree, when it has subdirectories still to list
    subdirs: int = 0


def _repo_args(repo: str) -> str:
    owner, name = repo.split("/", 1)
    return f"owner: {json.dumps(owner)}, name: {json.dumps(name)}"


def _path_fields(paths: list[str]) -> str:
    return " ".join(f"f{j}: object(expression: {json.dumps('HEAD:' + p)}) {{ {BLOB_META} }}"
                    for j, p in enumerate(paths))


def meta_query(batch: list[tuple[str, list[str]]]) -> str:
    parts = [f"r{i}: repository({_repo_args(repo)}) {{ {REPO_FIELDS} {CLAUDE_TOP} "
             f"{_path_fields(ROOT_FILES + extra[:MAX_EXTRA])} }}" for i, (repo, extra) in enumerate(batch)]
    return "query { rateLimit { cost remaining } " + " ".join(parts) + " }"


def _not_found(body: dict) -> set[tuple]:
    """GraphQL error paths of type NOT_FOUND, e.g. ("r3",) or ("r0", "b2")."""
    return {tuple(e["path"]) for e in body.get("errors") or [] if e.get("type") == "NOT_FOUND" and e.get("path")}


def _error_type(body: dict, alias: str) -> str:
    for e in body.get("errors") or []:
        if e.get("path") and e["path"][0] == alias:
            return e.get("type") or "unknown"
    return "unknown"


def _missing(repo: str, error: str) -> dict:
    return {"repo": repo, "missing": True, "error": error, "canary": False}


def parse_meta(batch: list[tuple[str, list[str]]], body: dict) -> tuple[dict[str, Found], dict[str, str]]:
    """(repos GitHub answered for, {repo: error} for repos it did not). Only NOT_FOUND is final."""
    data = body.get("data") or {}
    gone = _not_found(body)
    found: dict[str, Found] = {}
    failed: dict[str, str] = {}
    for i, (repo, extra) in enumerate(batch):
        r = data.get(f"r{i}")
        if r is None:
            if (f"r{i}",) in gone:
                found[repo] = Found(_missing(repo, "not_found"), [])
            else:
                failed[repo] = f"graphql_error:{_error_type(body, f'r{i}')}"
            continue
        claude = r.get("claude") or {}
        files, subdirs = [], 0
        for e in claude.get("entries") or []:
            if e["type"] == "blob":
                obj = e.get("object") or {}
                files.append({"path": f".claude/{e['name']}", "oid": e["oid"], "size": obj.get("byteSize"),
                              "binary": obj.get("isBinary")})
            elif e["type"] == "tree":
                subdirs += 1
        for j, p in enumerate(ROOT_FILES + extra[:MAX_EXTRA]):
            b = r.get(f"f{j}")
            if b:
                files.append({"path": p, "oid": b["oid"], "size": b["byteSize"], "binary": b["isBinary"]})
        row = {
            "repo": repo, "missing": False, "error": None, "canary": False,
            "stars": r.get("stargazerCount"), "is_fork": r.get("isFork"), "is_template": r.get("isTemplate"),
            "created_at": r.get("createdAt"), "pushed_at": r.get("pushedAt"),
            "language": (r.get("primaryLanguage") or {}).get("name"),
            "license": (r.get("licenseInfo") or {}).get("spdxId"),
            "head_oid": ((r.get("defaultBranchRef") or {}).get("target") or {}).get("oid"),
            "tree_truncated": 0,
        }
        found[repo] = Found(row, files, claude.get("oid") if subdirs else None, subdirs)
    return found, failed


def is_retryable(status: int, body: dict) -> bool:
    if status in (0, 502, 503, 504):
        return True
    # A whole-batch failure (GitHub's 10 s execution limit, resource limits) returns data null with errors.
    return not body.get("data") and bool(body.get("errors"))


def _post(client: GraphQLClient, query: str) -> tuple[int, dict]:
    status, body, _ = client.post(query)
    if status == 401:
        raise SystemExit("GitHub token rejected (401)")
    return status, body


def _stop_if_down(client: GraphQLClient, what: str, status: int) -> None:
    if not client.healthy():
        raise StopStage(f"GitHub GraphQL is failing ({what} query: status {status}); "
                        "stopped so nothing transient is recorded")


def _halves(items: list) -> tuple[list, list]:
    mid = len(items) // 2
    return items[:mid], items[mid:]


def fetch_meta(client: GraphQLClient, batch: list[tuple[str, list[str]]]) -> tuple[dict[str, Found], dict[str, str]]:
    status, body = _post(client, meta_query(batch))
    if is_retryable(status, body):
        _stop_if_down(client, "meta", status)
        if len(batch) > 1:
            a, b = (fetch_meta(client, half) for half in _halves(batch))
            return {**a[0], **b[0]}, {**a[1], **b[1]}
        return {}, {batch[0][0]: f"graphql_{status}"}
    return parse_meta(batch, body)


def _group(pairs: list[tuple[str, str]]) -> list[tuple[str, list[str]]]:
    by_repo: dict[str, list[str]] = {}
    for repo, item in pairs:
        by_repo.setdefault(repo, []).append(item)
    return list(by_repo.items())


def fetch_extra(client: GraphQLClient, wanted: list[tuple[str, str]]) -> tuple[list[dict], dict[str, str]]:
    """Blob metadata for (repo, path) pairs. Returns (file entries carrying `repo`, {repo: error})."""
    items = _group(wanted)
    query = "query { " + " ".join(f"r{i}: repository({_repo_args(repo)}) {{ {_path_fields(paths)} }}"
                                  for i, (repo, paths) in enumerate(items)) + " }"
    status, body = _post(client, query)
    if is_retryable(status, body):
        _stop_if_down(client, "paths", status)
        if len(wanted) > 1:
            a, b = (fetch_extra(client, half) for half in _halves(wanted))
            return a[0] + b[0], {**a[1], **b[1]}
        return [], {wanted[0][0]: f"graphql_{status}"}
    data = body.get("data") or {}
    out = []
    for i, (repo, paths) in enumerate(items):
        r = data.get(f"r{i}") or {}
        for j, p in enumerate(paths):
            b = r.get(f"f{j}")
            if b:
                out.append({"repo": repo, "path": p, "oid": b["oid"], "size": b["byteSize"], "binary": b["isBinary"]})
    return out, {}


def fetch_tree(rest: RestClient, repo: str, oid: str) -> tuple[list[dict], bool, str | None]:
    """Every blob under .claude, at any depth. Returns (files, truncated by GitHub, error)."""
    status, body = rest.get(f"/repos/{repo}/git/trees/{oid}", {"recursive": "1"})
    if status != 200 or not isinstance(body.get("tree"), list):
        return [], False, f"rest_tree_{status}"
    files = [{"path": f".claude/{e['path']}", "oid": e["sha"], "size": e.get("size")}
             for e in body["tree"] if e.get("type") == "blob"]
    return files, bool(body.get("truncated")), None


def blob_query(group: list[tuple[str, list[str]]]) -> str:
    parts = []
    for i, (repo, oids) in enumerate(group):
        blobs = " ".join(f"b{j}: object(oid: {json.dumps(o)}) {{ ... on Blob {{ oid isBinary isTruncated text }} }}"
                         for j, o in enumerate(oids))
        parts.append(f"r{i}: repository({_repo_args(repo)}) {{ {blobs} }}")
    return "query { " + " ".join(parts) + " }"


def plan_blob_batches(wanted: list[dict]) -> list[list[dict]]:
    batches, cur, size = [], [], 0
    for w in wanted:
        if cur and (len(cur) >= BLOB_BATCH or size + w["size"] > BLOB_BATCH_BYTES):
            batches.append(cur)
            cur, size = [], 0
        cur.append(w)
        size += w["size"]
    if cur:
        batches.append(cur)
    return batches


def fetch_blobs(client: GraphQLClient, blobs: list[dict], store: BlobStore) -> dict[str, str]:
    """Store each blob's redacted text. Returns {oid: "" when stored, else why not}."""
    items = _group([(b["repo"], b["oid"]) for b in blobs])
    status_code, body = _post(client, blob_query(items))
    if is_retryable(status_code, body):
        _stop_if_down(client, "blob", status_code)
        if len(blobs) > 1:
            a, b = (fetch_blobs(client, half, store) for half in _halves(blobs))
            return {**a, **b}
        return {blobs[0]["oid"]: "fetch_error"}
    data = body.get("data") or {}
    gone = _not_found(body)
    status: dict[str, str] = {}
    for i, (_, oids) in enumerate(items):
        r = data.get(f"r{i}") or {}
        for j, oid in enumerate(oids):
            b = r.get(f"b{j}")
            if b is None:
                is_gone = (f"r{i}",) in gone or (f"r{i}", f"b{j}") in gone
                status[oid] = "missing" if is_gone else "fetch_error"
            elif b["isBinary"]:
                status[oid] = "binary"
            elif b["isTruncated"] or b.get("text") is None:
                status[oid] = "truncated"
            else:
                store.put(oid, b["text"])
                status[oid] = ""
    return status


def _redaction_rows(ctx: Ctx, oids: list[str]) -> list[dict]:
    """Redaction rows from the counts stored with each blob, so a kill after a blob write loses nothing."""
    rows = []
    for oid in dict.fromkeys(oids):
        rows += [{"blob_sha": oid, "rule": k, "n": n} for k, n in ctx.blobs.redaction_counts(oid).items() if n > 0]
    return rows


def harvest_batch(ctx: Ctx, gql: GraphQLClient, rest: RestClient, units: list[Unit], final: bool = False) -> Partial:
    """final=True is the last attempt for these units: record what is reachable and why the rest is not."""
    payloads = [u.payload for u in units]
    found, failed = fetch_meta(gql, payloads)
    more = [(repo, p) for repo, extra in payloads if repo in found and not found[repo].row["missing"]
            for p in extra[MAX_EXTRA:]]
    for i in range(0, len(more), EXTRA_BATCH):
        entries, errors = fetch_extra(gql, more[i:i + EXTRA_BATCH])
        for e in entries:
            found[e.pop("repo")].files.append(e)
        failed.update(errors)
    tree_calls = tree_errors = 0
    for repo, f in found.items():
        if f.tree_oid is None or repo in failed:
            continue
        tree_calls += 1
        files, truncated, error = fetch_tree(rest, repo, f.tree_oid)
        if error:
            tree_errors += 1
            failed[repo] = error
            f.row["tree_truncated"] = f.subdirs  # the final pass keeps the top level and says what is missing
        else:
            f.files = [x for x in f.files if not x["path"].startswith(".claude/")] + files
            f.row["tree_truncated"] = int(truncated)
    if tree_calls >= 3 and tree_errors == tree_calls:
        raise StopStage("every REST tree request in the batch failed; stopped so nothing transient is recorded")

    rows, wanted, queued = [], [], set()
    for repo, f in found.items():
        if repo in failed and not final:
            continue
        seen: set[str] = set()
        for e in f.files:
            if e["path"] in seen:
                continue
            seen.add(e["path"])
            kind = classify(e["path"]) or "other"
            reason = None
            if kind not in PARSED_KINDS:
                reason = "not_fetched_kind"
            elif e.get("binary"):
                reason = "binary"
            elif (e["size"] or 0) > MAX_FETCH_BYTES:
                reason = "too_large"
            elif not ctx.blobs.has(e["oid"]) and e["oid"] not in queued:
                queued.add(e["oid"])
                wanted.append({"repo": repo, "oid": e["oid"], "size": e["size"] or 0})
            rows.append({"repo": repo, "path": e["path"], "kind": kind, "blob_sha": e["oid"], "size": e["size"],
                         "skip_reason": reason})
    status: dict[str, str] = {}
    for chunk in plan_blob_batches(wanted):
        status.update(fetch_blobs(gql, chunk, ctx.blobs))
    for row in rows:
        if row["skip_reason"] is None and not ctx.blobs.has(row["blob_sha"]):
            row["skip_reason"] = status.get(row["blob_sha"]) or "fetch_error"
            if row["skip_reason"] == "fetch_error":
                failed.setdefault(row["repo"], "blob_fetch_error")
        row["fetched"] = row["skip_reason"] is None

    repos = []
    for repo, _ in payloads:
        if repo in failed and not final:
            continue
        if repo not in found:
            repos.append(_missing(repo, f"unreachable:{failed[repo]}"))
            continue
        if repo in failed:
            found[repo].row["error"] = f"partial:{failed[repo]}"
        repos.append(found[repo].row)
    rows = [r for r in rows if final or r["repo"] not in failed]
    redactions = _redaction_rows(ctx, [r["blob_sha"] for r in rows if r["fetched"]])
    key_of = {u.payload[0]: u.key for u in units}
    deferred = {} if final else {key_of[repo]: error for repo, error in failed.items()}
    return Partial({"repos": repos, "harness_files": rows, "redactions": redactions}, deferred)


def run(ctx: Ctx, opts: Opts) -> RunStats:
    if ctx.fixtures:
        return _run_fixtures(ctx)
    nested: dict[str, set[str]] = {}
    for h in ctx.tables.read("repo_hits"):
        extra = nested.setdefault(h["repo"], set())
        if h["component"] == "claude_md" and h["path"] != "CLAUDE.md" and not h["path"].startswith(".claude/"):
            extra.add(h["path"])
    # Ordered by unit key (a hash), so `--limit N` harvests a stable pseudo-random N of the discovered repos.
    units = sorted((Unit(unit_key("s2", VERSION, repo), (repo, sorted(extra))) for repo, extra in nested.items()),
                   key=lambda u: u.key)
    gql, rest = GraphQLClient(github_token()), RestClient(github_token())
    stats = run_batched(ctx, "s2", units, lambda batch: harvest_batch(ctx, gql, rest, batch), BATCH, opts.limit,
                        give_up=lambda u, error: harvest_batch(ctx, gql, rest, [u], final=True).rows)
    print(f"s2 graphql: {gql.stats}; rest: {rest.stats}", flush=True)
    return stats
```

`_run_fixtures` stays below `harvest_batch`, unchanged.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q`
Expected: PASS. `tests/test_s2_harvest.py` has 24 tests (it had 14); total 324.

- [ ] **Step 5: Commit**

```bash
git add pipeline/s2_harvest.py tests/test_s2_harvest.py
git commit -m "m2: S2 lists .claude by REST tree, defers repos GitHub cannot serve, and records missing only when final"
```

---

### Task 4: S6 defers call errors; the harnesses_total comment

The S6 half of deferred M1 fix 1, plus the comment the M1 review flagged.

**Files:**
- Modify: `pipeline/s6_extract.py`, `facts/harnesses_total.sql`
- Test: `tests/test_s6_extract.py`

**Interfaces:**
- Consumes: `Partial`, `run_batched(..., give_up=)`, `MAX_ATTEMPTS`, `Ctx.attempts` (Task 1).
- Produces: `extract_batch(llm, p, items, texts) -> tuple[dict[str, list[dict]], dict[str, str]]` — the second value maps `cluster_id` to the call error of an artifact whose single-artifact call failed. `semantics_rejects.reason = "call_error"` is now written only after `MAX_ATTEMPTS` failed attempts.

- [ ] **Step 1: Update the tests**

In `tests/test_s6_extract.py`:

Add `from pipeline.runner import MAX_ATTEMPTS` to the imports.

Replace `test_extract_batch_accepts_valid_and_rejects_fabricated`'s call line and add the `failed` assertion:

```python
    out, failed = s6.extract_batch(ScriptLLM(fn), s6.PASSES["a"], items(2), TEXTS)
    assert failed == {}
```

Replace `test_call_error_splits_batch_down_to_single_artifacts`'s call line:

```python
    out, failed = s6.extract_batch(ScriptLLM(fn), s6.PASSES["a"], items(4), TEXTS)
    assert failed == {}
```

Replace `test_single_artifact_call_error_is_rejected` with:

```python
def test_single_artifact_call_error_is_deferred_not_rejected():
    out, failed = s6.extract_batch(ScriptLLM(lambda p: CallResult(None, "timeout", 900.0, None)), s6.PASSES["a"],
                                   items(1), TEXTS)
    assert failed == {"c0": "timeout"}
    assert out["semantics_rejects"] == [] and len(out["llm_calls"]) == 1
```

Replace `test_partial_failure_is_journaled` with:

```python
def test_call_error_is_deferred_then_rejected_after_max_attempts(fctx, monkeypatch):
    run_until(fctx, "s5")
    real = s6.make_llm("fake")
    reps = s6.representatives(fctx)
    marker = fctx.blobs.get(reps[0]["blob_sha"])

    def fn(prompt):
        if prompt.count("<artifact-") > 1 or marker in prompt:
            return CallResult(None, "overloaded", 1.0, None)
        return real.call("sonnet", s6.SYSTEM_A, prompt, s6.RECORDS_SCHEMA)

    monkeypatch.setattr(s6, "make_llm", lambda name: ScriptLLM(fn))
    stats = s6.run(fctx, Opts())
    assert (stats.units_run, stats.units_deferred) == (10, 1) and "deferred" in stats.stopped
    assert fctx.tables.read("semantics_rejects") == [] and len(fctx.tables.read("semantics")) == 10
    for _ in range(MAX_ATTEMPTS - 2):
        assert s6.run(fctx, Opts()).units_deferred == 1  # the lone failing artifact is retried, not an outage
    stats = s6.run(fctx, Opts())
    assert (stats.units_gave_up, stats.stopped) == (1, None)
    assert [r["reason"] for r in fctx.tables.read("semantics_rejects")] == ["call_error"]
    assert len(fctx.tables.read("semantics")) == 10
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_s6_extract.py -q`
Expected: FAIL with `ValueError: too many values to unpack` or `not enough values to unpack` in the three `extract_batch` tests, and `assert (11, 0) == (10, 1)` in the new run test.

- [ ] **Step 3: Implement**

In `pipeline/s6_extract.py`, change the runner import to `from pipeline.runner import Partial, RunStats, StopStage, Unit, run_batched`, and replace the module docstring, `extract_batch` and `run` with:

```python
"""S6 tier-2 extraction (PRD §4 S6, §5): one representative per distinct cluster, 20 per claude -p call,
3 calls in parallel (PRD §9.1). A call error splits the batch. An artifact whose own call still fails is
deferred and retried on later runs, and rejected as call_error only after runner.MAX_ATTEMPTS. A plan limit,
or a batch of fresh artifacts in which every call failed (an outage), stops the stage without journaling.
"""
```

```python
def extract_batch(llm, p: Pass, items: list[dict], texts: dict[str, str]) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """(rows, {cluster_id: error} for artifacts whose single-artifact call failed)."""
    ids = {f"a{i}": it for i, it in enumerate(items)}
    sources = {k: texts[it["blob_sha"]][:MAX_CHARS] for k, it in ids.items()}
    res = llm.call(p.model, p.system, build_prompt(list(sources.items()), secrets.token_hex(6)), RECORDS_SCHEMA)
    call = {"call_id": unit_key(p.pass_id, [it["cluster_id"] for it in items], time.time()), "pass_id": p.pass_id,
            "model": p.model, "n_sent": len(items), "n_ok": 0, "error": res.error, "wall_s": res.wall_s,
            "cost_usd": res.cost_usd}
    out: dict[str, list[dict]] = {"semantics": [], "semantics_rejects": [], "llm_calls": [call]}
    failed: dict[str, str] = {}
    if res.error:
        if len(items) > 1:
            mid = len(items) // 2
            for half in (items[:mid], items[mid:]):
                sub, sub_failed = extract_batch(llm, p, half, texts)
                for t in TABLES:
                    out[t] += sub[t]
                failed.update(sub_failed)
        else:
            failed[items[0]["cluster_id"]] = res.error
        return out, failed
    ok, rejects = score(sources, res.data.get("records") or [])
    for r in ok:
        it = ids[r["id"]]
        out["semantics"].append({"cluster_id": it["cluster_id"], "pass_id": p.pass_id, "artifact_id": it["artifact_id"],
                                 "use_case": r["use_case"], "domain_guess": r["domain_guess"],
                                 "non_coding": r["non_coding"],
                                 "techniques_json": json.dumps(r["techniques_described"], sort_keys=True),
                                 "notable": r["notable"]})
    for rid, reason in rejects.items():
        out["semantics_rejects"].append({"cluster_id": ids[rid]["cluster_id"], "pass_id": p.pass_id, "reason": reason})
    call["n_ok"] = len(ok)
    return out, failed


def run(ctx: Ctx, opts: Opts) -> RunStats:
    p = PASSES[opts.pass_id]
    reps = representatives(ctx)
    random.Random(p.order_seed).shuffle(reps)  # passes see artifacts in different orders (PRD §6.2)
    units = [Unit(unit_key("s6", VERSION, p.pass_id, p.model, p.system, r["cluster_id"], r["blob_sha"]), r) for r in reps]
    llm = make_llm(ctx.llm)
    tried_before = set(ctx.attempts("s6").counts())

    def work(batch: list[Unit]) -> Partial:
        texts = {u.payload["blob_sha"]: ctx.blobs.get(u.payload["blob_sha"]) for u in batch}
        chunks = [[u.payload for u in batch[i:i + BATCH]] for i in range(0, len(batch), BATCH)]
        try:
            with ThreadPoolExecutor(WORKERS) as pool:
                parts = list(pool.map(lambda c: extract_batch(llm, p, c, texts), chunks))
        except LLMLimitReached as e:
            raise StopStage(f"plan limit: {e}") from e
        out = {t: [row for rows, _ in parts for row in rows[t]] for t in TABLES}
        failed = {c: e for _, part_failed in parts for c, e in part_failed.items()}
        # An outage fails fresh artifacts wholesale. A batch made only of artifacts that failed on an
        # earlier run is content the model cannot process, and must be allowed to reach MAX_ATTEMPTS.
        fresh = any(u.key not in tried_before for u in batch)
        if fresh and out["llm_calls"] and all(c["error"] for c in out["llm_calls"]):
            raise StopStage(f"every call in the batch failed; last error: {out['llm_calls'][-1]['error']}")
        key_of = {u.payload["cluster_id"]: u.key for u in batch}
        return Partial(out, {key_of[c]: e for c, e in failed.items()})

    def give_up(u: Unit, error: str) -> dict[str, list[dict]]:
        return {"semantics_rejects": [{"cluster_id": u.payload["cluster_id"], "pass_id": p.pass_id,
                                       "reason": "call_error"}]}

    return run_batched(ctx, "s6", units, work, BATCH * WORKERS, opts.limit, give_up=give_up)
```

Replace `facts/harnesses_total.sql` with:

```sql
-- kind: scalar
-- Harnesses in the edition: discovered repos that S2 harvested. A repo that was deleted before harvest
-- (repos.error = 'not_found') or that GitHub could not serve in five attempts ('unreachable:...') is not counted.
SELECT count(*) FROM v_repos
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q`
Expected: PASS, still 324 tests (two tests replaced, none added).

- [ ] **Step 5: Commit**

```bash
git add pipeline/s6_extract.py tests/test_s6_extract.py facts/harnesses_total.sql
git commit -m "m2: S6 defers a failed call and rejects it only after five attempts; harnesses_total comment says what is excluded"
```

---

### Task 5: Redaction version and re-redaction

Deferred M1 fix 2. It must land before M3 tunes the rules.

**Files:**
- Modify: `pipeline/redact.py`, `pipeline/store.py`, `pipeline/freeze.py`, `pipeline/s8_facts.py:95-100`, `pipeline/s3_parse.py:21`, `pipeline/cli.py`
- Test: `tests/test_redact.py`, `tests/test_store.py`, `tests/test_freeze_integrity.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `atomic_write` (existing).
- Produces:
  - `pipeline.redact.REDACT_VERSION: int = 1`, `rules_fingerprint() -> str`.
  - `redact(text)` is idempotent: `redact(redact(t)[0]) == (redact(t)[0], {})`.
  - `BlobStore.put(oid, text) -> dict[str, int]`; `get(oid) -> str` and `redaction_counts(oid) -> dict[str, int]` re-redact first when the stored version is older; `version(oid) -> int`; `ensure_current(oid) -> bool` (True if it rewrote the blob); `legacy_counts_path(oid) -> Path`. `counts_path` is gone.
  - `pipeline.freeze.fetched_blobs(ctx) -> list[str]`, `stale_blobs(ctx) -> int`, `reredact(ctx) -> tuple[int, int]` (rewritten, total), `edition_hash(ctx, with_redact_version: bool = True)`. The manifest gains `redact_version`.
  - CLI: `census reredact --edition E`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_redact.py` (change its import to `from pipeline.redact import REDACT_VERSION, redact, rules_fingerprint` and add `import pytest`):

```python
SAMPLES = [
    "token " + GHP,
    "curl -u admin:hunter2secret https://x.example/api",
    "postgres://app:S3cr3tPassw0rd@db.internal:5432/app",
    'claude --api-key "sk-ant-' + "a1B2" * 8 + '"',
    "mysql -u root -pS3cr3tPassw0rd app",
    "Authorization: Bearer " + "abcDEF123456" * 3,
    '{"env": {"CLOUD_API_KEY": "Zq8Xv2Lm9Pw4Rt7Ky3Nb"}}',
    "curl -u admin:" + GHP + " https://x.example",
]


@pytest.mark.parametrize("text", SAMPLES)
def test_redact_is_idempotent_for_every_rule_shape(text):
    once, counts = redact(text)
    assert counts, text  # every sample holds something to redact
    assert redact(once) == (once, {})


def test_a_later_rule_does_not_relabel_an_earlier_placeholder():
    out, counts = redact("curl -u admin:" + GHP + " https://x.example")
    assert out == "curl -u admin:[REDACTED:github_token] https://x.example"
    assert counts == {"github_token": 1}


PINNED = (1, "REPLACE_IN_STEP_4")


def test_rule_changes_require_a_version_bump():
    assert (REDACT_VERSION, rules_fingerprint()) == PINNED, (
        "the redaction rules changed: bump REDACT_VERSION in pipeline/redact.py and pin the new fingerprint here")
```

Replace `test_blob_store_keeps_redaction_counts_in_a_sidecar` in `tests/test_store.py` with the tests below, and add `import json`, `import re`, `import zstandard` and `from pipeline.redact import redact as real_redact` to its imports.

```python
SECRET_LINE = "token ghp_" + "A1b2C3d4E5" * 4 + "\n"


def test_blob_store_keeps_version_and_counts_with_the_blob(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    oid = "ab" * 20
    assert store.redaction_counts(oid) == {}
    store.put(oid, SECRET_LINE)
    assert store.redaction_counts(oid) == {"github_token": 1} and store.version(oid) == 1
    assert [p.name for p in store.path(oid).parent.iterdir()] == [f"{oid}.zst"]  # one file per blob
    store.put("cd" * 20, "plain\n")
    assert store.redaction_counts("cd" * 20) == {} and store.get("cd" * 20) == "plain\n"


def legacy_blob(store, oid, text, counts):
    store.path(oid).parent.mkdir(parents=True, exist_ok=True)
    store.path(oid).write_bytes(zstandard.ZstdCompressor().compress(text.encode()))
    store.legacy_counts_path(oid).write_text(json.dumps(counts))


def test_m1_blob_with_a_sidecar_reads_as_version_one(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    oid = "ab" * 20
    legacy_blob(store, oid, "token [REDACTED:github_token]\n", {"github_token": 1})
    assert store.version(oid) == 1
    assert store.get(oid) == "token [REDACTED:github_token]\n"
    assert store.redaction_counts(oid) == {"github_token": 1}
    assert store.ensure_current(oid) is False
    assert store.legacy_counts_path(oid).exists()  # nothing is rewritten while the version matches


def stricter(text):
    """A later rule set that also removes e-mail addresses."""
    clean, counts = real_redact(text)
    clean, n = re.subn(r"[\w.]+@[\w.]+\.\w+", "[REDACTED:email]", clean)
    if n:
        counts["email"] = n
    return clean, counts


def test_blob_from_an_older_version_is_re_redacted_on_read(tmp_path, monkeypatch):
    store = BlobStore(tmp_path / "blobs")
    oid = "ab" * 20
    store.put(oid, "mail bob@example.com " + SECRET_LINE)
    monkeypatch.setattr("pipeline.store.REDACT_VERSION", 2)
    monkeypatch.setattr("pipeline.store.redact", stricter)
    assert store.version(oid) == 1
    assert store.get(oid) == "mail [REDACTED:email] token [REDACTED:github_token]\n"
    assert store.version(oid) == 2
    assert store.redaction_counts(oid) == {"github_token": 1, "email": 1}
    assert store.ensure_current(oid) is False


def test_re_redacting_an_m1_blob_drops_its_sidecar(tmp_path, monkeypatch):
    store = BlobStore(tmp_path / "blobs")
    oid = "ab" * 20
    legacy_blob(store, oid, "mail bob@example.com token [REDACTED:github_token]\n", {"github_token": 1})
    monkeypatch.setattr("pipeline.store.REDACT_VERSION", 2)
    monkeypatch.setattr("pipeline.store.redact", stricter)
    assert store.ensure_current(oid) is True
    assert store.redaction_counts(oid) == {"github_token": 1, "email": 1}
    assert not store.legacy_counts_path(oid).exists()


def test_blob_from_a_newer_version_is_refused(tmp_path, monkeypatch):
    store = BlobStore(tmp_path / "blobs")
    oid = "ab" * 20
    monkeypatch.setattr("pipeline.store.REDACT_VERSION", 3)
    store.put(oid, "plain\n")
    monkeypatch.setattr("pipeline.store.REDACT_VERSION", 2)
    with pytest.raises(RuntimeError, match="newer rules"):
        store.get(oid)
```

Append to `tests/test_freeze_integrity.py` (add `import json` and `from pipeline.freeze import edition_hash, reredact` to the imports):

```python
def test_manifest_records_the_redaction_version(tmp_path):
    _, ctx = copied_ctx(tmp_path)
    run_until(ctx, "s7")
    assert freeze(ctx)["redact_version"] == 1
    assert edition_hash(ctx)[0] != edition_hash(ctx, with_redact_version=False)[0]


def test_freeze_refuses_blobs_redacted_under_older_rules_until_reredact(tmp_path, monkeypatch):
    _, ctx = copied_ctx(tmp_path)
    run_until(ctx, "s7")
    for mod in ("pipeline.store", "pipeline.freeze"):
        monkeypatch.setattr(f"{mod}.REDACT_VERSION", 2)
    with pytest.raises(SystemExit, match=r"census reredact.*s3 --reset"):
        freeze(ctx)
    changed, total = reredact(ctx)
    assert changed == total > 0 and reredact(ctx) == (0, total)
    assert freeze(ctx)["redact_version"] == 2


def test_facts_refuse_an_edition_frozen_under_another_redaction_version(tmp_path, monkeypatch):
    from pipeline import s8_facts

    _, ctx = copied_ctx(tmp_path)
    run_until(ctx, "s7")
    freeze(ctx)
    monkeypatch.setattr(s8_facts, "REDACT_VERSION", 2)
    with pytest.raises(SystemExit, match="frozen under redaction v1"):
        run_stage("s8", ctx, Opts())


def test_m1_manifest_without_the_field_still_verifies(tmp_path):
    from pipeline.paths import manifest_path

    _, ctx = copied_ctx(tmp_path)
    run_until(ctx, "s7")
    freeze(ctx)
    path = manifest_path(ctx.edition)
    manifest = json.loads(path.read_text())
    del manifest["redact_version"]
    manifest["edition_hash"] = edition_hash(ctx, with_redact_version=False)[0]  # how M1 computed it
    path.write_text(json.dumps(manifest))
    assert not run_stage("s8", ctx, Opts()).stopped
```

Append to `tests/test_cli.py`:

```python
def test_reredact_reports_how_many_blobs_it_rewrote(fctx, capsys):
    from helpers import run_until

    run_until(fctx, "s2")
    assert cli.main(["reredact", "--edition", "test"]) == 0
    assert "re-redacted 0 of" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_redact.py tests/test_store.py tests/test_freeze_integrity.py tests/test_cli.py -q`
Expected: FAIL at import with `ImportError: cannot import name 'REDACT_VERSION'`.

- [ ] **Step 3: Implement `pipeline/redact.py`**

Add `import hashlib`, `import inspect` and `import json` to the imports. Add below the module docstring's imports:

```python
REDACT_VERSION = 1  # bump when RULES, a gate or the placeholder format changes; tests/test_redact.py pins the rules
```

Add after `RULES`:

```python
def rules_fingerprint() -> str:
    """Changes whenever a rule, a gate or a gate's helper changes, so a test can demand a version bump."""
    spec = [[name, rx.pattern, rx.flags, inspect.getsource(gate) if gate else None] for name, rx, gate in RULES]
    helpers = [IDENTIFIER.pattern, ENV_REF.pattern, ENV_REF.flags, inspect.getsource(_entropy)]
    return hashlib.sha256(json.dumps([spec, helpers]).encode()).hexdigest()[:16]
```

Replace `redact` with:

```python
def redact(text: str) -> tuple[str, dict[str, int]]:
    """Idempotent: a placeholder is never matched again, by a later rule or by a later run over stored text."""
    counts: dict[str, int] = {}
    for name, rx, gate in RULES:
        def sub(m: re.Match[str], name: str = name, gate: Gate = gate) -> str:
            g = m.groupdict()
            pre, post = g.get("pre") or "", g.get("post") or ""
            whole = m.group(0)
            secret = whole[len(pre):len(whole) - len(post)]
            if secret.startswith("[REDACTED:"):
                return whole
            if gate is not None and not gate(secret):
                return whole
            counts[name] = counts.get(name, 0) + 1
            return f"{pre}[REDACTED:{name}]{post}"
        text = rx.sub(sub, text)
    return text, counts
```

- [ ] **Step 4: Pin the fingerprint**

Run: `uv run python -c "from pipeline.redact import rules_fingerprint; print(rules_fingerprint())"`
Expected: 16 hex characters. In `tests/test_redact.py`, replace `REPLACE_IN_STEP_4` with that value.

- [ ] **Step 5: Implement `pipeline/store.py`**

Change the redact import to `from pipeline.redact import REDACT_VERSION, redact` and replace `BlobStore` with:

```python
HEADER = "\x00census-blob "  # git treats a file with a NUL byte as binary, so no stored text starts like this


class BlobStore:
    """Blobs keyed by git blob SHA. Text is redacted before it reaches disk (CLAUDE.md privacy rule).

    A blob file is zstd of one header line (redaction version and per-rule counts) followed by the redacted
    text, written in one atomic rename. A blob written under older rules is re-redacted when it is next read:
    the current rules are applied to the stored text, so re-redaction can add redactions and never undo one.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, oid: str) -> Path:
        return self.root / oid[:2] / f"{oid}.zst"

    def legacy_counts_path(self, oid: str) -> Path:
        """M1 kept counts in a sidecar and wrote no version. Those blobs are version 1."""
        return self.root / oid[:2] / f"{oid}.json"

    def has(self, oid: str) -> bool:
        return self.path(oid).exists()

    def _write(self, oid: str, text: str, counts: dict[str, int]) -> None:
        head = HEADER + json.dumps({"v": REDACT_VERSION, "counts": counts}, sort_keys=True) + "\n"
        atomic_write(self.path(oid), zstandard.ZstdCompressor(level=10).compress((head + text).encode()))

    def _read(self, oid: str) -> tuple[int, dict[str, int], str]:
        raw = zstandard.ZstdDecompressor().decompress(self.path(oid).read_bytes()).decode()
        if raw.startswith(HEADER):
            head, _, text = raw.partition("\n")
            meta = json.loads(head[len(HEADER):])
            return meta["v"], meta["counts"], text
        side = self.legacy_counts_path(oid)
        return 1, (json.loads(side.read_text()) if side.exists() else {}), raw

    def _current(self, oid: str) -> tuple[dict[str, int], str]:
        version, counts, text = self._read(oid)
        if version == REDACT_VERSION:
            return counts, text
        if version > REDACT_VERSION:
            raise RuntimeError(f"blob {oid} was redacted under newer rules (v{version}) than this code "
                               f"(v{REDACT_VERSION}); update the code before reading it")
        clean, found = redact(text)
        merged = dict(counts)
        for rule, n in found.items():
            merged[rule] = merged.get(rule, 0) + n
        self._write(oid, clean, merged)
        self.legacy_counts_path(oid).unlink(missing_ok=True)
        return merged, clean

    def put(self, oid: str, text: str) -> dict[str, int]:
        clean, counts = redact(text)
        self._write(oid, clean, counts)
        return counts

    def get(self, oid: str) -> str:
        return self._current(oid)[1]

    def redaction_counts(self, oid: str) -> dict[str, int]:
        return self._current(oid)[0] if self.has(oid) else {}

    def version(self, oid: str) -> int:
        return self._read(oid)[0]

    def ensure_current(self, oid: str) -> bool:
        """Re-redact the blob if it was written under older rules. True if it was rewritten."""
        if self.version(oid) == REDACT_VERSION:
            return False
        self._current(oid)
        return True
```

- [ ] **Step 6: Implement the freeze, facts, S3 and CLI changes**

In `pipeline/freeze.py`, add `from pipeline.redact import REDACT_VERSION` and replace `edition_hash` and `freeze` with:

```python
def fetched_blobs(ctx: Ctx) -> list[str]:
    rows = ctx.tables.connect().execute(
        "SELECT DISTINCT blob_sha FROM harness_files WHERE fetched ORDER BY blob_sha").fetchall()
    return [r[0] for r in rows]


def edition_hash(ctx: Ctx, with_redact_version: bool = True) -> tuple[str, dict]:
    """with_redact_version=False reproduces the hash of a manifest written before the field existed (M1)."""
    tables = {t: {p: hashlib.sha256((ctx.tables.dir(t) / f"{p}.parquet").read_bytes()).hexdigest()
                  for p in sorted(ctx.tables.parts(t))} for t in sorted(SCHEMAS)}
    blobs = fetched_blobs(ctx)
    body: dict = {"tables": tables, "blobs": blobs}
    if with_redact_version:
        body["redact_version"] = REDACT_VERSION  # the blob set names content; the version names what was removed
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    return digest, {"tables": tables, "blob_count": len(blobs)}


def stale_blobs(ctx: Ctx) -> int:
    return sum(1 for oid in fetched_blobs(ctx) if ctx.blobs.version(oid) != REDACT_VERSION)


def reredact(ctx: Ctx) -> tuple[int, int]:
    """Bring every blob of the edition to the current redaction version. Returns (rewritten, total)."""
    oids = fetched_blobs(ctx)
    return sum(ctx.blobs.ensure_current(oid) for oid in oids), len(oids)
```

```python
def freeze(ctx: Ctx) -> dict:
    bad = integrity_violations(ctx)
    if bad:
        raise SystemExit("freeze refused, stale rows:\n" + "\n".join(bad))
    stale = stale_blobs(ctx)
    if stale:
        raise SystemExit(f"freeze refused: {stale} blobs were redacted under rules older than v{REDACT_VERSION}, "
                         f"so parsed artifacts may hold text the current rules remove. Run "
                         f"`census reredact --edition {ctx.edition}`, then `census run s3 --reset` and every "
                         f"stage after it")
    digest, body = edition_hash(ctx)
    manifest = {"edition": ctx.edition, "edition_hash": digest, "frozen_at": time.strftime("%Y-%m-%d", time.gmtime()),
                "redact_version": REDACT_VERSION, **body}
    atomic_write(manifest_path(ctx.edition), (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
    return manifest
```

In `pipeline/s8_facts.py`, add `from pipeline.redact import REDACT_VERSION` and replace the first two statements of `run` (`manifest = ...` and `current, _ = ...`) with:

```python
    manifest = load_manifest(ctx.edition)
    frozen_under = manifest.get("redact_version", 1)  # M1 manifests predate the field; the M1 rules are version 1
    if frozen_under != REDACT_VERSION:
        raise SystemExit(f"{ctx.edition} was frozen under redaction v{frozen_under}; this code is v{REDACT_VERSION}. "
                         f"Run `census reredact --edition {ctx.edition}`, rerun s3 onward with --reset, then freeze")
    current, _ = edition_hash(ctx, "redact_version" in manifest)
```

In `pipeline/s3_parse.py`, add `from pipeline.redact import REDACT_VERSION` and change the unit line to:

```python
    units = [Unit(unit_key("s3", VERSION, REDACT_VERSION, f["repo"], f["path"], f["blob_sha"]), f) for f in todo]
```

In `pipeline/cli.py`, add `sub.add_parser("reredact", parents=[common])` after the `labels` parser, and add this branch in `main` after the `labels` branch:

```python
    elif args.cmd == "reredact":
        from pipeline.freeze import reredact
        from pipeline.redact import REDACT_VERSION
        changed, total = reredact(ctx)
        print(f"re-redacted {changed} of {total} blobs to v{REDACT_VERSION}")
```

In `tests/test_e2e.py`, change the comment `# Every stored file: raw, decompressed .zst, blob sidecars (.json) and site data.` to `# Every stored file: raw, decompressed .zst (header and text) and site data.`

- [ ] **Step 7: Run the tests**

Run: `uv run pytest -q`
Expected: PASS. New tests: 10 in test_redact (8 parametrized + 2), 4 net in test_store (5 added, 1 removed), 4 in test_freeze_integrity, 1 in test_cli. Total 343.

- [ ] **Step 8: Commit**

```bash
git add pipeline/redact.py pipeline/store.py pipeline/freeze.py pipeline/s8_facts.py pipeline/s3_parse.py pipeline/cli.py tests/test_redact.py tests/test_store.py tests/test_freeze_integrity.py tests/test_cli.py tests/test_e2e.py
git commit -m "m2: blobs carry their redaction version and counts; older blobs are re-redacted on read; the manifest records the version"
```

---

### Task 6: S1 at full scale

**Files:**
- Modify: `pipeline/s1_discover.py:129-166` (`run`) and its imports
- Test: `tests/test_s1_discover.py`

**Interfaces:**
- Consumes: `StageSession`, `run_batched(..., session=)`, `read_state`, `write_state` (Task 1); `SearchClient` count-only cache (Task 2).
- Produces: in full mode (no `--limit`), the state file `journal/s1.state.json`: `{"config": str, "families_done": ["claude_md/nonfork", "claude_md/fork", "claude_dir/nonfork", ...], "complete": bool}`. Family names are `f"{seed}/{mode}"` in the order `SEEDS` × `FORK_MODES`. Slice mode writes no state.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_s1_discover.py` (add `import pytest`, `from pipeline.context import make_ctx` and `from pipeline.runner import read_state` to the imports):

```python
def fake_github(monkeypatch):
    monkeypatch.setattr(s1, "SearchClient", FakeSearch)
    monkeypatch.setattr(s1, "github_token", lambda: "t")
    FakeSearch.queries = []


def hit_keys(ctx):
    return sorted((h["repo"], h["path"], h["query_id"]) for h in ctx.tables.read("repo_hits"))


def test_full_mode_records_each_family_and_completion(ctx, monkeypatch):
    fake_github(monkeypatch)
    s1.run(ctx, Opts())
    state = read_state(ctx, "s1")
    assert state["families_done"] == [f"{s}/{m}" for s in s1.SEEDS for m in s1.FORK_MODES]
    assert state["complete"] is True


def test_slice_mode_writes_no_state(ctx, monkeypatch):
    fake_github(monkeypatch)
    s1.run(ctx, Opts(limit=4))
    assert read_state(ctx, "s1") == {}


def test_rerun_of_a_complete_walk_makes_no_requests(ctx, monkeypatch):
    fake_github(monkeypatch)
    s1.run(ctx, Opts())
    FakeSearch.queries = []
    stats = s1.run(ctx, Opts())
    assert FakeSearch.queries == [] and stats.units_run == 0


def test_changed_walk_config_walks_again_without_duplicating(ctx, monkeypatch):
    fake_github(monkeypatch)
    s1.run(ctx, Opts())
    before = hit_keys(ctx)
    monkeypatch.setitem(s1.FLOOR_SPLITS, "mcp", ["path:/", "extension:json"])
    FakeSearch.queries = []
    stats = s1.run(ctx, Opts())
    assert FakeSearch.queries and stats.units_run == 0  # the lattice is walked again; every node is already journaled
    assert hit_keys(ctx) == before


class Crash(Exception):
    pass


def test_crash_mid_walk_resumes_without_loss_or_duplicates(ctx, monkeypatch):
    fake_github(monkeypatch)
    clean = make_ctx("clean")
    s1.run(clean, Opts())
    real, pages = FakeSearch.search, []

    def dying(self, q, page=1, per_page=100):
        if per_page == 100:
            pages.append(q)
            if len(pages) == 30:
                raise Crash()
        return real(self, q, page, per_page)

    monkeypatch.setattr(FakeSearch, "search", dying)
    with pytest.raises(Crash):
        s1.run(ctx, Opts())
    assert 0 < len(ctx.journal("s1").done_units()) and not read_state(ctx, "s1").get("complete")
    monkeypatch.setattr(FakeSearch, "search", real)
    s1.run(ctx, Opts())
    assert hit_keys(ctx) == hit_keys(clean) and read_state(ctx, "s1")["complete"]


def test_journal_is_read_once_per_run(ctx, monkeypatch):
    from pipeline.journal import Journal

    fake_github(monkeypatch)
    reads = []
    real = Journal.done_units
    monkeypatch.setattr(Journal, "done_units", lambda self: reads.append(1) or real(self))
    s1.run(ctx, Opts())
    assert len(reads) == 1


def test_full_mode_does_not_load_every_hit(ctx, monkeypatch):
    fake_github(monkeypatch)
    monkeypatch.setattr(s1, "_repos_by_seed", lambda ctx: pytest.fail("full mode must not read repo_hits"))
    s1.run(ctx, Opts())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_s1_discover.py -q`
Expected: the seven new tests FAIL (`KeyError: 'families_done'` or `'complete'`, more than one journal read, the `pytest.fail` message); the existing tests pass.

- [ ] **Step 3: Implement**

In `pipeline/s1_discover.py`, change the runner import to:

```python
from pipeline.runner import RunStats, StageSession, Unit, merge_stats, read_state, run_batched, run_whole, write_state
```

Add `PROGRESS_EVERY = 25` below `FLOOR_SPLITS`. Add to the module docstring, before the "Fork semantics" paragraph:

```
In full mode each (seed, fork mode) family that has been walked to the end is recorded in the stage state
file. A rerun skips finished families, and S2 reads the state to know when nested CLAUDE.md paths are final.
```

Replace `run` with:

```python
def _walk_config() -> str:
    """Fingerprint of everything that decides which nodes the walk visits."""
    return unit_key(VERSION, SEEDS, FLOOR_SPLITS, FORK_MODES, CAP, MAX_SIZE)


def run(ctx: Ctx, opts: Opts) -> RunStats:
    if ctx.fixtures:
        return _run_fixtures(ctx)
    client = SearchClient(github_token(), ctx.root / "search_cache.jsonl")

    incomplete = 0

    def count(q: str) -> int:
        nonlocal incomplete
        body = client.search(q, per_page=1)
        if body.get("incomplete_results"):
            incomplete += 1
        return body["total_count"]

    full = opts.limit is None
    session = StageSession(ctx, "s1")
    state = read_state(ctx, "s1") if full else {}
    if state.get("config") != _walk_config():
        state = {"config": _walk_config(), "families_done": [], "complete": False}
    have = {} if full else _repos_by_seed(ctx)  # slice mode only: the full table does not fit in memory
    target = None if full else math.ceil(opts.limit / len(SEEDS))
    total = RunStats("s1")
    for seed, mode in ((s, m) for s in SEEDS for m in (FORK_MODES if full else ["nonfork"])):
        name = f"{seed}/{mode}"
        if name in state["families_done"]:
            continue
        found = have.get((seed, mode), set())
        rng = random.Random(f"{VERSION}:{seed}:{mode}")
        nodes = walk(_base(SEEDS[seed], mode), count, rng, FLOOR_SPLITS[seed])
        walked = 0
        while target is None or len(found) < target:
            node = next(nodes, None)  # pulling a node issues count requests, so check the target first
            if node is None:
                break
            walked += 1
            key = unit_key("s1", VERSION, node.query, node.kind)
            if key in session.done:
                total.units_total += 1
                total.units_skipped += 1
            else:
                got: list[str] = []

                def work(batch: list[Unit], got: list[str] = got) -> dict[str, list[dict]]:
                    out = node_work(client, batch)
                    got.extend(r["repo"] for r in out["repo_hits"])
                    return out

                merge_stats(total, run_batched(ctx, "s1", [Unit(key, (seed, node))], work, batch_size=1,
                                               log=lambda m: None, session=session))
                found.update(got)
            if walked % PROGRESS_EVERY == 0:
                print(f"s1 {name}: {walked} nodes walked, {total.units_run} fetched this run; search {client.stats}",
                      flush=True)
        if full:  # the walk ran to its end, so every repo this family can reach is in repo_hits
            state["families_done"].append(name)
            write_state(ctx, "s1", state)
        print(f"s1 {name}: {walked} nodes; search {client.stats}; incomplete counts {incomplete}", flush=True)
    if full:
        state["complete"] = True
        write_state(ctx, "s1", state)
    return total
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q`
Expected: PASS, 343 + 7 = 350.

- [ ] **Step 5: Commit**

```bash
git add pipeline/s1_discover.py tests/test_s1_discover.py
git commit -m "m2: S1 reads its journal once per run, records finished families, and skips them on rerun"
```

---

### Task 7: S2 at full scale

**Files:**
- Modify: `pipeline/paths.py:17-18`, `pipeline/s2_harvest.py` (`run`, `harvest_batch` first line, imports)
- Test: `tests/test_s2_harvest.py`

**Interfaces:**
- Consumes: `read_state(ctx, "s1")` with `families_done` and `complete` (Task 6); `harvest_batch`, `Hub`, `seed_hits`, `wire` (Task 3).
- Produces:
  - `CENSUS_BLOBS` — directory for the blob store; default `<CENSUS_DATA>/blobs`.
  - `s2_harvest.discovered(ctx) -> list[tuple[str, list[str]]]` — (repo, sorted nested CLAUDE.md paths), computed in SQL.
  - `s2_harvest.MIN_FREE_BYTES = 5 * 2**30`.
  - In full mode `s2.run` returns `stopped="waiting for S1 to finish the claude_md families ..."` before both families are done, and `stopped="S1 is still discovering repos; rerun S2 to harvest the rest"` after a clean pass while S1 is not complete. Slice mode (`--limit`) is not gated.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_s2_harvest.py` (add `from collections import namedtuple` to the imports):

```python
def two_repo_hub():
    return Hub({"o/a": node(), "o/b": node()},
               paths={("o/a", "CLAUDE.md"): "a" * 40, ("o/b", "CLAUDE.md"): "b" * 40},
               texts={"a" * 40: "# a\n", "b" * 40: "# b\n"})


def test_discovered_groups_nested_claude_md_paths_per_repo(ctx):
    ctx.tables.write_part("repo_hits", "p-seed", [
        {"repo": "o/a", "path": "CLAUDE.md", "component": "claude_md", "query_id": "q", "blob_sha": "s", "is_fork": False},
        {"repo": "o/a", "path": "pkg/b/CLAUDE.md", "component": "claude_md", "query_id": "q", "blob_sha": "s", "is_fork": False},
        {"repo": "o/a", "path": "pkg/a/CLAUDE.md", "component": "claude_md", "query_id": "q2", "blob_sha": "s", "is_fork": False},
        {"repo": "o/a", "path": "pkg/a/CLAUDE.md", "component": "claude_md", "query_id": "q3", "blob_sha": "s", "is_fork": False},
        {"repo": "o/a", "path": ".claude/CLAUDE.md", "component": "claude_md", "query_id": "q", "blob_sha": "s", "is_fork": False},
        {"repo": "o/b", "path": ".claude/skills/x/SKILL.md", "component": "skill", "query_id": "q", "blob_sha": "s", "is_fork": False},
    ])
    assert sorted(s2.discovered(ctx)) == [("o/a", ["pkg/a/CLAUDE.md", "pkg/b/CLAUDE.md"]), ("o/b", [])]


def test_full_mode_waits_for_the_claude_md_families(ctx, monkeypatch):
    hub = two_repo_hub()
    seed_hits(ctx, [("o/a", "CLAUDE.md"), ("o/b", "CLAUDE.md")])
    write_state(ctx, "s1", {"families_done": ["claude_md/nonfork"], "complete": False})
    wire(monkeypatch, hub)
    stats = s2.run(ctx, Opts())
    assert stats.stopped.startswith("waiting for S1") and hub.meta_sizes == []
    assert s2.run(ctx, Opts(limit=1)).units_run == 1  # slice mode is not gated


def test_full_mode_asks_for_a_rerun_while_s1_is_still_discovering(ctx, monkeypatch):
    hub = two_repo_hub()
    seed_hits(ctx, [("o/a", "CLAUDE.md"), ("o/b", "CLAUDE.md")])
    write_state(ctx, "s1", {"families_done": ["claude_md/nonfork", "claude_md/fork"], "complete": False})
    wire(monkeypatch, hub)
    stats = s2.run(ctx, Opts())
    assert stats.units_run == 2 and stats.stopped.startswith("S1 is still discovering")
    write_state(ctx, "s1", {"families_done": ["claude_md/nonfork", "claude_md/fork"], "complete": True})
    assert s2.run(ctx, Opts()).stopped is None


def test_low_disk_stops_the_stage_before_any_request(ctx, monkeypatch):
    hub = two_repo_hub()
    seed_hits(ctx, [("o/a", "CLAUDE.md")])
    wire(monkeypatch, hub)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(s2.shutil, "disk_usage", lambda p: usage(100 * 2**30, 99 * 2**30, 2**30))
    stats = s2.run(ctx, Opts())
    assert "GiB free" in stats.stopped and "CENSUS_BLOBS" in stats.stopped
    assert hub.meta_sizes == [] and ctx.journal("s2").entries() == []
    monkeypatch.setattr(s2.shutil, "disk_usage", lambda p: usage(100 * 2**30, 50 * 2**30, 50 * 2**30))
    assert s2.run(ctx, Opts()).units_run == 1


def test_census_blobs_moves_the_blob_store(tmp_path, monkeypatch):
    from pipeline.context import make_ctx

    monkeypatch.setenv("CENSUS_BLOBS", str(tmp_path / "elsewhere"))
    ctx = make_ctx("test")
    ctx.blobs.put("ab" * 20, "hello\n")
    assert (tmp_path / "elsewhere" / "ab" / ("ab" * 20 + ".zst")).exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_s2_harvest.py -q`
Expected: the five new tests FAIL (`AttributeError: module 'pipeline.s2_harvest' has no attribute 'discovered'`, `'NoneType' object has no attribute 'startswith'`, `module 'pipeline.s2_harvest' has no attribute 'shutil'`, the blob path assertion).

- [ ] **Step 3: Implement**

In `pipeline/paths.py`, replace `blob_root` with:

```python
def blob_root() -> Path:
    """CENSUS_BLOBS puts the blob store on another volume; a full harvest needs tens of GiB."""
    return Path(os.environ.get("CENSUS_BLOBS", data_root() / "blobs"))
```

In `pipeline/s2_harvest.py`, add `import shutil` to the imports and `read_state` to the runner import. Add below the constants:

```python
MIN_FREE_BYTES = 5 * 2**30
PROGRESS_EVERY = 40  # batches between client-stat lines (1,000 repos)
CLAUDE_MD_FAMILIES = {"claude_md/nonfork", "claude_md/fork"}  # nested CLAUDE.md paths come only from these
```

Add above `harvest_batch`:

```python
def _check_disk(ctx: Ctx) -> None:
    root = ctx.blobs.root
    while not root.exists():
        root = root.parent
    free = shutil.disk_usage(root).free
    if free < MIN_FREE_BYTES:
        raise StopStage(f"only {free / 2**30:.1f} GiB free under {root}; free space or point CENSUS_BLOBS "
                        "at a larger volume, then rerun")


def discovered(ctx: Ctx) -> list[tuple[str, list[str]]]:
    """Every discovered repo with its nested CLAUDE.md paths. In SQL: repo_hits has millions of rows."""
    rows = ctx.tables.connect().execute("""
        SELECT repo, list(DISTINCT path ORDER BY path) FILTER (
                   WHERE component = 'claude_md' AND path <> 'CLAUDE.md' AND NOT starts_with(path, '.claude/'))
        FROM repo_hits GROUP BY repo""").fetchall()
    return [(repo, extra or []) for repo, extra in rows]
```

Make `_check_disk(ctx)` the first statement of `harvest_batch`.

Replace `run` with:

```python
def run(ctx: Ctx, opts: Opts) -> RunStats:
    if ctx.fixtures:
        return _run_fixtures(ctx)
    full = opts.limit is None
    s1 = read_state(ctx, "s1")  # read before the units, so "complete" never describes fewer repos than we list
    if full and not CLAUDE_MD_FAMILIES <= set(s1.get("families_done", [])):
        return RunStats("s2", stopped="waiting for S1 to finish the claude_md families "
                                      "(nested CLAUDE.md paths come from them)")
    # Ordered by unit key (a hash), so `--limit N` harvests a stable pseudo-random N of the discovered repos.
    units = sorted((Unit(unit_key("s2", VERSION, repo), (repo, extra)) for repo, extra in discovered(ctx)),
                   key=lambda u: u.key)
    gql, rest = GraphQLClient(github_token()), RestClient(github_token())
    batches = 0

    def work(batch: list[Unit]) -> Partial:
        nonlocal batches
        batches += 1
        if batches % PROGRESS_EVERY == 0:
            print(f"s2 graphql: {gql.stats}; rest: {rest.stats}", flush=True)
        return harvest_batch(ctx, gql, rest, batch)

    stats = run_batched(ctx, "s2", units, work, BATCH, opts.limit,
                        give_up=lambda u, error: harvest_batch(ctx, gql, rest, [u], final=True).rows)
    print(f"s2 graphql: {gql.stats}; rest: {rest.stats}", flush=True)
    if full and not stats.stopped and not s1.get("complete"):
        stats.stopped = "S1 is still discovering repos; rerun S2 to harvest the rest"
    return stats
```

If DuckDB rejects `list(DISTINCT path ORDER BY path) FILTER (...)` on the installed version, use `list_sort(list(DISTINCT path) FILTER (...))`; `test_discovered_groups_nested_claude_md_paths_per_repo` decides.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q`
Expected: PASS, 350 + 5 = 355.

- [ ] **Step 5: Commit**

```bash
git add pipeline/paths.py pipeline/s2_harvest.py tests/test_s2_harvest.py
git commit -m "m2: S2 lists its units in SQL, waits for S1's claude_md families, and stops cleanly on low disk"
```

---

### Task 8: Supervisor, audit, status

**Files:**
- Create: `pipeline/supervise.py`, `pipeline/audit.py`
- Modify: `pipeline/cli.py`
- Test: `tests/test_supervise.py`, `tests/test_audit.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `Ctx.journal`, `Ctx.attempts`, `read_state`, `STAGE_TABLES`, `atomic_write`.
- Produces:
  - `pipeline.supervise.supervise(ctx, stage: str, run_args: list[str], *, spawn=subprocess.Popen, sleep=time.sleep, min_backoff: float | None = None, max_backoff: float = 1800.0) -> int`. Files under `<edition>/logs/`: `<stage>.log` (child output and supervisor lines), `<stage>.child.pid`, `supervise-<stage>.pid`. `CENSUS_SUPERVISE_MIN_S` sets the minimum backoff (default 60).
  - `pipeline.audit.audit(ctx, stage: str) -> list[str]` — empty when nothing is lost or duplicated.
  - CLI: `census supervise <stage> [--edition E] [--limit N] [--pass a|b] [--detach]`; `census audit <stage> [--edition E]` (exit 1 on problems); `python -m pipeline.cli` works.
  - `census status` adds ` deferred=<n>` to a stage line and a line `s1 families done: ...; complete: <bool>`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_supervise.py`:

```python
import os

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
    assert (ctx.root / "logs" / "s1.child.pid").read_text() == "4242"
    assert not (ctx.root / "logs" / "supervise-s1.pid").exists()


def test_backoff_doubles_without_progress_and_resets_with_it(ctx):
    spawn, _ = script(ctx, [(1, 0), (1, 0), (1, 0), (2, 1), (1, 0), (0, 0)])
    sleeps = []
    sup.supervise(ctx, "s1", [], spawn=spawn, sleep=sleeps.append, min_backoff=1.0, max_backoff=3.0)
    assert sleeps == [2.0, 3.0, 3.0, 1.0, 2.0]


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
```

Create `tests/test_audit.py`:

```python
from pipeline.audit import audit
from pipeline.runner import Unit, run_batched


def hits(batch):
    return {"repo_hits": [{"repo": u.payload, "path": "CLAUDE.md", "component": "claude_md", "query_id": "q",
                           "blob_sha": u.key, "is_fork": False} for u in batch]}


def run(ctx, n=6):
    run_batched(ctx, "s1", [Unit(f"k{i}", f"o/r{i}") for i in range(n)], hits, batch_size=2, log=lambda m: None)


def test_clean_stage_has_no_problems(ctx):
    run(ctx)
    assert audit(ctx, "s1") == []


def test_a_journaled_part_that_is_gone_is_lost_rows(ctx):
    run(ctx)
    part = sorted(ctx.tables.parts("repo_hits"))[0]
    ctx.tables.delete_part("repo_hits", part)
    assert any("journaled but missing" in p for p in audit(ctx, "s1"))


def test_a_part_with_the_wrong_row_count_is_reported(ctx):
    run(ctx)
    part = sorted(ctx.tables.parts("repo_hits"))[0]
    ctx.tables.write_part("repo_hits", part, [])
    assert any("rows on disk differ from the journal" in p for p in audit(ctx, "s1"))


def test_duplicated_rows_and_unjournaled_parts_are_reported(ctx):
    run(ctx)
    ctx.tables.write_part("repo_hits", "p-extra", hits([Unit("k0", "o/r0")])["repo_hits"])
    problems = audit(ctx, "s1")
    assert any("no journal line" in p for p in problems)
    assert any("duplicated rows" in p for p in problems)


def test_a_unit_journaled_twice_is_reported(ctx):
    run(ctx)
    ctx.journal("s1").record("p-again", ["k0"], {"repo_hits": 0, "s1_overflows": 0})
    assert any("journaled more than once" in p for p in audit(ctx, "s1"))
```

Append to `tests/test_cli.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_supervise.py tests/test_audit.py tests/test_cli.py -q`
Expected: FAIL with `ImportError: cannot import name 'supervise' from 'pipeline'` and `ModuleNotFoundError: No module named 'pipeline.audit'`.

- [ ] **Step 3: Create `pipeline/supervise.py`**

```python
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
```

- [ ] **Step 4: Create `pipeline/audit.py`**

```python
"""census audit: after a kill and a resume, is anything lost or duplicated? (PRD §4, §9 M2)

Lost: a journaled part that is missing or short. Duplicated: a unit journaled twice, or a natural key
that appears in more than one row. A part with no journal line is reported too: it is normal while the
stage is running or before a killed stage is resumed, and a fault otherwise.
"""
from collections import Counter

import pyarrow.parquet as pq

from pipeline.context import Ctx
from pipeline.runner import STAGE_TABLES

NATURAL_KEYS = {
    "s1": [("repo_hits", ("repo", "path", "query_id"))],
    "s2": [("repos", ("repo",)), ("harness_files", ("repo", "path"))],
}


def audit(ctx: Ctx, stage: str) -> list[str]:
    problems: list[str] = []
    entries = ctx.journal(stage).entries()
    twice = sorted(u for u, n in Counter(u for e in entries for u in e["units"]).items() if n > 1)
    if twice:
        problems.append(f"{len(twice)} units journaled more than once, e.g. {twice[0]}")
    journaled = {e["part"] for e in entries}
    for t in STAGE_TABLES[stage]:
        on_disk = ctx.tables.parts(t)
        missing = sorted(journaled - on_disk)
        if missing:
            problems.append(f"{t}: {len(missing)} parts journaled but missing (lost rows), e.g. {missing[0]}")
        short = [e["part"] for e in entries if e["part"] in on_disk
                 and pq.read_metadata(ctx.tables.dir(t) / f"{e['part']}.parquet").num_rows != e["rows"].get(t, 0)]
        if short:
            problems.append(f"{t}: {len(short)} parts whose rows on disk differ from the journal, e.g. {short[0]}")
        orphans = on_disk - journaled
        if orphans:
            problems.append(f"{t}: {len(orphans)} parts have no journal line (the stage is running, "
                            "or a killed run has not been resumed)")
    con = ctx.tables.connect()
    for table, cols in NATURAL_KEYS.get(stage, []):
        key = ", ".join(cols)
        rows = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        distinct = con.execute(f"SELECT count(*) FROM (SELECT DISTINCT {key} FROM {table})").fetchone()[0]
        if rows != distinct:
            problems.append(f"{table}: {rows} rows but {distinct} distinct ({key}) (duplicated rows)")
    return problems
```

- [ ] **Step 5: Wire the CLI**

In `pipeline/cli.py`:

Add `import subprocess` to the imports and change the runner import to `from pipeline.runner import STAGE_TABLES, RunStats, read_state, reset`.

Replace `status` with:

```python
def status(ctx: Ctx) -> None:
    for stage in [*PIPELINE]:
        entries = ctx.journal(stage).entries()
        attempts = ctx.attempts(stage).counts()
        if not entries and not attempts:
            print(f"{stage}: not started")
            continue
        rows: Counter[str] = Counter()
        for e in entries:
            rows.update(e["rows"])
        done = {u for e in entries for u in e["units"]}
        deferred = sum(1 for u in attempts if u not in done)
        print(f"{stage}: units={len(done)} parts={len(entries)} "
              + " ".join(f"{t}={rows[t]}" for t in STAGE_TABLES[stage])
              + (f" deferred={deferred}" if deferred else ""))
    s1 = read_state(ctx, "s1")
    if s1:
        print(f"s1 families done: {', '.join(s1.get('families_done', [])) or 'none'}; "
              f"complete: {bool(s1.get('complete'))}")
```

In `_parser`, after the `labels` and `reredact` parsers, add:

```python
    a = sub.add_parser("audit", parents=[common])
    a.add_argument("stage", choices=[*STAGE_TABLES])
    s = sub.add_parser("supervise", parents=[common])
    s.add_argument("stage", choices=[*STAGE_TABLES])
    s.add_argument("--limit", type=int)
    s.add_argument("--pass", dest="pass_id", choices=["a", "b"], default="a")
    s.add_argument("--detach", action="store_true",
                   help="run in a new session, so the supervisor outlives the shell that started it")
```

In `main`, add these branches after the `reredact` branch:

```python
    elif args.cmd == "audit":
        from pipeline.audit import audit
        problems = audit(ctx, args.stage)
        for p in problems:
            print(p)
        if not problems:
            print(f"{args.stage}: nothing lost or duplicated")
        return 1 if problems else 0
    elif args.cmd == "supervise":
        run_args = ((["--limit", str(args.limit)] if args.limit is not None else [])
                    + (["--pass", args.pass_id] if args.pass_id != "a" else []))
        if args.detach:
            logs = ctx.root / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            out = logs / f"supervise-{args.stage}.out"
            cmd = [sys.executable, "-m", "pipeline.cli", "supervise", args.stage, "--edition", args.edition, *run_args]
            with out.open("ab") as f:
                proc = subprocess.Popen(cmd, start_new_session=True, stdin=subprocess.DEVNULL, stdout=f,
                                        stderr=subprocess.STDOUT)
            print(f"supervising {args.stage} as pid {proc.pid}; stage log {logs / (args.stage + '.log')}")
            return 0
        from pipeline.supervise import supervise
        return supervise(ctx, args.stage, run_args)
```

At the bottom of the file add:

```python
if __name__ == "__main__":
    entry()
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest -q`
Expected: PASS, 355 + 4 + 5 + 3 = 367.

- [ ] **Step 7: Commit**

```bash
git add pipeline/supervise.py pipeline/audit.py pipeline/cli.py tests/test_supervise.py tests/test_audit.py tests/test_cli.py
git commit -m "m2: census supervise restarts a stage until it completes; census audit checks for lost or duplicated rows"
```

---

### Task 9: kill -9 on real S1 and S2 processes

"Test resume by killing it, not by reasoning." The stages run as real processes under the real supervisor against a local fake GitHub, are killed with SIGKILL several times, and must end with exactly the tables an uninterrupted run produces.

**Files:**
- Create: `tests/fake_github.py`, `tests/test_kill_stages.py`

**Interfaces:**
- Consumes: `GITHUB_API_URL`, `CENSUS_PACE` (Task 2); `census supervise`, `<edition>/logs/<stage>.child.pid`, `CENSUS_SUPERVISE_MIN_S`, `audit` (Task 8); S1 full mode and its state (Task 6); S2 (Tasks 3, 7).
- Produces: `tests/fake_github.FakeGitHub()` with `.url`, `.close()`; `SECRET`.

- [ ] **Step 1: Create `tests/fake_github.py`**

```python
"""A local stand-in for the three GitHub endpoints S1 and S2 use, so whole stages can run as real processes
and be killed (CLAUDE.md: test resume by killing it, not by reasoning).

The corpus is synthetic and deterministic. Some repos fail once per token, so a run meets transient failures
the way a real one does: a meta query that answers null without NOT_FOUND, and a REST tree that answers 502.
"""
import hashlib
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

N_REPOS = 1300
SECRET = "Zq8Xv2Lm9Pw4Rt7Ky3Nb"
ALIAS = re.compile(r'(r\d+): repository\(owner: "([^"]+)", name: "([^"]+)"\) \{')
EXPR = re.compile(r'(f\d+): object\(expression: "HEAD:([^"]+)"\)')
OID = re.compile(r'(b\d+): object\(oid: "([0-9a-f]{40})"\)')
TREE = re.compile(r"/repos/([^/]+/[^/]+)/git/trees/([0-9a-f]{40})")


def sha(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


def repo_files(i: int) -> list[tuple[str, str]]:
    files = [("CLAUDE.md", f"# Project {i}\n\nUses Claude to maintain project {i}.\n" + "x" * (37 * (i % 11)))]
    if i % 3 == 0:
        files.append(("docs/CLAUDE.md", f"# Docs {i}\n" + "y" * (i % 5)))
    if i % 2 == 0:
        skill = i % 4
        settings = '{"env": {"CLOUD_API_KEY": "%s"}}\n' % SECRET if i % 40 == 0 else '{"model": "sonnet"}\n'
        files += [(".claude/settings.json", settings),
                  (f".claude/skills/s{skill}/SKILL.md", f"---\nname: s{skill}\n---\nUses Claude to run skill {skill}.\n"),
                  (f".claude/skills/s{skill}/scripts/run.sh", "#!/bin/sh\necho run\n")]
    if i % 5 == 0:
        files.append((".mcp.json", '{"mcpServers": {"db": {"command": "db-mcp"}}}\n'))
    return files


class FakeGitHub:
    def __init__(self, delay: float = 0.004) -> None:
        self.delay = delay
        self.repos: dict[str, dict] = {}
        self.files: list[dict] = []
        self.blobs: dict[str, str] = {}
        self.failures: dict[tuple, int] = {}
        self.lock = threading.Lock()
        for i in range(N_REPOS):
            name = f"o{i % 50}/r{i}"
            repo = {"i": i, "fork": i % 10 == 9, "gone": i % 97 == 0, "files": dict(repo_files(i))}
            self.repos[name] = repo
            for path, text in repo["files"].items():
                self.blobs[sha(text)] = text
                self.files.append({"repo": name, "fork": repo["fork"], "path": path, "sha": sha(text),
                                   "size": len(text)})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def fail_once(self, token: str, kind: str, name: str, times: int) -> bool:
        with self.lock:
            n = self.failures.get((token, kind, name), 0)
            if n >= times:
                return False
            self.failures[(token, kind, name)] = n + 1
            return True

    def search(self, q: str, page: int, per_page: int) -> dict:
        terms = q.split()
        forks = "fork:only" in terms
        out = [f for f in self.files if f["fork"] == forks]
        for t in terms:
            k, _, v = t.partition(":")
            if k == "filename":
                out = [f for f in out if f["path"].rsplit("/", 1)[-1] == v]
            elif k == "path" and v == "/":
                out = [f for f in out if "/" not in f["path"]]
            elif k == "path":
                out = [f for f in out if f["path"].startswith(v + "/")]
            elif k == "extension":
                out = [f for f in out if f["path"].endswith("." + v)]
            elif k == "size":
                lo, hi = v.split("..")
                out = [f for f in out if int(lo) <= f["size"] <= int(hi)]
        items = [{"name": f["path"].rsplit("/", 1)[-1], "path": f["path"], "sha": f["sha"],
                  "repository": {"full_name": f["repo"], "fork": f["fork"]}}
                 for f in out[:1000][(page - 1) * per_page: page * per_page]]
        return {"total_count": len(out), "incomplete_results": False, "items": items}

    def _claude(self, name: str) -> dict | None:
        files = {p[len(".claude/"):]: t for p, t in self.repos[name]["files"].items() if p.startswith(".claude/")}
        if not files:
            return None
        entries, dirs = [], set()
        for rel, text in files.items():
            if "/" in rel:
                dirs.add(rel.split("/", 1)[0])
            else:
                entries.append({"name": rel, "type": "blob", "oid": sha(text),
                                "object": {"byteSize": len(text), "isBinary": False}})
        entries += [{"name": d, "type": "tree", "oid": sha(f"dir:{name}:{d}"), "object": {}} for d in sorted(dirs)]
        return {"oid": sha(f"tree:{name}"), "entries": entries}

    def graphql(self, token: str, q: str) -> dict:
        marks = list(ALIAS.finditer(q))
        if not marks:
            return {"data": {"rateLimit": {"remaining": 4999}}}
        data, errors = {}, []
        for n, m in enumerate(marks):
            alias, name = m[1], f"{m[2]}/{m[3]}"
            block = q[m.end(): marks[n + 1].start() if n + 1 < len(marks) else len(q)]
            repo = self.repos.get(name)
            is_meta = "claude: object(" in block
            if repo is None or repo["gone"]:
                data[alias] = None
                errors.append({"type": "NOT_FOUND", "path": [alias], "message": "Could not resolve to a Repository"})
                continue
            if is_meta and repo["i"] % 61 == 1 and self.fail_once(token, "meta", name, 1):
                data[alias] = None
                errors.append({"type": "SERVICE_UNAVAILABLE", "path": [alias], "message": "try again"})
                continue
            node: dict = {}
            if is_meta:
                node = {"nameWithOwner": name, "stargazerCount": repo["i"] % 7, "isFork": repo["fork"],
                        "isTemplate": False, "createdAt": "2025-01-01T00:00:00Z", "pushedAt": "2026-01-01T00:00:00Z",
                        "primaryLanguage": {"name": "Python"}, "licenseInfo": None,
                        "defaultBranchRef": {"target": {"oid": sha(f"head:{name}")}}, "claude": self._claude(name)}
            for a, path in EXPR.findall(block):
                text = repo["files"].get(path)
                node[a] = None if text is None else {"oid": sha(text), "byteSize": len(text), "isBinary": False}
            for a, oid in OID.findall(block):
                node[a] = ({"oid": oid, "isBinary": False, "isTruncated": False, "text": self.blobs[oid]}
                           if oid in self.blobs else None)
            data[alias] = node
        return {"data": data, **({"errors": errors} if errors else {})}

    def tree(self, token: str, name: str) -> tuple[int, dict]:
        repo = self.repos[name]
        if repo["i"] % 53 == 2 and self.fail_once(token, "tree", name, 4):  # the client's four attempts all fail
            return 502, {"message": "Bad Gateway"}
        tree = [{"path": p[len(".claude/"):], "type": "blob", "sha": sha(t), "size": len(t)}
                for p, t in repo["files"].items() if p.startswith(".claude/")]
        return 200, {"sha": sha(f"tree:{name}"), "tree": tree, "truncated": False}

    def _handler(self):
        gh = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # keep test output clean
                pass

            def _send(self, status: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                time.sleep(gh.delay)
                url = urlparse(self.path)
                token = self.headers.get("Authorization", "")
                if url.path == "/search/code":
                    qs = parse_qs(url.query)
                    self._send(200, gh.search(qs["q"][0], int(qs["page"][0]), int(qs["per_page"][0])))
                elif m := TREE.fullmatch(url.path):
                    self._send(*gh.tree(token, m[1]))
                else:
                    self._send(404, {"message": "Not Found"})

            def do_POST(self) -> None:
                time.sleep(gh.delay)
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self._send(200, gh.graphql(self.headers.get("Authorization", ""), body["query"]))

        return Handler
```

- [ ] **Step 2: Create `tests/test_kill_stages.py`**

```python
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
```

1,300 repos with `i % 97 == 0` gone gives i = 0, 97, ..., 1261: 14 repos.

- [ ] **Step 3: Run the new tests**

Run: `uv run pytest tests/test_kill_stages.py -q`
Expected: PASS, 3 tests, in roughly one to two minutes. If a test fails, the failure is a real resume bug or a fake-server mismatch: use superpowers:systematic-debugging, read `<tmp>/work/e/logs/<stage>.log`, and fix the cause. Do not loosen an assertion.

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS, 367 + 3 = 370.

- [ ] **Step 5: Commit**

```bash
git add tests/fake_github.py tests/test_kill_stages.py
git commit -m "m2: kill -9 tests run real S1 and S2 processes under the supervisor against a fake GitHub"
```

---

### Task 10: Live rehearsal, measurements, launch (controller-run)

The controller runs this task, not an implementer subagent: it is hours of live operations with deliberate kills. Any code change it turns up goes to a subagent with review.

**Files:**
- Create: `docs/m2/rehearsal.md`, `docs/m2/runbook.md`
- Modify: `docs/PRD.md` (§4 S1 and S2 rows, §8 CLI line, §9.1 add "M2 rehearsal" numbers, §10 rate limits)

- [ ] **Step 1: Pre-flight**

```bash
pgrep -fl m0.lattice; gh auth status; df -h data | tail -1
```

If the M0 lattice is running, ask Michael before stopping it (decision 1). Do not start a live S1 while it runs.

- [ ] **Step 2: Rehearse S1 with a kill (edition `m2-rehearsal`, live GitHub)**

```bash
export CENSUS_DATA=/Users/michaelfornal/Documents/agent-census/data
uv run census supervise s1 --edition m2-rehearsal --limit 400 --detach
# when `uv run census status --edition m2-rehearsal` shows s1 parts >= 1:
kill -9 "$(cat $CENSUS_DATA/work/m2-rehearsal/logs/s1.child.pid)"
# wait for "supervise s1: complete" in logs/s1.log, then:
uv run census audit s1 --edition m2-rehearsal
```

Expected: the log shows `exit -9 ... restarting`, then `complete`; the audit prints `s1: nothing lost or duplicated`. Record requests, rate-limited share and successful requests/min.

- [ ] **Step 3: Rehearse S2 with a kill and measure the harvest**

```bash
uv run census supervise s2 --edition m2-rehearsal --limit 300 --detach
# after 3 journal lines:
kill -9 "$(cat $CENSUS_DATA/work/m2-rehearsal/logs/s2.child.pid)"
uv run census audit s2 --edition m2-rehearsal
uv run census status --edition m2-rehearsal
```

Expected: `s2: nothing lost or duplicated`. Record from `logs/s2.log` and the tables: repos/hr of wall time, GraphQL posts and 5xx share, REST requests and errors, deferred units, given-up units, `missing` by error, repos with `tree_truncated > 0`. Compare with M1: 480 repos/hr, 25% 5xx, 2.6% lost, 15.6% truncated.

Pass criteria for launching: no repo `missing` for a transient reason; `tree_truncated > 0` in under 1% of repos; at least 3,000 repos/hr. If the rate is lower, find the bound in the client stats (GraphQL points, REST pacing, blob batches) and change one constant through a reviewed commit, then repeat this step with `--limit 600`.

- [ ] **Step 4: Write `docs/m2/rehearsal.md`**

Same form as `docs/m1/slice-run.md`: what ran, the kills, the audit output, the measured numbers next to M1's, and the projected S2 wall time for the universe S1 is finding.

- [ ] **Step 5: Update the PRD to the measured mechanics**

§4 S1: only counts are cached; `incomplete_results` is retried. §4 S2: metadata and the top of `.claude` by batched GraphQL, the full tree by REST git trees, transient failures deferred and final after five attempts, the redaction version stored with each blob. §8 CLI: add `census supervise`, `census audit`, `census reredact`. §9.1: add the rehearsal table. §10: the S1 request estimate (about 55,000) and the disk need.

- [ ] **Step 6: Write `docs/m2/runbook.md`**

How to watch (`census status`, `tail logs/s1.log`), stop (`kill $(cat logs/supervise-s1.pid)` then the child), restart (the same `supervise --detach` command; also after a reboot), kill-test S2 on the full edition once it runs (`kill -9 $(cat logs/s2.child.pid)`, then `census audit s2`), move the blob store (`CENSUS_BLOBS`, rsync first), and what each stop message means.

- [ ] **Step 7: Commit the docs**

```bash
git add docs/m2 docs/PRD.md
git commit -m "m2: rehearsal measurements, runbook, PRD S1/S2 mechanics as built"
```

- [ ] **Step 8: Launch the full S1 and kill it once**

```bash
uv run census supervise s1 --edition fall-2026 --detach
# after the first journal lines:
kill -9 "$(cat $CENSUS_DATA/work/fall-2026/logs/s1.child.pid)"
uv run census audit s1 --edition fall-2026   # after the supervisor has restarted the stage
```

Expected: restart in the log; the audit reports only the in-flight part, if any, as having no journal line.

- [ ] **Step 9: Full S2**

Launch `uv run census supervise s2 --edition fall-2026 --detach` only after Michael has decided where the blobs go (decision 2). It waits for S1's claude_md families by itself. The mid-S2 kill on the full edition is in the runbook.

- [ ] **Step 10: Report**

State plainly what M2's exit criterion still needs: the full S1 to finish (about two weeks), the full S2 to run and be kill-tested on the full edition, and the final counts written to `docs/m2/run.md`.

---

## Self-Review

**Spec coverage.** PRD §9 M2 "whole repo universe harvested": Tasks 6, 7 make the stages able to; Task 10 launches S1 and states what remains. "Unattended": Task 8 (supervisor, detach, keep-awake), Task 2 (rate limits and outages waited out), Task 7 (disk guard). "Resume verified with kill -9 mid-S1 and mid-S2, no loss or duplication": Task 9 (real processes, fake GitHub), Task 10 (live GitHub, rehearsal edition and the full S1). Deferred fix 1: Tasks 1, 3, 4. Deferred fix 2: Task 5. PRD §4 S1 mechanics (pace, decay, 60 s floor, reset wait, both fork families) are unchanged from M1 and still tested in `tests/test_gh.py` and `tests/test_s1_discover.py`. PRD §4 S2 "a blob already in the store is not fetched again": Task 3, `test_each_new_blob_is_fetched_once_and_stored_redacted`. "About 6.4% of indexed repos are gone by harvest time; they are recorded as missing": Task 3, `not_found`.

**Gaps stated, not hidden.** The full S2 cannot start until the blob store has a volume with room. Loosening a redaction rule needs a refetch, which M3 owns. `harness_files` at full scale will not fit in memory in S3; M3 owns that.

**Type consistency.** `Partial(rows, deferred)` is produced by `harvest_batch` (Task 3) and S6's `work` (Task 4) and consumed by `run_batched` (Task 1). `give_up(unit, error) -> Rows` is passed in Tasks 3, 4, 7 and tested in Task 1. `read_state(ctx, "s1")["families_done"]` is written in Task 6 and read in Tasks 7 and 8. `RestClient.get -> (status, body)` is defined in Task 2 and used by `fetch_tree` in Task 3. `<edition>/logs/<stage>.child.pid` is written in Task 8 and read in Tasks 9 and 10.
