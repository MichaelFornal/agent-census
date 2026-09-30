import json
import re

import pyarrow as pa
import pytest
import zstandard

from pipeline.redact import redact as real_redact
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


SECRET_LINE = "token ghp_" + "A1b2C3d4E5" * 4 + "\n"


def test_blob_store_keeps_version_and_counts_with_the_blob(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    oid = "ab" * 20
    assert store.redaction_counts(oid) == {}
    store.put(oid, SECRET_LINE)
    assert store.redaction_counts(oid) == {"github_token": 1} and store.version(oid) == 1
    assert [p.name for p in store.path(oid).parent.iterdir()] == [f"{oid}.zst"]  # one file per blob
    store.put("cd" * 20, "plain\n")
    assert store.redaction_counts("cd" * 20) == {} and store.get("cd" * 20) == "plain\n"


def legacy_blob(store, oid, text, counts):
    store.path(oid).parent.mkdir(parents=True, exist_ok=True)
    store.path(oid).write_bytes(zstandard.ZstdCompressor().compress(text.encode()))
    store.legacy_counts_path(oid).write_text(json.dumps(counts))


def test_m1_blob_with_a_sidecar_reads_as_version_one(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    oid = "ab" * 20
    legacy_blob(store, oid, "token [REDACTED:github_token]\n", {"github_token": 1})
    assert store.version(oid) == 1
    assert store.get(oid) == "token [REDACTED:github_token]\n"
    assert store.redaction_counts(oid) == {"github_token": 1}
    assert store.ensure_current(oid) is False
    assert store.legacy_counts_path(oid).exists()  # nothing is rewritten while the version matches


def stricter(text):
    """A later rule set that also removes e-mail addresses."""
    clean, counts = real_redact(text)
    clean, n = re.subn(r"[\w.]+@[\w.]+\.\w+", "[REDACTED:email]", clean)
    if n:
        counts["email"] = n
    return clean, counts


def test_blob_from_an_older_version_is_re_redacted_on_read(tmp_path, monkeypatch):
    store = BlobStore(tmp_path / "blobs")
    oid = "ab" * 20
    store.put(oid, "mail bob@example.com " + SECRET_LINE)
    monkeypatch.setattr("pipeline.store.REDACT_VERSION", 2)
    monkeypatch.setattr("pipeline.store.redact", stricter)
    assert store.version(oid) == 1
    assert store.get(oid) == "mail [REDACTED:email] token [REDACTED:github_token]\n"
    assert store.version(oid) == 2
    assert store.redaction_counts(oid) == {"github_token": 1, "email": 1}
    assert store.ensure_current(oid) is False


def test_re_redacting_an_m1_blob_drops_its_sidecar(tmp_path, monkeypatch):
    store = BlobStore(tmp_path / "blobs")
    oid = "ab" * 20
    legacy_blob(store, oid, "mail bob@example.com token [REDACTED:github_token]\n", {"github_token": 1})
    monkeypatch.setattr("pipeline.store.REDACT_VERSION", 2)
    monkeypatch.setattr("pipeline.store.redact", stricter)
    assert store.ensure_current(oid) is True
    assert store.redaction_counts(oid) == {"github_token": 1, "email": 1}
    assert not store.legacy_counts_path(oid).exists()


def test_blob_from_a_newer_version_is_refused(tmp_path, monkeypatch):
    store = BlobStore(tmp_path / "blobs")
    oid = "ab" * 20
    monkeypatch.setattr("pipeline.store.REDACT_VERSION", 3)
    store.put(oid, "plain\n")
    monkeypatch.setattr("pipeline.store.REDACT_VERSION", 2)
    with pytest.raises(RuntimeError, match="newer rules"):
        store.get(oid)


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
