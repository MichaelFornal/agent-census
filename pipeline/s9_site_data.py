"""S9 site data (PRD §4 S9, §3): per-page JSON and evidence cards. Numbers live only in facts.json.

An evidence card is an excerpt of at most 25 lines (re-redacted as a second guard) with a permalink to
the exact commit and line range. Techniques in PRIVATE_CATEGORIES get anonymized cards (PRD §3).
Only use cases Michael approved in editorial/<edition>/taxonomy_labels.json get a page.
"""
import json
import re
import shutil
from pathlib import Path
from urllib.parse import quote

from pipeline.context import Ctx, Opts
from pipeline.detectors.catalog import PRIVATE_CATEGORIES, TECHNIQUES
from pipeline.editorial import load_labels, resolve
from pipeline.paths import facts_path
from pipeline.redact import redact
from pipeline.runner import RunStats
from pipeline.store import atomic_write

EXCERPT_MAX = 25
CARDS_PER_PAGE = 3
SKILLS_PER_USE_CASE = 10


def permalink(repo: str, head_oid: str | None, path: str, start: int, end: int) -> str | None:
    if not head_oid:
        return None
    return f"https://github.com/{repo}/blob/{head_oid}/{quote(path)}#L{start}-L{end}"


def card(ctx: Ctx, repo_row: dict, art: dict, start: int, end: int, anonymize: bool) -> dict:
    lines = ctx.blobs.get(art["blob_sha"]).splitlines()
    s = max(1, min(start, len(lines) or 1))
    e = max(s, min(end, len(lines), s + EXCERPT_MAX - 1))
    excerpt, _ = redact("\n".join(lines[s - 1:e]))
    return {"repo": None if anonymize else art["repo"], "path": art["path"], "kind": art["kind"],
            "permalink": None if anonymize else permalink(art["repo"], repo_row.get("head_oid"), art["path"], s, e),
            "excerpt": excerpt}


def _write(path: Path, obj: object) -> None:
    atomic_write(path, (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode())


def _skill_name(art: dict) -> str:
    name = (json.loads(art["parsed_json"]).get("frontmatter") or {}).get("name")
    return name if isinstance(name, str) and name else art["path"].split("/")[-2]


def run(ctx: Ctx, opts: Opts) -> RunStats:
    fp = facts_path(ctx.edition)
    if not fp.exists():
        raise SystemExit(f"no facts.json for {ctx.edition}; run `census facts --edition {ctx.edition}` first")
    out = ctx.site_data
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    _write(out / "facts.json", json.loads(fp.read_text()))
    repos = {r["repo"]: r for r in ctx.tables.read("repos")}
    arts = {a["artifact_id"]: a for a in ctx.tables.read("artifacts")}

    by_tech: dict[str, list[dict]] = {}
    for f in ctx.tables.read("features"):
        by_tech.setdefault(f["technique_id"], []).append(f)
    for tid, t in TECHNIQUES.items():
        private = t.category in PRIVATE_CATEGORIES
        feats = sorted(by_tech.get(tid, []), key=lambda f: (-(repos[f["repo"]]["stars"] or 0), f["repo"], f["path"]))
        cards = [card(ctx, repos[f["repo"]], arts[f["artifact_id"]], f["start_line"], f["end_line"], private)
                 for f in feats[:CARDS_PER_PAGE]]
        _write(out / "techniques" / f"{tid}.json", {"id": tid, "label": t.label, "category": t.category,
                                                    "definition": t.definition, "detection": "deterministic",
                                                    "private": private, "evidence": cards})

    labels = load_labels(ctx.edition)
    ucs = {u["use_case_id"]: u for u in ctx.tables.read("use_cases")}
    clusters = {c["cluster_id"]: c for c in ctx.tables.read("clusters")}
    members: dict[str, set[str]] = {}
    for m in ctx.tables.read("uc_membership"):
        members.setdefault(m["use_case_id"], set()).add(m["cluster_id"])
        parent = ucs[m["use_case_id"]]["parent_id"]
        if parent:
            members.setdefault(parent, set()).add(m["cluster_id"])
    approved: dict[str, str] = {}
    for uid, u in ucs.items():
        st = resolve(labels, uid)
        if st["status"] == "approved":
            label = st["label"] or u["label"]
            if re.search(r"\d", label):
                raise SystemExit(f"use case {uid}: label {label!r} has a digit, and page copy may not (PRD §7)")
            approved[uid] = label
    for uid, label in sorted(approved.items()):
        u = ucs[uid]
        cids = sorted(members.get(uid, set()), key=lambda c: (-clusters[c]["size"], c))
        canon = [arts[clusters[c]["canonical_artifact"]] for c in cids]
        _write(out / "use-cases" / f"{uid}.json", {
            "id": uid, "label": label, "parent_id": u["parent_id"],
            "parent_label": approved.get(u["parent_id"]) if u["parent_id"] else None,
            "non_coding": u["non_coding"],
            "examples": [card(ctx, repos[a["repo"]], a, 1, EXCERPT_MAX, False) for a in canon[:CARDS_PER_PAGE]],
            "skills": sorted({_skill_name(a) for a in canon if a["kind"] == "skill"})[:SKILLS_PER_USE_CASE],
        })

    kinds: dict[str, set[str]] = {}
    root_md: dict[str, dict] = {}
    for a in arts.values():
        kinds.setdefault(a["repo"], set()).add(a["kind"])
        if a["path"] == "CLAUDE.md":
            root_md[a["repo"]] = a
    eligible = [r for r in root_md if not repos[r]["missing"] and not repos[r]["canary"]]
    best = min(eligible, key=lambda r: (-len(kinds[r]), -(repos[r]["stars"] or 0), r), default=None)
    evidence = [card(ctx, repos[best], root_md[best], 1, EXCERPT_MAX, False)] if best else []
    _write(out / "findings" / "anatomy.json", {"id": "anatomy", "evidence": evidence})
    n = len(TECHNIQUES) + len(approved) + 1
    return RunStats("s9", units_total=n, units_run=n)
