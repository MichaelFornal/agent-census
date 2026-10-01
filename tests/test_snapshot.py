from pipeline import cli
from pipeline.context import make_ctx
from pipeline.runner import Unit, run_batched


def _hit(repo: str) -> dict:
    return {"repo": repo, "path": "CLAUDE.md", "component": "claude_md", "query_id": "q", "blob_sha": "s",
            "is_fork": False}


def test_snapshot_copies_journaled_parts_once(capsys):
    src = make_ctx("src")
    run_batched(src, "s1", [Unit("a", "o/a"), Unit("b", "o/b")], lambda b: {"repo_hits": [_hit(u.payload) for u in b]},
                batch_size=1, log=lambda m: None)
    (src.tables.dir("repo_hits") / "p-unjournaled.parquet").write_bytes(b"in flight")

    assert cli.main(["snapshot-s1", "--edition", "dst", "--from", "src"]) == 0
    assert "copied 2 parts, 0 already present" in capsys.readouterr().out
    dst = make_ctx("dst")
    assert sorted(r["repo"] for r in dst.tables.read("repo_hits")) == ["o/a", "o/b"]
    assert dst.journal("s1").parts() == src.journal("s1").parts()
    assert not dst.state_path("s1").exists()

    assert cli.main(["snapshot-s1", "--edition", "dst", "--from", "src"]) == 0
    assert "copied 0 parts, 2 already present" in capsys.readouterr().out
    assert len(dst.journal("s1").entries()) == 2
