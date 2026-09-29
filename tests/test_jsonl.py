from pipeline.jsonl import append_jsonl, read_jsonl


def test_read_jsonl_skips_partial_line_and_append_starts_fresh_line(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"a": 1}\n{"a": 2')
    assert read_jsonl(p) == [{"a": 1}]
    append_jsonl(p, {"a": 3})
    assert read_jsonl(p) == [{"a": 1}, {"a": 3}]


def test_read_missing_file_is_empty(tmp_path):
    assert read_jsonl(tmp_path / "none.jsonl") == []
