import pytest


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("CENSUS_DATA", str(d))
    monkeypatch.setenv("CENSUS_RETRY_GAP_S", "0")  # tests rerun at once; the gap has its own test
    return d


@pytest.fixture
def ctx(tmp_path):
    from pipeline.context import make_ctx
    return make_ctx("test", site_data=tmp_path / "site-data")


@pytest.fixture
def fctx(tmp_path):
    from helpers import FIXTURES
    from pipeline.context import make_ctx
    return make_ctx("test", fixtures=FIXTURES, llm="fake", embedder="hash", site_data=tmp_path / "site-data")
