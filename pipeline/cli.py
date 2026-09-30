"""census: the pipeline's command line (PRD §8)."""
import argparse
import importlib
import sys
from collections import Counter
from pathlib import Path

from pipeline.context import Ctx, Opts, make_ctx
from pipeline.paths import DEFAULT_EDITION
from pipeline.runner import STAGE_TABLES, RunStats, reset

PIPELINE = ["s1", "s2", "s3", "s4", "s5", "s6", "s7"]
MODULES = {
    "s1": "pipeline.s1_discover", "s2": "pipeline.s2_harvest", "s3": "pipeline.s3_parse",
    "s4": "pipeline.s4_dedup", "s5": "pipeline.s5_features", "s6": "pipeline.s6_extract",
    "s7": "pipeline.s7_taxonomy", "s8": "pipeline.s8_facts", "s9": "pipeline.s9_site_data",
}


def _stage_run(name: str):
    return importlib.import_module(MODULES[name]).run


def run_stage(name: str, ctx: Ctx, opts: Opts) -> RunStats:
    stats = _stage_run(name)(ctx, opts)
    line = (f"{stats.stage}: ran {stats.units_run}, skipped {stats.units_skipped} of {stats.units_total} units; "
            f"rows {dict(sorted(stats.rows.items()))}")
    print(line + (f"; stopped: {stats.stopped}" if stats.stopped else ""), flush=True)
    return stats


def _freeze(ctx: Ctx) -> None:
    from pipeline.freeze import freeze
    m = freeze(ctx)
    print(f"frozen {ctx.edition}: {m['edition_hash']}")


def run_all(ctx: Ctx, opts: Opts) -> int:
    for s in PIPELINE:
        if run_stage(s, ctx, opts).stopped:
            return 2
    _freeze(ctx)
    if run_stage("s8", ctx, Opts()).stopped:
        return 2
    return 2 if run_stage("s9", ctx, Opts()).stopped else 0


def status(ctx: Ctx) -> None:
    for stage in [*PIPELINE]:
        entries = ctx.journal(stage).entries()
        if not entries:
            print(f"{stage}: not started")
            continue
        rows: Counter[str] = Counter()
        for e in entries:
            rows.update(e["rows"])
        units = sum(len(e["units"]) for e in entries)
        print(f"{stage}: units={units} parts={len(entries)} "
              + " ".join(f"{t}={rows[t]}" for t in STAGE_TABLES[stage]))


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--edition", default=DEFAULT_EDITION)
    ap = argparse.ArgumentParser(prog="census")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", parents=[common])
    r.add_argument("stage", choices=[*MODULES, "all"])
    r.add_argument("--limit", type=int,
                   help="work on the first N units in the stage's stable order; for s1, the target number of repos")
    r.add_argument("--reset", action="store_true", help="drop this stage's outputs and journal first")
    r.add_argument("--fixtures", type=Path, help="run offline from fixture harnesses (implies fake LLM, hash embedder)")
    r.add_argument("--llm", choices=["claude", "fake"])
    r.add_argument("--embedder", choices=["bge", "hash"])
    r.add_argument("--pass", dest="pass_id", choices=["a", "b"], default="a")
    r.add_argument("--site-data", type=Path)
    sub.add_parser("status", parents=[common])
    sub.add_parser("freeze", parents=[common])
    sub.add_parser("labels", parents=[common])
    f = sub.add_parser("facts", parents=[common])
    f.add_argument("--check", action="store_true")
    f.add_argument("--site-data", type=Path, help="with --check, where the site's copy of facts.json lives")
    return ap


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.cmd == "run":
        if args.reset and args.stage in ("all", "s8", "s9"):
            parser.error("--reset applies to one of s1..s7")
        offline = args.fixtures is not None
        kw = {"fixtures": args.fixtures, "llm": args.llm or ("fake" if offline else "claude"),
              "embedder": args.embedder or ("hash" if offline else "bge")}
        if args.site_data:
            kw["site_data"] = args.site_data
        ctx = make_ctx(args.edition, **kw)
        opts = Opts(limit=args.limit, pass_id=args.pass_id)
        if args.stage == "all":
            return run_all(ctx, opts)
        if args.reset and args.stage in STAGE_TABLES:
            reset(ctx, args.stage)
        return 2 if run_stage(args.stage, ctx, opts).stopped else 0
    ctx = make_ctx(args.edition)
    if args.cmd == "status":
        status(ctx)
    elif args.cmd == "freeze":
        _freeze(ctx)
    elif args.cmd == "labels":
        from pipeline.editorial import export_drafts
        print(f"wrote {export_drafts(ctx)}")
    elif args.cmd == "facts":
        if args.site_data:
            ctx.site_data = args.site_data
        return 2 if run_stage("s8", ctx, Opts(check=args.check)).stopped else 0
    return 0


def entry() -> None:
    sys.exit(main())
