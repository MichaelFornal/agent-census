from pathlib import Path

from pipeline.context import Opts
from pipeline.fixtures import fixture_files

FIXTURES = Path(__file__).parent / "fixtures" / "harnesses"


def fixture_text(repo: str, path: str) -> str:
    for f in fixture_files(FIXTURES):
        if (f.repo, f.path) == (repo, path):
            return f.data.decode()
    raise KeyError((repo, path))


def run_until(ctx, last: str) -> None:
    from pipeline.cli import PIPELINE, run_stage
    for stage in PIPELINE[: PIPELINE.index(last) + 1]:
        stats = run_stage(stage, ctx, Opts())
        assert not stats.stopped, (stage, stats.stopped)
