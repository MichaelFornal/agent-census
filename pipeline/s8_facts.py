"""S8 facts (PRD §4 S8, §7): every published number is a named SQL query in facts/ over the frozen edition.

A query starts with `-- kind: scalar` or `-- kind: series` and may read only the canary-safe v_* views.
`census facts --check` recomputes everything and fails on any drift, in values or in the data itself.
"""
import json
import math
import re
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from pipeline.context import Ctx, Opts
from pipeline.freeze import edition_hash, load_manifest
from pipeline.paths import REPO_ROOT, facts_path
from pipeline.runner import RunStats
from pipeline.store import atomic_write

FACTS_DIR = REPO_ROOT / "facts"
VIEWS = Path(__file__).with_name("views.sql")
KIND_RE = re.compile(r"^--\s*kind:\s*(scalar|series)\s*$", re.M)
TABLE_REF = re.compile(r"\b(?:from|join)\s+([A-Za-z_]\w*)", re.I)


def connect(ctx: Ctx) -> duckdb.DuckDBPyConnection:
    con = ctx.tables.connect()
    con.execute(VIEWS.read_text())
    return con


def check_sql(name: str, sql: str) -> str:
    m = KIND_RE.search(sql)
    if not m:
        raise ValueError(f"{name}: missing '-- kind: scalar|series' header")
    bad = sorted({t for t in TABLE_REF.findall(sql) if not t.startswith("v_")})
    if bad:
        raise ValueError(f"{name}: facts may read only canary-safe v_* views, found {bad}")
    return m.group(1)


def _plain(v: Any) -> Any:
    return float(v) if isinstance(v, Decimal) else v


def compute(ctx: Ctx, facts_dir: Path = FACTS_DIR) -> dict[str, Any]:
    con = connect(ctx)
    values: dict[str, Any] = {}
    for f in sorted(facts_dir.glob("*.sql")):
        sql = f.read_text()
        kind = check_sql(f.name, sql)
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        if kind == "scalar":
            if len(rows) != 1 or len(cols) != 1:
                raise ValueError(f"{f.name}: a scalar fact must return one row and one column")
            values[f.stem] = _plain(rows[0][0])
        else:
            values[f.stem] = [{c: _plain(v) for c, v in zip(cols, r)} for r in rows]
    return values


def same(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return a is not None and b is not None and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    return a == b


def run(ctx: Ctx, opts: Opts) -> RunStats:
    manifest = load_manifest(ctx.edition)
    current, _ = edition_hash(ctx)
    if current != manifest["edition_hash"]:
        raise SystemExit(f"{ctx.edition}: data changed since freeze ({current[:12]} != "
                         f"{manifest['edition_hash'][:12]}); run census freeze again")
    values = compute(ctx)
    path = facts_path(ctx.edition)
    if opts.check:
        if not path.exists():
            raise SystemExit(f"no facts.json for {ctx.edition}; run `census facts` first")
        old = json.loads(path.read_text())
        drift = [k for k in sorted(set(values) | set(old["facts"]))
                 if k not in values or k not in old["facts"] or not same(values[k], old["facts"][k]["value"])]
        if old["edition_hash"] != manifest["edition_hash"]:
            drift.append("edition_hash")
        if drift:
            raise SystemExit(f"facts --check failed; drift in {drift}")
        print(f"facts --check: {len(values)} facts match edition {manifest['edition_hash'][:12]}")
        return RunStats("s8", units_total=len(values), units_skipped=len(values))
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    doc = {"edition": ctx.edition, "edition_hash": manifest["edition_hash"], "frozen_at": manifest["frozen_at"],
           "facts": {k: {"id": k, "query_file": f"facts/{k}.sql", "edition_hash": manifest["edition_hash"],
                         "value": v, "computed_at": now} for k, v in values.items()}}
    atomic_write(path, (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode())
    return RunStats("s8", units_total=len(values), units_run=len(values))
