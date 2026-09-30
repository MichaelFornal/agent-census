"""What a stage needs to run: the edition's tables, the blob store, and how to reach models."""
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.journal import Attempts, Journal
from pipeline.paths import DEFAULT_EDITION, REPO_ROOT, blob_root, edition_dir
from pipeline.store import BlobStore, Tables


@dataclass
class Opts:
    limit: int | None = None
    pass_id: str = "a"
    check: bool = False


@dataclass
class Ctx:
    edition: str
    root: Path
    tables: Tables
    blobs: BlobStore
    fixtures: Path | None = None
    llm: str = "claude"
    embedder: str = "bge"
    site_data: Path = field(default_factory=lambda: REPO_ROOT / "site" / "src" / "data")

    def journal(self, stage: str) -> Journal:
        return Journal(self.root / "journal" / f"{stage}.jsonl")

    def attempts(self, stage: str) -> Attempts:
        return Attempts(self.root / "journal" / f"{stage}.attempts.jsonl")

    def state_path(self, stage: str) -> Path:
        return self.root / "journal" / f"{stage}.state.json"


def make_ctx(edition: str = DEFAULT_EDITION, **kw) -> Ctx:
    root = edition_dir(edition)
    return Ctx(edition=edition, root=root, tables=Tables(root), blobs=BlobStore(blob_root()), **kw)
