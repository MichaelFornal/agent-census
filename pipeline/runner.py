"""Idempotent, resumable stage execution (PRD §4).

A batch of units writes one Parquet part per output table, then one journal line naming the part
and its units. A part with no journal line is an orphan from a killed run; it is deleted before
the next run starts, so kill -9 at any point loses nothing and duplicates nothing.
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pipeline.journal import part_name, unit_key

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
class RunStats:
    stage: str
    units_total: int = 0
    units_skipped: int = 0
    units_run: int = 0
    rows: dict[str, int] = field(default_factory=dict)
    stopped: str | None = None


def merge_stats(total: RunStats, part: RunStats) -> None:
    total.units_total += part.units_total
    total.units_skipped += part.units_skipped
    total.units_run += part.units_run
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


def run_batched(ctx, stage: str, units: list[Unit], work: Callable[[list[Unit]], Rows], batch_size: int,
                limit: int | None = None, log: Callable[[str], None] = print) -> RunStats:
    _clean_orphans(ctx, stage)
    if limit is not None:
        units = units[:limit]  # a stable prefix: rerunning with the same limit resumes the same slice
    done = ctx.journal(stage).done_units()
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
        except StopStage as e:
            stats.stopped = str(e)
            break
        _commit(ctx, stage, [u.key for u in batch], out, stats)
        log(f"{stage}: {stats.units_run}/{len(todo)} units")
    return stats


def run_whole(ctx, stage: str, fingerprint: str, work: Callable[[], Rows],
              log: Callable[[str], None] = print) -> RunStats:
    """For stages computed over whole tables (dedup, taxonomy): recompute only when inputs change."""
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
    ctx.journal(stage).clear()  # journal first: a kill after this leaves orphans, which are cleaned
    for t in STAGE_TABLES[stage]:
        ctx.tables.clear(t)
