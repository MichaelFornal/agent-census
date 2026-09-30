import pytest

from pipeline.jsonl import append_jsonl, read_jsonl


def test_read_jsonl_skips_partial_line_and_append_starts_fresh_line(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"a": 1}\n{"a": 2')
    assert read_jsonl(p) == [{"a": 1}]
    append_jsonl(p, {"a": 3})
    assert read_jsonl(p) == [{"a": 1}, {"a": 3}]


def test_read_missing_file_is_empty(tmp_path):
    assert read_jsonl(tmp_path / "none.jsonl") == []


def test_corrupt_line_before_the_end_raises(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"a": 1}\n{"a": 2\n{"a": 3}\n')
    with pytest.raises(ValueError, match=":2: corrupt"):
        read_jsonl(p)


def test_non_object_line_raises_unless_last(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('1\n{"a": 2}\n')
    with pytest.raises(ValueError):
        read_jsonl(p)

    p.write_text('{"a": 1}\n12')
    assert read_jsonl(p) == [{"a": 1}]


def test_append_after_partial_line_discards_it(tmp_path):
    p = tmp_path / "x.jsonl"
    # Case 1: partial line after a newline
    p.write_text('{"a": 1}\n{"a": 2')
    append_jsonl(p, {"a": 3})
    assert p.read_text() == '{"a": 1}\n{"a": 3}\n'

    # Case 2: no newline at all (entire file is partial)
    p.write_text('{"a"')
    append_jsonl(p, {"a": 3})
    assert p.read_text() == '{"a": 3}\n'
