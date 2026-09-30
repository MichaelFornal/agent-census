"""S9 site data (PRD §4 S9, §3): per-page JSON and evidence cards. Numbers live only in facts.json.

An evidence card is an excerpt of at most 25 lines (re-redacted as a second guard) with a permalink to
the exact commit and line range. Techniques in PRIVATE_CATEGORIES get anonymized cards (PRD §3).
Only use cases Michael approved in editorial/<edition>/taxonomy_labels.json get a page.
"""
import json
import os
import re
import shutil
from pathlib import Path, PurePosixPath
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
MARKER = ".s9-site-data"


def permalink(repo: str, head_oid: str | None, path: str, start: int, end: int) -> str | None:
    if not head_oid:
        return None
    return f"https://github.com/{repo}/blob/{head_oid}/{quote(path)}#L{start}-L{end}"


def card(ctx: Ctx, repo_row: dict, art: dict, start: int, end: int, anonymize: bool) -> dict:
    if anonymize:  # private category: no repo, permalink, directory or content (PRD §3)
        return {"repo": None, "path": PurePosixPath(art["path"]).name, "kind": art["kind"],
                "permalink": None, "excerpt": None}
    lines = ctx.blobs.get(art["blob_sha"]).splitlines()
    s = max(1, min(start, len(lines) or 1))
    e = max(s, min(end, len(lines), s + EXCERPT_MAX - 1))
    excerpt, _ = redact("\n".join(lines[s - 1:e]))
    return {"repo": art["repo"], "path": art["path"], "kind": art["kind"],
            "permalink": permalink(art["repo"], repo_row.get("head_oid"), art["path"], s, e),
            "excerpt": excerpt}


def _write(path: Path, obj: object) -> None:
    atomic_write(path, (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode())


def _skill_name(art: dict) -> str:
    name = (json.loads(art["parsed_json"]).get("frontmatter") or {}).get("name")
    if isinstance(name, str) and name:
        return name
    p = PurePosixPath(art["path"])
    return p.parent.name or p.stem


def representative_skills(canon: list[dict]) -> list[str]:
    """Skill names in cluster-size order (canon's order), deduplicated; names with a digit are dropped
    because page copy may not contain one (PRD §7)."""
    names = dict.fromkeys(_skill_name(a) for a in canon if a["kind"] == "skill")
    return [n for n in names if not re.search(r"\d", n)][:SKILLS_PER_USE_CASE]


def run(ctx: Ctx, opts: Opts) -> RunStats:
    fp = facts_path(ctx.edition)
    if not fp.exists():
        raise SystemExit(f"no facts.json for {ctx.edition}; run `census facts --edition {ctx.edition}` first")
    out = ctx.site_data
    repos = {r["repo"]: r for r in ctx.tables.read("repos")}
    ucs = {u["use_case_id"]: u for u in ctx.tables.read("use_cases")}
    labels = load_labels(ctx.edition)
    approved: dict[str, str] = {}
    for uid, u in ucs.items():
        st = resolve(labels, uid)
        if st["status"] == "approved":
            label = st["label"] or u["label"]
            if re.search(r"\d", label):
                raise SystemExit(f"use case {uid}: label {label!r} has a digit, and page copy may not (PRD §7)")
            approved[uid] = label
    if out.exists() and any(out.iterdir()) and not (out / MARKER).exists():
        raise SystemExit(f"refusing to replace {out}: not an S9 site-data directory")
    tmp = out.with_name(out.name + f".tmp-{os.getpid()}")
    if tmp.exists():
        shutil.rmtree(tmp)
    try:
        tmp.mkdir(parents=True)
        atomic_write(tmp / MARKER, b"")
        n = _build(ctx, tmp, fp, repos, ucs, approved)
        old = out.with_name(out.name + ".old")
        if old.exists():
            shutil.rmtree(old)
        if out.exists():
            out.rename(old)
        tmp.rename(out)
        shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return RunStats("s9", units_total=n, units_run=n)


def _build(ctx: Ctx, out: Path, fp: Path, repos: dict, ucs: dict, approved: dict) -> int:
    hidden = {r for r, row in repos.items() if row["canary"] or row["missing"]}
    _write(out / "facts.json", json.loads(fp.read_text()))
    arts = {a["artifact_id"]: a for a in ctx.tables.read("artifacts")}

    by_tech: dict[str, list[dict]] = {}
    for f in ctx.tables.read("features"):
        if f["repo"] in hidden:
            continue
        by_tech.setdefault(f["technique_id"], []).append(f)
    for tid, t in TECHNIQUES.items():
        private = t.category in PRIVATE_CATEGORIES
        feats = sorted(by_tech.get(tid, []), key=lambda f: (-(repos[f["repo"]]["stars"] or 0), f["repo"], f["path"]))
        cards = [card(ctx, repos[f["repo"]], arts[f["artifact_id"]], f["start_line"], f["end_line"], private)
                 for f in feats[:CARDS_PER_PAGE]]
        _write(out / "techniques" / f"{tid}.json", {"id": tid, "label": t.label, "category": t.category,
                                                    "definition": t.definition, "detection": "deterministic",
                                                    "private": private, "evidence": cards})

    clusters = {c["cluster_id"]: c for c in ctx.tables.read("clusters")}
    members: dict[str, set[str]] = {}
    for m in ctx.tables.read("uc_membership"):
        members.setdefault(m["use_case_id"], set()).add(m["cluster_id"])
        parent = ucs[m["use_case_id"]]["parent_id"]
        if parent:
            members.setdefault(parent, set()).add(m["cluster_id"])
    for uid, label in sorted(approved.items()):
        u = ucs[uid]
        cids = sorted(members.get(uid, set()), key=lambda c: (-clusters[c]["size"], c))
        canon = [a for a in (arts[clusters[c]["canonical_artifact"]] for c in cids) if a["repo"] not in hidden]
        _write(out / "use-cases" / f"{uid}.json", {
            "id": uid, "label": label, "parent_id": u["parent_id"],
            "parent_label": approved.get(u["parent_id"]) if u["parent_id"] else None,
            "non_coding": u["non_coding"],
            "examples": [card(ctx, repos[a["repo"]], a, 1, EXCERPT_MAX, False) for a in canon[:CARDS_PER_PAGE]],
            "skills": representative_skills(canon),
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
    return n
