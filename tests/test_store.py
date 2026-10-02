import json
import re
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import zstandard

from pipeline.redact import redact as real_redact
from pipeline.store import BlobStore, Tables, encode_blob, legacy_files, pack_legacy

HIT = {"repo": "o/r", "path": "CLAUDE.md", "component": "claude_md", "query_id": "q", "blob_sha": "s",
       "is_fork": False}


def test_blob_store_redacts_before_writing(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    secret = "ghp_" + "A1b2C3d4E5" * 4
    oid = "ab" * 20
    assert store.put(oid, f"token {secret}\n") == {"github_token": 1}
    assert store.get(oid) == "token [REDACTED:github_token]\n"
    assert store.has(oid) and not store.has("cd" * 20)


SECRET_LINE = "token ghp_" + "A1b2C3d4E5" * 4 + "\n"


def test_blob_store_keeps_version_and_counts_with_the_blob(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    oid = "ab" * 20
    assert store.redaction_counts(oid) == {}
    store.put(oid, SECRET_LINE)
    assert store.redaction_counts(oid) == {"github_token": 1} and store.version(oid) == 1
    assert {p.name for p in store.root.iterdir()} <= {"blobs.sqlite", "blobs.sqlite-wal", "blobs.sqlite-shm"}
    store.put("cd" * 20, "plain\n")
    assert store.redaction_counts("cd" * 20) == {} and store.get("cd" * 20) == "plain\n"


def legacy_blob(root, oid, data: bytes, counts=None):
    """The M1/M2 layout: one zstd file per blob, and for M1 a counts sidecar and no header."""
    (root / oid[:2]).mkdir(parents=True, exist_ok=True)
    (root / oid[:2] / f"{oid}.zst").write_bytes(data)
    if counts is not None:
        (root / oid[:2] / f"{oid}.json").write_text(json.dumps(counts))


def test_a_store_with_unpacked_files_refuses_to_open(tmp_path):
    root = tmp_path / "blobs"
    legacy_blob(root, "ab" * 20, encode_blob(1, {}, "x\n"))
    with pytest.raises(SystemExit, match="pack-blobs"):
        BlobStore(root).has("ab" * 20)


def test_pack_keeps_each_blob_version_and_counts_and_removes_the_files(tmp_path):
    root = tmp_path / "blobs"
    m1, m2 = "ab" * 20, "cd" * 20
    legacy_blob(root, m1, zstandard.ZstdCompressor().compress(b"token [REDACTED:github_token]\n"),
                {"github_token": 1})
    legacy_blob(root, m2, encode_blob(1, {"email": 2}, "two\n"))
    assert pack_legacy(root) == (2, 0)
    assert legacy_files(root) == [] and [p.name for p in root.iterdir() if p.is_dir()] == []
    store = BlobStore(root)
    assert store.version(m1) == 1 and store.redaction_counts(m1) == {"github_token": 1}
    assert store.get(m1) == "token [REDACTED:github_token]\n"
    assert store.redaction_counts(m2) == {"email": 2} and store.get(m2) == "two\n"
    assert pack_legacy(root) == (0, 0)


def test_a_pack_killed_before_its_deletes_finishes_on_rerun(tmp_path, monkeypatch):
    root = tmp_path / "blobs"
    oids = [f"{i:02x}" * 20 for i in range(5)]
    for i, oid in enumerate(oids):
        legacy_blob(root, oid, encode_blob(1, {}, f"blob {i}\n"))
    real_unlink = Path.unlink
    calls = []

    def dying_unlink(self, missing_ok=False):
        if self.suffix == ".zst":
            calls.append(self)
            if len(calls) == 3:
                raise KeyboardInterrupt  # killed after the batch committed, mid-delete
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", dying_unlink)
    with pytest.raises(KeyboardInterrupt):
        pack_legacy(root, batch=5)
    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert pack_legacy(root) == (0, 3)  # the rows were committed; only the files were left
    store = BlobStore(root)
    assert [store.get(o) for o in oids] == [f"blob {i}\n" for i in range(5)]


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


def test_blob_from_a_newer_version_is_refused(tmp_path, monkeypatch):
    store = BlobStore(tmp_path / "blobs")
    oid = "ab" * 20
    monkeypatch.setattr("pipeline.store.REDACT_VERSION", 3)
    store.put(oid, "plain\n")
    monkeypatch.setattr("pipeline.store.REDACT_VERSION", 2)
    with pytest.raises(RuntimeError, match="newer rules"):
        store.get(oid)


def test_census_pack_blobs_packs_the_blob_root(tmp_path, monkeypatch, capsys):
    from pipeline.cli import main

    monkeypatch.setenv("CENSUS_BLOBS", str(tmp_path / "blobs"))
    legacy_blob(tmp_path / "blobs", "ab" * 20, encode_blob(1, {}, "x\n"))
    assert main(["pack-blobs"]) == 0
    assert "packed 1 blobs" in capsys.readouterr().out
    assert BlobStore(tmp_path / "blobs").get("ab" * 20) == "x\n"


def test_re_redacting_a_packed_m1_blob_keeps_its_counts(tmp_path, monkeypatch):
    root = tmp_path / "blobs"
    oid = "ab" * 20
    legacy_blob(root, oid, zstandard.ZstdCompressor().compress(
        b"mail bob@example.com token [REDACTED:github_token]\n"), {"github_token": 1})
    pack_legacy(root)
    store = BlobStore(root)
    monkeypatch.setattr("pipeline.store.REDACT_VERSION", 2)
    monkeypatch.setattr("pipeline.store.redact", stricter)
    assert store.ensure_current(oid) is True
    assert store.redaction_counts(oid) == {"github_token": 1, "email": 1}


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


def test_parts_are_zstd_compressed(tmp_path):
    t = Tables(tmp_path)
    t.write_part("repo_hits", "p-1", [HIT])
    meta = pq.ParquetFile(t.dir("repo_hits") / "p-1.parquet").metadata
    assert meta.row_group(0).column(0).compression == "ZSTD"


def test_connect_exposes_every_table(tmp_path):
    con = Tables(tmp_path).connect()
    assert con.execute("SELECT count(*) FROM artifacts").fetchone() == (0,)


def test_a_lone_surrogate_from_a_json_escape_does_not_crash_a_put(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    oid = "ab" * 20
    store.put(oid, "a\ud800b")
    assert store.get(oid) == "a?b"


def test_atomic_write_syncs_the_file_before_the_rename(tmp_path, monkeypatch):
    import os

    from pipeline.store import atomic_write

    events = []
    monkeypatch.setattr(os, "fsync", lambda fd: events.append("fsync"))
    real_replace = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: events.append("replace") or real_replace(a, b))
    atomic_write(tmp_path / "x.bin", b"data")
    assert events == ["fsync", "replace"] and (tmp_path / "x.bin").read_bytes() == b"data"
