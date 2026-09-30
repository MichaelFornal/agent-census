import json

from helpers import run_until

from pipeline import s3_parse
from pipeline.context import Opts


def test_s3_parses_every_fetched_harness_file(fctx):
    run_until(fctx, "s3")
    arts = fctx.tables.read("artifacts")
    assert len(arts) == 21
    errors = {(a["repo"], a["path"]): a["error_class"] for a in arts if a["error_class"]}
    assert errors == {("eta/broken", ".claude/settings.json"): "json_invalid",
                      ("eta/broken", ".claude/skills/notes/SKILL.md"): "frontmatter_invalid"}
    skill = next(a for a in arts if a["repo"] == "beta/data-pipeline" and a["kind"] == "skill")
    assert json.loads(skill["parsed_json"])["resources"] == ["references", "scripts"]
    mcp = next(a for a in arts if a["repo"] == "epsilon/infra" and a["kind"] == "mcp")
    assert "Zq8Xv2Lm9Pw4Rt7Ky3Nb" not in mcp["parsed_json"]
    assert s3_parse.run(fctx, Opts()).units_run == 0
    assert len(fctx.tables.read("artifacts")) == 21
