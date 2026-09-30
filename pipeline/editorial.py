"""Michael's editorial layer (PRD §1 Validation): taxonomy labels stay drafts until approved here.

Edit editorial/<edition>/taxonomy_labels.json: set "status": "approved" (and optionally "label") per
use case. "*" applies to every use case (used only by the fixture edition).
"""
import json
from pathlib import Path

from pipeline.context import Ctx
from pipeline.paths import editorial_dir


def labels_path(edition: str) -> Path:
    return editorial_dir(edition) / "taxonomy_labels.json"


def load_labels(edition: str) -> dict:
    p = labels_path(edition)
    return json.loads(p.read_text()) if p.exists() else {}


def resolve(labels: dict, use_case_id: str) -> dict:
    e = labels.get(use_case_id) or labels.get("*") or {}
    return {"status": e.get("status", "draft"), "label": e.get("label")}


def export_drafts(ctx: Ctx) -> Path:
    labels = load_labels(ctx.edition)
    for uc in ctx.tables.read("use_cases"):
        e = labels.setdefault(uc["use_case_id"], {"status": "draft"})
        e.update(draft_label=uc["label"], level=uc["level"], parent_id=uc["parent_id"], size=uc["size"],
                 non_coding=uc["non_coding"])
    p = labels_path(ctx.edition)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(labels, indent=2, sort_keys=True) + "\n")
    return p
