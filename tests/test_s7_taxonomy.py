import json

from helpers import run_until

from pipeline import editorial, s7_taxonomy as s7
from pipeline.context import Opts
from pipeline.kinds import artifact_id


def clusters(fctx):
    return {m["artifact_id"]: m["cluster_id"] for m in fctx.tables.read("membership")}


def test_s7_on_fixtures(fctx):
    run_until(fctx, "s7")
    mem = {m["cluster_id"]: m["use_case_id"] for m in fctx.tables.read("uc_membership")}
    c = clusters(fctx)
    web = [c[artifact_id("acme/webapp", p)] for p in
           ("CLAUDE.md", "packages/api/CLAUDE.md", ".claude/agents/reviewer.md", ".claude/commands/fix-issue.md")]
    assert len({mem[x] for x in web}) == 1
    csv = [c[artifact_id("beta/data-pipeline", p)] for p in ("CLAUDE.md", ".claude/skills/csv-cleaner/SKILL.md")]
    assert mem[csv[0]] == mem[csv[1]] != mem[web[0]]
    ucs = fctx.tables.read("use_cases")
    assert all(u["level"] == 1 and u["parent_id"] is None and u["label"].startswith("Fixture") for u in ucs)
    singles = {c[artifact_id(r, p)] for r, p in [("epsilon/infra", "CLAUDE.md"),
                                                  ("zeta/release-plugin", ".claude/commands/release.md"),
                                                  ("eta/broken", ".claude/skills/notes/SKILL.md")]}
    unch = fctx.tables.read("uncharted")
    assert singles <= {u["cluster_id"] for u in unch}
    assert [u["rank"] for u in unch] == list(range(1, len(unch) + 1))
    assert (fctx.root / "llm_cache.jsonl").exists()
    assert s7.run(fctx, Opts()).units_skipped == 1


def test_empty_semantics_yields_empty_taxonomy(fctx):
    assert s7.taxonomy(fctx, []) == {"use_cases": [], "uc_membership": [], "technique_candidates": [], "uncharted": []}


def test_export_drafts_keeps_approvals(fctx, monkeypatch, tmp_path):
    monkeypatch.setattr(editorial, "editorial_dir", lambda edition: tmp_path / "editorial" / edition)
    run_until(fctx, "s7")
    path = editorial.export_drafts(fctx)
    labels = json.loads(path.read_text())
    uc = sorted(labels)[0]
    labels[uc].update(status="approved", label="Web apps")
    path.write_text(json.dumps(labels))
    again = json.loads(editorial.export_drafts(fctx).read_text())
    assert again[uc]["status"] == "approved" and again[uc]["label"] == "Web apps" and again[uc]["draft_label"]
    assert editorial.resolve(again, uc) == {"status": "approved", "label": "Web apps"}
    assert editorial.resolve({"*": {"status": "approved"}}, "uc-x") == {"status": "approved", "label": None}
    assert editorial.resolve({}, "uc-x") == {"status": "draft", "label": None}
