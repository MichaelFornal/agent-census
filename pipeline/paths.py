"""Where the pipeline keeps its data. Everything under data/ is gitignored (PRD §8)."""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EDITION = "m1-slice"


def data_root() -> Path:
    return Path(os.environ.get("CENSUS_DATA", REPO_ROOT / "data"))


def edition_dir(edition: str) -> Path:
    return data_root() / "work" / edition


def blob_root() -> Path:
    return data_root() / "blobs"


def manifest_path(edition: str) -> Path:
    return data_root() / "editions" / edition / "manifest.json"


def facts_path(edition: str) -> Path:
    return data_root() / "editions" / edition / "facts.json"


def editorial_dir(edition: str) -> Path:
    return REPO_ROOT / "editorial" / edition
