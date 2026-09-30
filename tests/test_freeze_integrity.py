import shutil

import pytest
from helpers import FIXTURES, run_until

from pipeline.cli import run_stage
from pipeline.context import Opts, make_ctx
from pipeline.freeze import freeze


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
