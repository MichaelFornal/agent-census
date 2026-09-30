import hashlib

from helpers import run_until

from pipeline import s4_dedup as s4
from pipeline.context import Opts
from pipeline.kinds import artifact_id

WORDS = " ".join(f"word{i}" for i in range(60))


def doc(i, text, repo="o/r", kind="claude_md", created="2025-01-01T00:00:00Z", family=None):
    return s4.Doc(f"a{i}", kind, repo, "CLAUDE.md", hashlib.sha1(text.encode()).hexdigest(), text, created, family)


def cluster_of(out):
    return {m["artifact_id"]: m["cluster_id"] for m in out["membership"]}


def test_exact_copies_share_cluster_and_are_verbatim():
    out = s4.dedup([doc(0, WORDS, "o/a"), doc(1, WORDS, "o/b", created="2026-01-01T00:00:00Z")])
    c = cluster_of(out)
    assert c["a0"] == c["a1"]
    assert [(m["artifact_id"], m["tier"]) for m in out["membership"]] == [("a0", "origin"), ("a1", "exact")]
    assert out["mutations"] == [{"artifact_id": "a1", "cluster_id": c["a1"], "mutation_class": "verbatim"}]
    assert out["clusters"][0]["size"] == 2 and out["clusters"][0]["canonical_artifact"] == "a0"


def test_retargeted_copy_joins_by_normalized_hash():
    origin = doc(0, "# Webapp\n\nRun the webapp tests before merging any change to the webapp.", "acme/webapp")
    copy = doc(1, "# Shop\n\nRun the shop tests before merging any change to the shop.", "theta/shop",
               created="2026-01-01T00:00:00Z")
    out = s4.dedup([origin, copy])
    assert len(out["clusters"]) == 1
    assert out["membership"][1]["tier"] == "normalized"
    assert out["mutations"][0]["mutation_class"] == "retargeted"


def test_trimmed_and_extended_copies():
    trimmed = " ".join(WORDS.split()[:55])
    extended = WORDS + " extra1 extra2 extra3 extra4 extra5"
    out = s4.dedup([doc(0, WORDS), doc(1, trimmed, created="2026-01-01T00:00:00Z"),
                    doc(2, extended, created="2026-02-01T00:00:00Z")])
    assert len(out["clusters"]) == 1
    assert {m["artifact_id"]: m["mutation_class"] for m in out["mutations"]} == {"a1": "trimmed", "a2": "extended"}


def test_rewritten_copy_is_classed_rewritten():
    other = " ".join(f"other{i}" for i in range(30)) + " " + " ".join(WORDS.split()[:30])
    assert s4.mutation_class(doc(1, other), doc(0, WORDS)) == "rewritten"


def test_origin_is_earliest_created_repo_and_upstream_lib_is_recorded():
    out = s4.dedup([doc(0, WORDS, "x/late", created="2026-03-01T00:00:00Z"),
                    doc(1, WORDS, "anthropics/skills", created="2025-06-01T00:00:00Z"),
                    doc(2, WORDS, "y/early", created="2024-01-01T00:00:00Z")])
    assert out["lineage"] == [{"cluster_id": out["clusters"][0]["cluster_id"], "origin_repo": "y/early",
                               "origin_basis": "repo_created_at", "upstream_lib": "anthropics/skills"}]


def test_kinds_never_merge():
    out = s4.dedup([doc(0, WORDS, kind="claude_md"), doc(1, WORDS, kind="skill")])
    assert len(out["clusters"]) == 2


def test_family_key():
    assert s4.family_key("skill", ".claude/skills/csv-cleaner/SKILL.md", {"frontmatter": {"name": "CSV Cleaner"}}) == "csv-cleaner"
    assert s4.family_key("skill", ".claude/skills/csv-cleaner/SKILL.md", {"frontmatter": {}}) == "csv-cleaner"
    assert s4.family_key("agent", ".claude/agents/Reviewer.md", {}) == "reviewer"
    assert s4.family_key("claude_md", "CLAUDE.md", {}) is None


def test_s4_on_fixtures(fctx):
    run_until(fctx, "s4")
    c = {m["artifact_id"]: m["cluster_id"] for m in fctx.tables.read("membership")}
    skill = ".claude/skills/csv-cleaner/SKILL.md"
    assert c[artifact_id("beta/data-pipeline", skill)] == c[artifact_id("delta/skills-fork", skill)]
    assert c[artifact_id("acme/webapp", "CLAUDE.md")] == c[artifact_id("theta/shop", "CLAUDE.md")]
    muts = {m["artifact_id"]: m["mutation_class"] for m in fctx.tables.read("mutations")}
    assert muts[artifact_id("theta/shop", "CLAUDE.md")] == "retargeted"
    assert muts[artifact_id("delta/skills-fork", skill)] == "verbatim"
    assert len(fctx.tables.read("clusters")) == 19
    fams = {m["family_key"] for m in fctx.tables.read("membership") if m["family_key"]}
    assert {"csv-cleaner", "reviewer", "chapter-outliner", "fix-issue", "release", "notes"} <= fams
    assert s4.run(fctx, Opts()).units_skipped == 1
