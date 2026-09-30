import json

import duckdb
import pytest
from helpers import run_until

from pipeline import s8_facts as s8
from pipeline.context import Opts
from pipeline.freeze import edition_hash, freeze, load_manifest
from pipeline.kinds import artifact_id
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


@pytest.mark.parametrize("sql", [
    'SELECT count(*) FROM "repos"',
    "SELECT count(*) FROM v_repos, repos",
    "SELECT count(*) FROM v_repos, 'x.parquet'",
    "SELECT count(*) FROM v_x.repos",
    "SELECT count(*) FROM _base.repos",
    "SELECT count(*) FROM _BASE.repos",
])
def test_isolation_bypasses_are_rejected(fctx, tmp_path, sql):
    frozen(fctx)
    d = tmp_path / "facts"
    d.mkdir()
    (d / "bad.sql").write_text(f"-- kind: scalar\n{sql}\n")
    with pytest.raises((ValueError, duckdb.Error)):
        s8.compute(fctx, d)


def test_base_reference_names_the_schema(fctx, tmp_path):
    frozen(fctx)
    d = tmp_path / "facts"
    d.mkdir()
    (d / "bad.sql").write_text("-- kind: scalar\nSELECT count(*) FROM _base.repos\n")
    with pytest.raises(ValueError, match="_base"):
        s8.compute(fctx, d)


def test_canary_and_missing_repos_change_no_fact(fctx):
    run_until(fctx, "s7")
    freeze(fctx)
    baseline = s8.compute(fctx)
    t = fctx.tables
    src_repo = next(r for r in t.read("repos") if r["repo"] == "acme/webapp")
    src_arts = [a for a in t.read("artifacts") if a["repo"] == "acme/webapp"]
    src_feats = [f for f in t.read("features") if f["repo"] == "acme/webapp"]
    mem = {m["artifact_id"]: m for m in t.read("membership")}
    assert src_arts and src_feats
    for repo, flag in (("canary/x", "canary"), ("gone/y", "missing")):
        new_ids = {a["artifact_id"]: artifact_id(repo, a["path"]) for a in src_arts}
        t.write_part("repos", f"p-{flag}", [{**src_repo, "repo": repo, flag: True}])
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
                                         "canonical_artifact": only, "size": 1}])
    t.write_part("membership", "p-only", [{"artifact_id": only, "cluster_id": "c-only", "tier": "exact",
                                           "family_key": None}])
    freeze(fctx)
    assert s8.compute(fctx) == baseline


def test_blob_set_drift_is_detected(fctx):
    frozen(fctx)
    s8.run(fctx, Opts())
    fctx.tables.write_part("harness_files", "p-extra", [{"repo": "acme/webapp", "path": "X.md", "kind": "claude_md",
                                                        "blob_sha": "f" * 40, "size": 1, "fetched": True,
                                                        "skip_reason": None}])
    assert edition_hash(fctx)[0] != load_manifest("test")["edition_hash"]
    with pytest.raises(SystemExit, match="data changed since freeze"):
        s8.run(fctx, Opts(check=True))
