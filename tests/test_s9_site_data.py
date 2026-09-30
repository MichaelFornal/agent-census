import json

import pytest
import zstandard
from helpers import run_until

from pipeline import s8_facts, s9_site_data as s9
from pipeline.context import Opts
from pipeline.detectors.catalog import TECHNIQUES
from pipeline.freeze import freeze
from pipeline.store import atomic_write


def build(fctx, monkeypatch, labels):
    run_until(fctx, "s7")
    freeze(fctx)
    s8_facts.run(fctx, Opts())
    monkeypatch.setattr(s9, "load_labels", lambda edition: labels)
    s9.run(fctx, Opts())
    return fctx.site_data


def read(path):
    return json.loads(path.read_text())


def test_permalink_format():
    assert s9.permalink("o/r", "abc", ".claude/settings.json", 7, 7) == \
        "https://github.com/o/r/blob/abc/.claude/settings.json#L7-L7"
    assert s9.permalink("o/r", None, "CLAUDE.md", 1, 2) is None


def test_card_clips_to_25_lines_and_re_redacts(ctx):
    secret = "ghp_" + "A1b2C3d4E5" * 4
    text = "\n".join([f"line {i}" for i in range(1, 41)] + [f"token {secret}"])
    oid = "ab" * 20
    atomic_write(ctx.blobs.path(oid), zstandard.ZstdCompressor().compress(text.encode()))  # bypass put()
    art = {"repo": "o/r", "path": "CLAUDE.md", "kind": "claude_md", "blob_sha": oid}
    c = s9.card(ctx, {"head_oid": "h"}, art, 30, 100, False)
    assert c["excerpt"].splitlines()[0] == "line 30" and len(c["excerpt"].splitlines()) == 12
    assert secret not in c["excerpt"] and c["permalink"].endswith("#L30-L41")
    assert len(s9.card(ctx, {"head_oid": "h"}, art, 1, 100, False)["excerpt"].splitlines()) == 25


def test_technique_pages_and_anonymized_permissions(fctx, monkeypatch):
    out = build(fctx, monkeypatch, {"*": {"status": "approved"}})
    assert {p.stem for p in (out / "techniques").glob("*.json")} == set(TECHNIQUES)
    gate = read(out / "techniques" / "hook_stop_gate.json")
    assert gate["evidence"][0]["repo"] == "acme/webapp"
    assert gate["evidence"][0]["permalink"].endswith("/.claude/settings.json#L7-L7")
    assert "npm test --silent" in gate["evidence"][0]["excerpt"]
    deny = read(out / "techniques" / "permissions_deny.json")
    assert deny["private"] is True
    assert deny["evidence"] and all(c["repo"] is None and c["permalink"] is None for c in deny["evidence"])


def test_only_approved_use_cases_are_written(fctx, monkeypatch):
    out = build(fctx, monkeypatch, {})
    assert not list((out / "use-cases").glob("*.json"))
    out = build(fctx, monkeypatch, {"*": {"status": "approved"}})
    pages = [read(p) for p in (out / "use-cases").glob("*.json")]
    assert len(pages) == len(fctx.tables.read("use_cases"))
    csv = next(p for p in pages if "csv-cleaner" in p["skills"])
    assert csv["examples"] and all(c["permalink"] for c in csv["examples"])


def test_anatomy_evidence_is_the_richest_harness(fctx, monkeypatch):
    out = build(fctx, monkeypatch, {})
    [card] = read(out / "findings" / "anatomy.json")["evidence"]
    assert (card["repo"], card["path"]) == ("acme/webapp", "CLAUDE.md")
    assert card["excerpt"].startswith("# Webapp")
    assert read(out / "facts.json")["facts"]["harnesses_total"]["value"] == 8


def test_digit_in_approved_label_is_refused(fctx, monkeypatch):
    with pytest.raises(SystemExit, match="digit"):
        build(fctx, monkeypatch, {"*": {"status": "approved", "label": "Top 10 apps"}})
