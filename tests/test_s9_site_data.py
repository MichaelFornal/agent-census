import json

import pytest
import zstandard
from helpers import run_until

from pipeline import s8_facts, s9_site_data as s9
from pipeline.context import Opts
from pipeline.detectors.catalog import TECHNIQUES
from pipeline.freeze import freeze
from pipeline.kinds import artifact_id
from pipeline.store import atomic_write


def build(fctx, monkeypatch, labels):
    run_until(fctx, "s7")
    freeze(fctx)
    s8_facts.run(fctx, Opts())
    monkeypatch.setattr(s9, "load_labels", lambda edition: labels)
    s9.run(fctx, Opts())
    return fctx.site_data


def read(path):
    return json.loads(path.read_text())


def test_permalink_format():
    assert s9.permalink("o/r", "abc", ".claude/settings.json", 7, 7) == \
        "https://github.com/o/r/blob/abc/.claude/settings.json#L7-L7"
    assert s9.permalink("o/r", None, "CLAUDE.md", 1, 2) is None


def test_card_clips_to_25_lines_and_re_redacts(ctx):
    secret = "ghp_" + "A1b2C3d4E5" * 4
    text = "\n".join([f"line {i}" for i in range(1, 41)] + [f"token {secret}"])
    oid = "ab" * 20
    ctx.blobs._put_bytes(oid, zstandard.ZstdCompressor().compress(text.encode()))  # bypass put()
    art = {"repo": "o/r", "path": "CLAUDE.md", "kind": "claude_md", "blob_sha": oid}
    c = s9.card(ctx, {"head_oid": "h"}, art, 30, 100, False)
    assert c["excerpt"].splitlines()[0] == "line 30" and len(c["excerpt"].splitlines()) == 12
    assert secret not in c["excerpt"] and c["permalink"].endswith("#L30-L41")
    assert len(s9.card(ctx, {"head_oid": "h"}, art, 1, 100, False)["excerpt"].splitlines()) == 25


def test_technique_pages_and_anonymized_permissions(fctx, monkeypatch):
    out = build(fctx, monkeypatch, {"*": {"status": "approved"}})
    assert {p.stem for p in (out / "techniques").glob("*.json")} == set(TECHNIQUES)
    gate = read(out / "techniques" / "hook_stop_gate.json")
    assert gate["evidence"][0]["repo"] == "acme/webapp"
    assert gate["evidence"][0]["permalink"].endswith("/.claude/settings.json#L7-L7")
    assert "npm test --silent" in gate["evidence"][0]["excerpt"]
    deny = read(out / "techniques" / "permissions_deny.json")
    assert deny["private"] is True
    assert deny["evidence"] and all(c["repo"] is None and c["permalink"] is None for c in deny["evidence"])
    assert all(c["excerpt"] is None and "/" not in c["path"] for c in deny["evidence"])


def test_only_approved_use_cases_are_written(fctx, monkeypatch):
    out = build(fctx, monkeypatch, {})
    assert not list((out / "use-cases").glob("*.json"))
    out = build(fctx, monkeypatch, {"*": {"status": "approved"}})
    pages = [read(p) for p in (out / "use-cases").glob("*.json")]
    assert len(pages) == len(fctx.tables.read("use_cases"))
    csv = next(p for p in pages if "csv-cleaner" in p["skills"])
    assert csv["examples"] and all(c["permalink"] for c in csv["examples"])


def test_representative_skills_follow_cluster_size_order_and_drop_digit_names():
    def skill(name):
        return {"kind": "skill", "path": f".claude/skills/{name}/SKILL.md", "parsed_json": json.dumps(
            {"frontmatter": {"name": name}})}
    canon = [skill("zeta"), skill("v2-tool"), skill("alpha"), skill("zeta"), {**skill("agent-x"), "kind": "agent"},
             skill("mid")]
    assert s9.representative_skills(canon) == ["zeta", "alpha", "mid"]  # size order, deduped, no digits, no non-skills
    many = [skill(f"s{chr(97 + i)}") for i in range(s9.SKILLS_PER_USE_CASE + 3)]
    assert s9.representative_skills(many) == [f"s{chr(97 + i)}" for i in range(s9.SKILLS_PER_USE_CASE)]


def test_anatomy_evidence_is_the_richest_harness(fctx, monkeypatch):
    out = build(fctx, monkeypatch, {})
    [card] = read(out / "findings" / "anatomy.json")["evidence"]
    assert (card["repo"], card["path"]) == ("acme/webapp", "CLAUDE.md")
    assert card["excerpt"].startswith("# Webapp")
    assert read(out / "facts.json")["facts"]["harnesses_total"]["value"] == 8


def test_digit_in_approved_label_is_refused(fctx, monkeypatch):
    with pytest.raises(SystemExit, match="digit"):
        build(fctx, monkeypatch, {"*": {"status": "approved", "label": "Top 10 apps"}})


def prep(fctx):
    run_until(fctx, "s7")
    freeze(fctx)
    s8_facts.run(fctx, Opts())


def test_unmarked_non_empty_site_data_is_never_replaced(fctx, monkeypatch):
    prep(fctx)
    monkeypatch.setattr(s9, "load_labels", lambda edition: {})
    fctx.site_data.mkdir(parents=True, exist_ok=True)
    (fctx.site_data / "mine.txt").write_text("keep")
    with pytest.raises(SystemExit, match="refusing to replace"):
        s9.run(fctx, Opts())
    assert (fctx.site_data / "mine.txt").read_text() == "keep"
    assert not list(fctx.site_data.parent.glob("*.tmp-*"))


def test_failed_run_leaves_previous_tree_and_rerun_leaves_no_siblings(fctx, monkeypatch):
    out = build(fctx, monkeypatch, {})
    with pytest.raises(SystemExit, match="digit"):
        monkeypatch.setattr(s9, "load_labels", lambda edition: {"*": {"status": "approved", "label": "Top 10"}})
        s9.run(fctx, Opts())
    assert (out / "facts.json").exists()
    monkeypatch.setattr(s9, "load_labels", lambda edition: {})
    s9.run(fctx, Opts())
    assert (out / "facts.json").exists()
    names = {p.name for p in out.parent.iterdir()}
    assert not [n for n in names if ".tmp-" in n or n.endswith(".old")]


def test_canary_and_missing_repos_never_surface(fctx, monkeypatch):
    run_until(fctx, "s7")
    t = fctx.tables
    src_repo = next(r for r in t.read("repos") if r["repo"] == "acme/webapp")
    src_arts = [a for a in t.read("artifacts") if a["repo"] == "acme/webapp"]
    src_feats = [f for f in t.read("features") if f["repo"] == "acme/webapp"]
    mem = {m["artifact_id"]: m for m in t.read("membership")}
    for repo, flag in (("canary/x", "canary"), ("gone/y", "missing")):
        new_ids = {a["artifact_id"]: artifact_id(repo, a["path"]) for a in src_arts}
        t.write_part("repos", f"p-{flag}", [{**src_repo, "repo": repo, flag: True, "stars": 10**6}])
        t.write_part("artifacts", f"p-{flag}",
                     [{**a, "repo": repo, "artifact_id": new_ids[a["artifact_id"]]} for a in src_arts])
        t.write_part("membership", f"p-{flag}",
                     [{**mem[old], "artifact_id": new} for old, new in new_ids.items()])
        t.write_part("features", f"p-{flag}",
                     [{**f, "repo": repo, "artifact_id": new_ids.get(f["artifact_id"], f["artifact_id"])}
                      for f in src_feats])
    only = artifact_id("canary/x", "ONLY.md")
    t.write_part("artifacts", "p-only", [{**src_arts[0], "repo": "canary/x", "artifact_id": only, "path": "ONLY.md"}])
    t.write_part("clusters", "p-only", [{"cluster_id": "c-only", "kind": src_arts[0]["kind"],
                                         "canonical_artifact": only, "size": 10**6}])
    t.write_part("membership", "p-only", [{"artifact_id": only, "cluster_id": "c-only", "tier": "exact",
                                           "family_key": None}])
    freeze(fctx)
    s8_facts.run(fctx, Opts())
    monkeypatch.setattr(s9, "load_labels", lambda edition: {"*": {"status": "approved"}})
    s9.run(fctx, Opts())
    for p in list((fctx.site_data / "techniques").glob("*.json")) + list((fctx.site_data / "use-cases").glob("*.json")):
        assert "canary/" not in p.read_text() and "gone/" not in p.read_text()


def test_skill_name_without_parent_directory():
    art = {"path": "SKILL.md", "parsed_json": "{}"}
    assert s9._skill_name(art) == "SKILL"
