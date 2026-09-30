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
