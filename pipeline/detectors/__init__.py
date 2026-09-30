"""detect(harness): every tier-1 detector, one evidence row per (technique, artifact)."""
from pipeline.detectors.base import Evidence, Harness
from pipeline.detectors.hooks import detect_hooks
from pipeline.detectors.permissions import detect_integrations, detect_permissions
from pipeline.detectors.structure import detect_structure

DETECTORS = [detect_hooks, detect_permissions, detect_integrations, detect_structure]


def detect(h: Harness) -> list[Evidence]:
    out, seen = [], set()
    for d in DETECTORS:
        for e in d(h):
            if (e.technique_id, e.artifact_id) not in seen:
                seen.add((e.technique_id, e.artifact_id))
                out.append(e)
    return out
