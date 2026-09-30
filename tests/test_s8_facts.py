import json

import pytest
from helpers import run_until

from pipeline import s8_facts as s8
from pipeline.context import Opts
from pipeline.freeze import freeze
from pipeline.paths import facts_path


def frozen(fctx):
    run_until(fctx, "s7")
    freeze(fctx)


def test_facts_on_fixture_edition(fctx):
    frozen(fctx)
    s8.run(fctx, Opts())
    doc = json.loads(facts_path("test").read_text())
    f = doc["facts"]
    assert f["harnesses_total"]["value"] == 8
    assert f["harnesses_without_forks"]["value"] == 7
    assert f["artifacts_total"]["value"] == 21
    assert f["distinct_artifacts"]["value"] == 19
    assert f["copy_rate"]["value"] == pytest.approx(2 / 21)
    comp = {r["kind"]: r["harnesses"] for r in f["component_share"]["value"]}
    assert comp["claude_md"] == 5 and comp["skill"] == 4 and comp["plugin"] == 1
    tech = {r["technique_id"]: r["harnesses"] for r in f["technique_prevalence"]["value"]}
    assert tech["command_arguments"] == 2 and tech["claude_md_imports"] == 2
    assert f["use_case_sizes"]["value"]
    assert all(r["query_file"] == f"facts/{k}.sql" and r["edition_hash"] == doc["edition_hash"] for k, r in f.items())
    s8.run(fctx, Opts(check=True))


def test_check_detects_value_drift_and_data_drift(fctx):
    frozen(fctx)
    s8.run(fctx, Opts())
    p = facts_path("test")
    doc = json.loads(p.read_text())
    doc["facts"]["harnesses_total"]["value"] = 9
    p.write_text(json.dumps(doc))
    with pytest.raises(SystemExit, match="harnesses_total"):
        s8.run(fctx, Opts(check=True))
    fctx.tables.write_part("repos", "p-extra", [])
    with pytest.raises(SystemExit, match="data changed since freeze"):
        s8.run(fctx, Opts(check=True))


def test_canary_repos_are_excluded(fctx):
    run_until(fctx, "s7")
    fctx.tables.write_part("repos", "p-canary", [{"repo": "canary/x", "missing": False, "canary": True,
                                                  "is_fork": False, "is_template": False}])
    freeze(fctx)
    assert s8.compute(fctx)["harnesses_total"] == 8


def test_fact_rules(fctx, tmp_path):
    frozen(fctx)
    d = tmp_path / "facts"
    d.mkdir()
    (d / "bad.sql").write_text("-- kind: scalar\nSELECT count(*) FROM repos\n")
    with pytest.raises(ValueError, match=r"v_\* views"):
        s8.compute(fctx, d)
    (d / "bad.sql").write_text("SELECT count(*) FROM v_repos\n")
    with pytest.raises(ValueError, match="kind"):
        s8.compute(fctx, d)
    (d / "bad.sql").write_text("-- kind: scalar\nSELECT repo FROM v_repos\n")
    with pytest.raises(ValueError, match="one row and one column"):
        s8.compute(fctx, d)


def test_unfrozen_edition_is_refused(fctx):
    with pytest.raises(SystemExit, match="not frozen"):
        s8.run(fctx, Opts())
