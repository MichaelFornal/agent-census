import json

from helpers import run_until

from pipeline import editorial, s7_taxonomy as s7
from pipeline.context import Opts
from pipeline.kinds import artifact_id
from pipeline.llm.client import CallResult, LLMLimitReached
from pipeline.llm.fake import FakeLLM


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


def test_s7_ignores_semantics_for_clusters_that_no_longer_exist(fctx):
    from pipeline.runner import reset
    run_until(fctx, "s7")
    fctx.tables.write_part("semantics", "p-stale", [{
        "cluster_id": "cl-gone", "pass_id": "a", "artifact_id": "x", "use_case": "Uses Claude to haunt.",
        "domain_guess": "x", "non_coding": False, "techniques_json": "[]", "notable": None}])
    reset(fctx, "s7")
    s7.run(fctx, Opts())
    assert "cl-gone" not in {m["cluster_id"] for m in fctx.tables.read("uc_membership")}
    assert "cl-gone" not in {u["cluster_id"] for u in fctx.tables.read("uncharted")}


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
    fresh = {u["use_case_id"]: u for u in fctx.tables.read("use_cases")}[uc]
    assert (again[uc]["draft_label"], again[uc]["level"], again[uc]["size"], again[uc]["non_coding"]) == (
        fresh["label"], fresh["level"], fresh["size"], fresh["non_coding"])
    labels[uc].update(draft_label="stale", level=9, size=999, non_coding=not fresh["non_coding"])
    path.write_text(json.dumps(labels))
    refreshed = json.loads(editorial.export_drafts(fctx).read_text())[uc]
    assert (refreshed["draft_label"], refreshed["level"], refreshed["size"], refreshed["non_coding"]) == (
        fresh["label"], fresh["level"], fresh["size"], fresh["non_coding"])
    assert refreshed["status"] == "approved" and refreshed["label"] == "Web apps"
    assert editorial.resolve(again, uc) == {"status": "approved", "label": "Web apps"}
    assert editorial.resolve({"*": {"status": "approved"}}, "uc-x") == {"status": "approved", "label": None}
    assert editorial.resolve({}, "uc-x") == {"status": "draft", "label": None}


class Wrapper:
    def __init__(self, fail_first=False, raises=False):
        self.inner, self.fail_first, self.raises, self.calls, self.failed = FakeLLM(), fail_first, raises, 0, []

    def call(self, model, system, prompt, schema):
        self.calls += 1
        if self.raises:
            raise LLMLimitReached("quota")
        if self.fail_first and not self.failed:
            self.failed.append(prompt)
            return CallResult(None, "boom", 0.0, None)
        return self.inner.call(model, system, prompt, schema)


def test_failed_label_calls_stop_and_resume_uses_cache(fctx, monkeypatch):
    run_until(fctx, "s6")
    bad = Wrapper(fail_first=True)
    monkeypatch.setattr(s7, "make_llm", lambda name: bad)
    stats = s7.run(fctx, Opts())
    assert stats.stopped and "label calls failed" in stats.stopped
    assert fctx.tables.read("use_cases") == []
    good = Wrapper()
    monkeypatch.setattr(s7, "make_llm", lambda name: good)
    stats = s7.run(fctx, Opts())
    assert stats.stopped is None
    assert good.calls == len(bad.failed) == 1
    assert fctx.tables.read("use_cases")


def test_label_plan_limit_becomes_stop(fctx, monkeypatch):
    run_until(fctx, "s6")
    monkeypatch.setattr(s7, "make_llm", lambda name: Wrapper(raises=True))
    assert s7.run(fctx, Opts()).stopped.startswith("plan limit")


def test_use_case_ids_are_stable(fctx):
    run_until(fctx, "s6")
    sem = sorted((x for x in fctx.tables.read("semantics") if x["pass_id"] == "a"), key=lambda x: x["cluster_id"])
    a, b = s7.taxonomy(fctx, sem), s7.taxonomy(fctx, sem)
    assert a["use_cases"] == b["use_cases"] and a["uc_membership"] == b["uc_membership"]
    assert all(u["use_case_id"].startswith("uc-") and len(u["use_case_id"]) == 13 for u in a["use_cases"])


def test_changing_llm_changes_fingerprint(fctx, monkeypatch):
    run_until(fctx, "s7")
    monkeypatch.setattr(s7, "make_llm", lambda name: FakeLLM())
    fctx.llm = "other"
    assert s7.run(fctx, Opts()).units_run == 1
