import json

from m0.paths import append_jsonl, blob_path, data_path, read_jsonl


def test_data_path_creates_parent_under_m0_data(isolated_data):
    p = data_path("metrics", "x.json")
    assert p == isolated_data / "metrics" / "x.json"
    assert p.parent.is_dir()


def test_blob_path_shards_by_oid_prefix(isolated_data):
    assert blob_path("abcdef") == isolated_data / "blobs" / "ab" / "abcdef.txt"


def test_read_jsonl_skips_partial_line_and_append_starts_fresh_line(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text(json.dumps({"a": 1}) + "\n" + '{"a": 2, "tr')  # kill -9 mid-write
    assert read_jsonl(p) == [{"a": 1}]
    append_jsonl(p, {"a": 3})
    assert read_jsonl(p) == [{"a": 1}, {"a": 3}]


def test_read_jsonl_missing_file_is_empty(tmp_path):
    assert read_jsonl(tmp_path / "nope.jsonl") == []
