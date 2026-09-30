import pyarrow as pa
import pytest

from pipeline.store import BlobStore, Tables

HIT = {"repo": "o/r", "path": "CLAUDE.md", "component": "claude_md", "query_id": "q", "blob_sha": "s",
       "is_fork": False}


def test_blob_store_redacts_before_writing(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    secret = "ghp_" + "A1b2C3d4E5" * 4
    oid = "ab" * 20
    assert store.put(oid, f"token {secret}\n") == {"github_token": 1}
    assert store.get(oid) == "token [REDACTED:github_token]\n"
    assert store.has(oid) and not store.has("cd" * 20)
    assert store.path(oid).parent.name == "ab"


def test_tables_write_read_and_empty_views(tmp_path):
    t = Tables(tmp_path)
    assert t.read("repo_hits") == []
    assert t.write_part("repo_hits", "p-1", [HIT]) == 1
    assert t.write_part("repo_hits", "p-2", []) == 0
    assert t.read("repo_hits") == [HIT]
    assert t.parts("repo_hits") == {"p-1", "p-2"}
    t.delete_part("repo_hits", "p-1")
    assert t.read("repo_hits") == []


def test_write_part_rejects_wrong_types(tmp_path):
    with pytest.raises((pa.ArrowInvalid, pa.ArrowTypeError)):
        Tables(tmp_path).write_part("repos", "p", [{"repo": "o/r", "stars": "many"}])


def test_connect_exposes_every_table(tmp_path):
    con = Tables(tmp_path).connect()
    assert con.execute("SELECT count(*) FROM artifacts").fetchone() == (0,)
