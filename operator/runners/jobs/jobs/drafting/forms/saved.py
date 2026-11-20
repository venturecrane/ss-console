"""A delivered form set's answer values, saved so a change request revises them.

At delivery every form unit writes ``form-answers.json`` in its data dir: the
checked list, the form and revision, the responding party, whether requests
for admission were served, the definition boxes, the filed draft's name, and
every answer value with its verified cites. The drafting lane copies the
job's records into its run dir store (``drafting_lane.FORM_ANSWERS``, one
file per delivered job, ``<run dir>/drafting-form-answers/<job id>.json``)
when the job is delivered, and into a revise job's data dir as
``prior-form-answers.json`` when the broker queues a revision of that job
(``revise_stage``). The values are text read from the matter file; the store
goes with the run dir.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .answers import Answer, Cite, Item, Value

UNIT_FILE = "form-answers.json"
#: In a revise job's data dir: the prior job's saved units.
PRIOR = "prior-form-answers.json"


def answer_to(a: Answer) -> dict[str, Any]:
    return {
        "number": a.number,
        "kind": a.kind,
        "items": [
            {
                "name": it.name,
                "parts": {
                    k: {
                        "text": v.text,
                        "cites": [{"file": c.file, "page": c.page, "excerpt": c.excerpt} for c in v.cites],
                    }
                    for k, v in it.parts.items()
                },
            }
            for it in a.items
        ],
    }


def answer_from(raw: dict[str, Any]) -> Answer:
    items = tuple(
        Item(
            it.get("name"),
            {
                k: Value(
                    str(v["text"]), tuple(Cite(str(c["file"]), int(c["page"]), str(c["excerpt"])) for c in v["cites"])
                )
                for k, v in it["parts"].items()
            },
        )
        for it in raw.get("items") or []
    )
    return Answer(str(raw["number"]), str(raw["kind"]), items)


def unit_record(
    *,
    label: str,
    draft_name: str,
    form: str,
    revision: str,
    inputs: Any,
    answers: tuple[Answer, ...],
    stats: dict[str, Any],
) -> dict[str, Any]:
    return {
        "label": label,
        "draft_name": draft_name,
        "form": form,
        "form_revision": revision,
        "checked": list(inputs.checked),
        "responding_party": inputs.responding_party,
        "admissions_served": inputs.admissions_served,
        "definition_boxes": list(inputs.definition_boxes),
        "answers": [answer_to(a) for a in answers],
        **stats,
    }


#: The lane's store of delivered form sets' records, under its run dir.
STORE = "drafting-form-answers"


def keep(run_dir: Path, job_id: str, data: Path) -> bool:
    """A delivered job's form records into the lane's store (root, ``drafting_lane._report``)."""
    recs = job_records(data)
    if not recs:
        return False
    d = Path(run_dir) / STORE
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{job_id}.json.tmp"
    tmp.write_text(json.dumps({"job_id": job_id, "units": recs}, indent=1), encoding="utf-8")
    tmp.replace(d / f"{job_id}.json")
    return True


def hand_to(run_dir: Path, prior_job_id: str, data: Path) -> bool:
    """A revise job's copy of the prior job's records (root, ``drafting_lane._write_job``)."""
    src = Path(run_dir) / STORE / f"{prior_job_id}.json"
    if not src.is_file():
        return False
    Path(data).mkdir(parents=True, exist_ok=True)
    (Path(data) / PRIOR).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return True


def job_records(data: Path) -> list[dict[str, Any]]:
    """Every form unit's saved record in a job's data dir (root, then ``set-*``)."""
    paths = [Path(data) / UNIT_FILE, *sorted(Path(data).glob(f"*/{UNIT_FILE}"))]
    out = []
    for p in paths:
        if p.is_file():
            rec = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(rec, dict) and isinstance(rec.get("answers"), list):
                out.append(rec)
    return out
