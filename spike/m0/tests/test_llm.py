import json

import pytest

from m0.llm import build_prompt, extract_json_array, parse_envelope, score_batch, summarize, validate

SRC = "# Rules\n\nAlways run `pytest -q` before finishing. Use the planner agent for big changes.\n"


def good(rid: str = "a", quote: str = "run `pytest -q` before finishing") -> dict:
    return {"id": rid, "use_case": "Keeps a Python repo tested.", "domain_guess": "python dev",
            "non_coding": False, "techniques_described": [{"name": "verification gate", "evidence_quote": quote}],
            "notable": None}


def test_build_prompt_wraps_each_artifact_with_its_id():
    p = build_prompt([("a", "one"), ("b", "two")])
    assert '<artifact id="a">\none\n</artifact>' in p
    assert '<artifact id="b">\ntwo\n</artifact>' in p


def test_parse_envelope_and_extract_fenced_array():
    stdout = json.dumps({"type": "result", "is_error": False, "duration_ms": 1200, "total_cost_usd": 0.01,
                         "num_turns": 1, "usage": {"input_tokens": 10},
                         "result": 'Here:\n```json\n[{"id": "a"}]\n```'})
    result, meta = parse_envelope(stdout)
    assert meta["duration_ms"] == 1200
    assert meta["is_error"] is False
    assert extract_json_array(result) == [{"id": "a"}]


def test_extract_json_array_raises_without_array():
    with pytest.raises(ValueError):
        extract_json_array("I could not do that.")


def test_validate_accepts_verbatim_quote():
    assert validate(good(), SRC) is None


def test_validate_rejects_paraphrase_long_quote_and_bad_types():
    assert validate(good(quote="run the tests first"), SRC) == "quote_not_verbatim"
    assert validate(good(quote="x" * 201), SRC) == "bad_quote_length"
    bad = good()
    bad["non_coding"] = "no"
    assert validate(bad, SRC) == "bad_non_coding"
    bad = good()
    bad["use_case"] = ""
    assert validate(bad, SRC) == "bad_use_case"


def test_score_batch_counts_missing_duplicate_and_unknown_ids():
    score, ok = score_batch({"a": SRC, "b": SRC}, [good("a"), good("a"), {"id": "zzz"}, "not a dict"])
    assert score == {"sent": 2, "ok": 1, "rejected": {"unknown_or_duplicate_id": 3}, "missing": 1}
    assert [r["id"] for r in ok] == ["a"]


def test_summarize_aggregates_sweep_calls_and_passes_sessions_through():
    calls = [
        {"mode": "sweep", "model": "haiku", "batch_size": 5, "wall_s": 30.0, "ok": 5},
        {"mode": "sweep", "model": "haiku", "batch_size": 5, "wall_s": 30.0, "ok": 4},
        {"mode": "sweep", "model": "haiku", "batch_size": 5, "wall_s": 10.0, "error": "timeout"},
        {"mode": "sustain", "model": "haiku", "batch_size": 10, "wall_s": 99.0, "ok": 10},
    ]
    sessions = [{"model": "haiku", "batch_size": 10, "workers": 2, "calls": 4, "sent": 40, "ok": 36,
                 "errors": 0, "wall_s": 360.0, "stopped_reason": "time"}]
    groups = summarize(calls, sessions)["groups"]
    sweep = groups[0]
    assert (sweep["calls"], sweep["sent"], sweep["ok"], sweep["errors"]) == (3, 15, 9, 1)
    assert sweep["artifacts_per_hr"] == pytest.approx(9 / 70 * 3600)
    assert sweep["valid_rate"] == pytest.approx(0.6)
    assert groups[1]["mode"] == "sustain"
    assert groups[1]["artifacts_per_hr"] == 360.0
