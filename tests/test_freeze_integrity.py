import json
import shutil

import pytest
from helpers import FIXTURES, run_until

from pipeline.cli import run_stage
from pipeline.context import Opts, make_ctx
from pipeline.freeze import edition_hash, freeze, reredact


def copied_ctx(tmp_path):
    fx = tmp_path / "fx"
    shutil.copytree(FIXTURES, fx)
    return fx, make_ctx("test", fixtures=fx, llm="fake", embedder="hash", site_data=tmp_path / "site-data")


def test_clean_fixture_run_freezes(tmp_path):
    _, ctx = copied_ctx(tmp_path)
    run_until(ctx, "s7")
    assert freeze(ctx)["edition"] == "test"


def test_stale_rows_after_upstream_change_without_reset_block_freeze(tmp_path):
    fx, ctx = copied_ctx(tmp_path)
    run_until(ctx, "s5")
    md = fx / "acme__webapp" / "CLAUDE.md.fixture"
    md.write_text(md.read_text() + "\nAn added line.\n")
    for stage in ("s1", "s2", "s3", "s4", "s5"):
        run_stage(stage, ctx, Opts())
    with pytest.raises(SystemExit) as e:
        freeze(ctx)
    msg = str(e.value)
    assert "artifacts.artifact_id" in msg and "s3 --reset" in msg


def test_dangling_reference_blocks_freeze(tmp_path):
    _, ctx = copied_ctx(tmp_path)
    run_until(ctx, "s7")
    ctx.tables.write_part("uc_membership", "p-x", [{"cluster_id": "nope", "use_case_id": "uc-x"}])
    with pytest.raises(SystemExit, match=r"uc_membership.cluster_id.*s7 --reset"):
        freeze(ctx)


def test_manifest_records_the_redaction_version(tmp_path):
    _, ctx = copied_ctx(tmp_path)
    run_until(ctx, "s7")
    assert freeze(ctx)["redact_version"] == 1
    assert edition_hash(ctx)[0] != edition_hash(ctx, with_redact_version=False)[0]


def test_freeze_refuses_blobs_redacted_under_older_rules_until_reredact(tmp_path, monkeypatch):
    _, ctx = copied_ctx(tmp_path)
    run_until(ctx, "s7")
    for mod in ("pipeline.store", "pipeline.freeze"):
        monkeypatch.setattr(f"{mod}.REDACT_VERSION", 2)
    with pytest.raises(SystemExit, match=r"census reredact.*s3 --reset"):
        freeze(ctx)
    changed, total = reredact(ctx)
    assert changed == total > 0 and reredact(ctx) == (0, total)
    assert freeze(ctx)["redact_version"] == 2


def test_facts_refuse_an_edition_frozen_under_another_redaction_version(tmp_path, monkeypatch):
    from pipeline import s8_facts

    _, ctx = copied_ctx(tmp_path)
    run_until(ctx, "s7")
    freeze(ctx)
    monkeypatch.setattr(s8_facts, "REDACT_VERSION", 2)
    with pytest.raises(SystemExit, match="frozen under redaction v1"):
        run_stage("s8", ctx, Opts())


def test_m1_manifest_without_the_field_still_verifies(tmp_path):
    from pipeline.paths import manifest_path

    _, ctx = copied_ctx(tmp_path)
    run_until(ctx, "s7")
    freeze(ctx)
    path = manifest_path(ctx.edition)
    manifest = json.loads(path.read_text())
    del manifest["redact_version"]
    manifest["edition_hash"] = edition_hash(ctx, with_redact_version=False)[0]  # how M1 computed it
    path.write_text(json.dumps(manifest))
    assert not run_stage("s8", ctx, Opts()).stopped
