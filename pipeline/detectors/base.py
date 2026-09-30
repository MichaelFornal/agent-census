"""Detector contract (PRD §5): detect(harness) -> list[Evidence]; pure functions over parsed artifacts."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    kind: str
    path: str
    parsed: dict
    error_class: str | None


@dataclass(frozen=True)
class Harness:
    repo: str
    artifacts: tuple[Artifact, ...]

    def of(self, *kinds: str) -> list[Artifact]:
        return [a for a in self.artifacts if a.kind in kinds]


@dataclass(frozen=True)
class Evidence:
    technique_id: str
    artifact_id: str
    path: str
    start_line: int
    end_line: int


def ev(technique_id: str, a: Artifact, start: int | None, end: int | None = None) -> Evidence:
    s = start or 1
    return Evidence(technique_id, a.artifact_id, a.path, s, max(end or s, s))
