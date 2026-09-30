from pipeline.audit import audit
from pipeline.runner import Unit, run_batched


def hits(batch):
    return {"repo_hits": [{"repo": u.payload, "path": "CLAUDE.md", "component": "claude_md", "query_id": "q",
                           "blob_sha": u.key, "is_fork": False} for u in batch]}


def run(ctx, n=6):
    run_batched(ctx, "s1", [Unit(f"k{i}", f"o/r{i}") for i in range(n)], hits, batch_size=2, log=lambda m: None)


def test_clean_stage_has_no_problems(ctx):
    run(ctx)
    assert audit(ctx, "s1") == []


def test_a_journaled_part_that_is_gone_is_lost_rows(ctx):
    run(ctx)
    part = sorted(ctx.tables.parts("repo_hits"))[0]
    ctx.tables.delete_part("repo_hits", part)
    assert any("journaled but missing" in p for p in audit(ctx, "s1"))


def test_a_part_with_the_wrong_row_count_is_reported(ctx):
    run(ctx)
    part = sorted(ctx.tables.parts("repo_hits"))[0]
    ctx.tables.write_part("repo_hits", part, [])
    assert any("rows on disk differ from the journal" in p for p in audit(ctx, "s1"))


def test_duplicated_rows_and_unjournaled_parts_are_reported(ctx):
    run(ctx)
    ctx.tables.write_part("repo_hits", "p-extra", hits([Unit("k0", "o/r0")])["repo_hits"])
    problems = audit(ctx, "s1")
    assert any("no journal line" in p for p in problems)
    assert any("duplicated rows" in p for p in problems)


def test_a_unit_journaled_twice_is_reported(ctx):
    run(ctx)
    ctx.journal("s1").record("p-again", ["k0"], {"repo_hits": 0, "s1_overflows": 0})
    assert any("journaled more than once" in p for p in audit(ctx, "s1"))
