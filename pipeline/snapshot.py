"""Copy another edition's journaled S1 output into this edition, so a preview edition can be harvested
(`census run s2 --limit N`) while the source edition's S1 is still running.

Only parts the source journal has recorded are copied: a part file the source is still writing has no
journal line yet. Each part's files are copied before its journal line is written, so a kill leaves the part
either absent from this journal (and copied again on rerun) or complete. The S1 state file is not copied:
this edition never claims S1 is complete, so S2 runs here only with --limit.
"""
from pipeline.context import Ctx
from pipeline.runner import STAGE_TABLES
from pipeline.store import atomic_write


def snapshot_s1(src: Ctx, dst: Ctx) -> tuple[int, int]:
    """Returns (parts copied, parts already present)."""
    if src.root == dst.root:
        raise SystemExit("snapshot source and destination are the same edition")
    have = dst.journal("s1").parts()
    copied = skipped = 0
    for e in src.journal("s1").entries():
        if e["part"] in have:
            skipped += 1
            continue
        for table in STAGE_TABLES["s1"]:
            f = src.tables.dir(table) / f"{e['part']}.parquet"
            if f.exists():
                atomic_write(dst.tables.dir(table) / f.name, f.read_bytes())
        dst.journal("s1").record(e["part"], e["units"], e["rows"])
        have.add(e["part"])
        copied += 1
    return copied, skipped
