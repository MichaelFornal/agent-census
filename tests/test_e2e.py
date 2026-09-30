import json

import zstandard
from helpers import FIXTURES

from pipeline.cli import main
from pipeline.context import make_ctx
from pipeline.schemas import SCHEMAS

SECRET = "Zq8Xv2Lm9Pw4Rt7Ky3Nb"


def journals(data):
    return {p.name: p.read_text() for p in (data / "work" / "fixture" / "journal").glob("*.jsonl")}


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
    assert main(["run", "all", *args]) == 0
    assert journals(isolated_data) == before  # a second full run executes no units
    again = json.loads(facts_file.read_text())["facts"]
    assert {k: v["value"] for k, v in again.items()} == {k: v["value"] for k, v in facts.items()}

    # Every stored file: raw, decompressed .zst, blob sidecars (.json) and site data.
    for p in [*isolated_data.rglob("*"), *site.rglob("*")]:
        if p.is_file():
            data = p.read_bytes()
            if p.suffix == ".zst":
                data = zstandard.ZstdDecompressor().decompress(data)
            assert SECRET.encode() not in data, p
    ctx = make_ctx("fixture")
    for t in SCHEMAS:
        assert SECRET not in json.dumps(ctx.tables.read(t), default=str), t

    assert (site / "techniques" / "hook_stop_gate.json").exists()
    assert list((site / "use-cases").glob("*.json"))
    assert (site / "findings" / "anatomy.json").exists()
