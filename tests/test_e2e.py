import hashlib
import json

import zstandard
from helpers import FIXTURES

from pipeline.cli import main
from pipeline.context import make_ctx
from pipeline.schemas import SCHEMAS

SECRET = "Zq8Xv2Lm9Pw4Rt7Ky3Nb"


def journals(data):
    return {p.name: p.read_text() for p in (data / "work" / "fixture" / "journal").glob("*.jsonl")}


def snapshot(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_fixture_edition_end_to_end(tmp_path, isolated_data):
    site = tmp_path / "site-data"
    args = ["--edition", "fixture", "--fixtures", str(FIXTURES), "--site-data", str(site)]
    assert main(["run", "all", *args]) == 0
    facts_file = isolated_data / "editions" / "fixture" / "facts.json"
    facts = json.loads(facts_file.read_text())["facts"]
    assert facts["harnesses_total"]["value"] == 8
    assert facts["distinct_artifacts"]["value"] < facts["artifacts_total"]["value"]
    assert main(["facts", "--check", "--edition", "fixture"]) == 0

    before = journals(isolated_data)
    assert {f"s{i}.jsonl" for i in range(1, 8)} <= set(before)
    tables_dir = isolated_data / "work" / "fixture" / "tables"
    tables_before = snapshot(tables_dir)
    assert tables_before
    site_before = snapshot(site)
    assert main(["run", "all", *args]) == 0
    assert journals(isolated_data) == before  # a second full run executes no units
    assert snapshot(tables_dir) == tables_before
    site_after = snapshot(site)
    assert set(site_after) == set(site_before)
    assert {k: v for k, v in site_after.items() if k != "facts.json"} == {
        k: v for k, v in site_before.items() if k != "facts.json"
    }
    again = json.loads(facts_file.read_text())["facts"]
    assert {k: v["value"] for k, v in again.items()} == {k: v["value"] for k, v in facts.items()}

    # Every stored file: raw, decompressed .zst, blob sidecars (.json) and site data.
    needles = (SECRET.encode(), SECRET[:8].encode())
    seen = set()
    for p in [*isolated_data.rglob("*"), *site.rglob("*")]:
        if p.is_file():
            seen.add(p.suffix)
            data = p.read_bytes()
            if p.suffix == ".zst":
                data = zstandard.ZstdDecompressor().decompress(data)
            for n in needles:
                assert n not in data, p
    assert {".zst", ".parquet"} <= seen  # the scan saw stored blobs and tables
    ctx = make_ctx("fixture")
    assert any(r["rule"] == "assigned_secret" for r in ctx.tables.read("redactions"))
    for t in SCHEMAS:
        dumped = json.dumps(ctx.tables.read(t), default=str)
        assert SECRET not in dumped and SECRET[:8] not in dumped, t

    assert (site / "techniques" / "hook_stop_gate.json").exists()
    assert list((site / "use-cases").glob("*.json"))
    assert (site / "findings" / "anatomy.json").exists()
