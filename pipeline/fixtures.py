"""Offline source for S1/S2: fixture harness directories instead of GitHub (tests and CI).

Layout: <root>/<owner>__<name>/_repo.json plus harness files. Names end in `.fixture` and dot-dirs
are spelled `dot.` so Claude Code never loads a fixture as live instructions.
"""
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FixtureFile:
    repo: str
    path: str
    data: bytes


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def map_path(rel: str) -> str:
    segs = ["." + s[4:] if s.startswith("dot.") else s for s in rel.split("/")]
    if segs[-1].endswith(".fixture"):
        segs[-1] = segs[-1][: -len(".fixture")]
    return "/".join(segs)


def _repo_name(dirname: str) -> str:
    owner, name = dirname.split("__", 1)
    return f"{owner}/{name}"


def _repo_dirs(root: Path) -> list[Path]:
    return sorted(d for d in root.iterdir() if d.is_dir())


def fixture_repos(root: Path) -> list[dict]:
    rows = []
    for d in _repo_dirs(root):
        meta = json.loads((d / "_repo.json").read_text())
        rows.append({"repo": _repo_name(d.name), "missing": False, "error": None, "tree_truncated": 0,
                     "canary": False, **meta})
    return rows


def fixture_files(root: Path) -> list[FixtureFile]:
    out = []
    for d in _repo_dirs(root):
        for p in sorted(d.rglob("*")):
            if p.is_file() and p.name != "_repo.json":
                out.append(FixtureFile(_repo_name(d.name), map_path(p.relative_to(d).as_posix()), p.read_bytes()))
    return out
