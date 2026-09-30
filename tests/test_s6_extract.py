import re

from helpers import run_until

from pipeline import s6_extract as s6
from pipeline.context import Opts
from pipeline.llm.client import CallResult, LLMLimitReached

TEXTS = {f"s{i}": f"# Doc {i}\n\nUses Claude to do task {i}.\n" for i in range(4)}


class ScriptLLM:
    def __init__(self, fn):
        self.fn = fn
        self.prompts = []

    def call(self, model, system, prompt, schema):
        self.prompts.append(prompt)
        return self.fn(prompt)


def items(n):
    return [{"cluster_id": f"c{i}", "artifact_id": f"a{i}", "blob_sha": f"s{i}"} for i in range(n)]


def good(prompt):
    recs = []
    for aid, body in re.findall(r'<artifact id="(a\d+)">\n(.*?)\n</artifact>', prompt, re.S):
        quote = next(line for line in body.splitlines() if line.startswith("Uses"))
        recs.append({"id": aid, "use_case": quote, "domain_guess": "x", "non_coding": False,
                     "techniques_described": [{"name": "purpose", "evidence_quote": quote}], "notable": None})
    return CallResult({"records": recs}, None, 1.0, 0.01)


def test_extract_batch_accepts_valid_and_rejects_fabricated():
    def fn(prompt):
        res = good(prompt)
        res.data["records"][1]["techniques_described"][0]["evidence_quote"] = "Never deploy on Fridays."
        return res

    out = s6.extract_batch(ScriptLLM(fn), s6.PASSES["a"], items(2), TEXTS)
    assert [r["cluster_id"] for r in out["semantics"]] == ["c0"]
    assert out["semantics"][0]["use_case"] == "Uses Claude to do task 0."
    assert out["semantics_rejects"] == [{"cluster_id": "c1", "pass_id": "a", "reason": "quote_not_verbatim"}]
    assert [(c["n_sent"], c["n_ok"], c["error"]) for c in out["llm_calls"]] == [(2, 1, None)]


def test_call_error_splits_batch_down_to_single_artifacts():
    def fn(prompt):
        if prompt.count("<artifact ") > 1:
            return CallResult(None, "exit 1: overloaded", 1.0, None)
        return good(prompt)

    out = s6.extract_batch(ScriptLLM(fn), s6.PASSES["a"], items(4), TEXTS)
    assert sorted(r["cluster_id"] for r in out["semantics"]) == ["c0", "c1", "c2", "c3"]
    assert len(out["llm_calls"]) == 7 and sum(1 for c in out["llm_calls"] if c["error"]) == 3


def test_single_artifact_call_error_is_rejected():
    out = s6.extract_batch(ScriptLLM(lambda p: CallResult(None, "timeout", 900.0, None)), s6.PASSES["a"], items(1), TEXTS)
    assert out["semantics_rejects"] == [{"cluster_id": "c0", "pass_id": "a", "reason": "call_error"}]


def test_plan_limit_stops_stage_without_journaling(fctx, monkeypatch):
    run_until(fctx, "s5")
    original = s6.make_llm

    def limited(prompt):
        raise LLMLimitReached("Claude usage limit reached")

    monkeypatch.setattr(s6, "make_llm", lambda name: ScriptLLM(limited))
    stats = s6.run(fctx, Opts())
    assert stats.stopped.startswith("plan limit") and stats.units_run == 0
    assert fctx.tables.read("semantics") == [] and fctx.journal("s6").entries() == []
    monkeypatch.setattr(s6, "make_llm", original)
    stats = s6.run(fctx, Opts())
    assert (stats.units_run, stats.stopped) == (11, None)
    assert len(fctx.tables.read("semantics")) == 11


def test_passes_on_fixtures(fctx):
    run_until(fctx, "s6")
    sem = fctx.tables.read("semantics")
    assert len(sem) == 11 and {s["pass_id"] for s in sem} == {"a"}
    assert fctx.tables.read("semantics_rejects") == []
    assert s6.run(fctx, Opts()).units_run == 0
    s6.run(fctx, Opts(pass_id="b", limit=3))
    assert sum(1 for s in fctx.tables.read("semantics") if s["pass_id"] == "b") == 3


def test_batch_where_every_call_fails_stops_unjournaled(fctx, monkeypatch):
    run_until(fctx, "s5")
    original = s6.make_llm
    monkeypatch.setattr(s6, "make_llm",
                        lambda name: ScriptLLM(lambda p: CallResult(None, "timeout", 1.0, None)))
    stats = s6.run(fctx, Opts())
    assert stats.stopped.startswith("every call in the batch failed") and stats.units_run == 0
    assert fctx.journal("s6").entries() == [] and fctx.tables.read("semantics_rejects") == []
    monkeypatch.setattr(s6, "make_llm", original)
    assert s6.run(fctx, Opts()).units_run == 11


def test_partial_failure_is_journaled(fctx, monkeypatch):
    run_until(fctx, "s5")
    real = s6.make_llm("fake")
    reps = s6.representatives(fctx)
    marker = fctx.blobs.get(reps[0]["blob_sha"])

    def fn(prompt):
        if prompt.count("<artifact ") > 1 or marker in prompt:
            return CallResult(None, "overloaded", 1.0, None)
        return real.call("sonnet", s6.SYSTEM_A, prompt, s6.RECORDS_SCHEMA)

    monkeypatch.setattr(s6, "make_llm", lambda name: ScriptLLM(fn))
    stats = s6.run(fctx, Opts())
    assert stats.stopped is None and stats.units_run == 11
    rejects = fctx.tables.read("semantics_rejects")
    assert [r["reason"] for r in rejects] == ["call_error"]
    assert len(fctx.tables.read("semantics")) == 10


def test_plan_limit_in_a_later_chunk_discards_the_whole_batch(fctx, monkeypatch):
    run_until(fctx, "s5")
    monkeypatch.setattr(s6, "BATCH", 4)
    real = s6.make_llm("fake")
    reps = s6.representatives(fctx)
    import random
    random.Random(0).shuffle(reps)
    trigger = fctx.blobs.get(reps[8]["blob_sha"])  # first artifact of the third chunk

    def fn(prompt):
        if trigger in prompt:
            raise LLMLimitReached("Claude usage limit reached")
        return real.call("sonnet", s6.SYSTEM_A, prompt, s6.RECORDS_SCHEMA)

    original = s6.make_llm
    monkeypatch.setattr(s6, "make_llm", lambda name: ScriptLLM(fn))
    stats = s6.run(fctx, Opts())
    assert stats.stopped.startswith("plan limit") and stats.units_run == 0
    assert fctx.tables.read("semantics") == [] and fctx.journal("s6").entries() == []
    monkeypatch.setattr(s6, "make_llm", original)
    stats = s6.run(fctx, Opts())
    assert stats.units_run == 11
    ids = [r["cluster_id"] for r in fctx.tables.read("semantics")]
    assert len(ids) == 11 == len(set(ids))
