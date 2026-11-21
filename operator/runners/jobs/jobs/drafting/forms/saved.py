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

A response job's record also carries its frozen served sets (the requests as
served, verbatim) and what each was filed as (``SERVED_FILES``), and is kept
for those alone when it filed no form set: a revision of every filed response
(``revise/set_run.py``) pins each file's responses to the request numbers
served, long after the job dir is gone. The same purge removes them.
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


def _matter_of(data: Path) -> str:
    """The job's matter id, from its ``job.json`` beside the data dir; "" when unread."""
    p = Path(data).parent / "job.json"
    try:
        doc = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except ValueError:
        doc = {}
    return str(doc.get("matter_id") or "") if isinstance(doc, dict) else ""


#: A response job's frozen served sets and what each was filed as
#: (``served.PLAN``, ``served.OUTCOMES``), kept beside its form records so a
#: revision pins each filed response to its request numbers (``revise/set_run``).
SERVED_FILES = {"served": "served-plan.json", "served_outcomes": "served-outcomes.json"}


def _served_of(data: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, name in SERVED_FILES.items():
        p = Path(data) / name
        try:
            sets = json.loads(p.read_text(encoding="utf-8")).get("sets") if p.is_file() else None
        except (ValueError, AttributeError):
            sets = None
        if isinstance(sets, list):
            out[key] = sets
    return out


def keep(run_dir: Path, job_id: str, data: Path) -> bool:
    """A delivered job's form records and served sets into the lane's store
    (root, ``drafting_lane._report``)."""
    recs, served = job_records(data), _served_of(data)
    if not recs and not served:
        return False
    d = Path(run_dir) / STORE
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{job_id}.json.tmp"
    doc = {"job_id": job_id, "matter_id": _matter_of(data), "units": recs, **served}
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    tmp.replace(d / f"{job_id}.json")
    return True


def purge(run_dir: Path, *, job: str | None = None, matter: str | None = None) -> list[str]:
    """Remove the store's records for one job or one matter; the names removed."""
    d = Path(run_dir) / STORE
    gone: list[str] = []
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            doc = {}
        doc = doc if isinstance(doc, dict) else {}
        if (job and (p.stem == job or doc.get("job_id") == job)) or (matter and doc.get("matter_id") == matter):
            p.unlink()
            gone.append(p.name)
    return gone


def main(argv: list[str] | None = None) -> int:
    """``python -m jobs.drafting.forms.saved purge --job <id> | --matter <id> [--run-dir <dir>]``."""
    import argparse
    import os

    from ...daemon import DEFAULT_RUN_DIR, RUN_DIR_ENV

    ap = argparse.ArgumentParser(prog="python -m jobs.drafting.forms.saved")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("purge", help="remove a job's or a matter's saved form answers from the lane's store")
    who = pp.add_mutually_exclusive_group(required=True)
    who.add_argument("--job")
    who.add_argument("--matter")
    pp.add_argument("--run-dir", default=os.environ.get(RUN_DIR_ENV) or DEFAULT_RUN_DIR)
    a = ap.parse_args(argv)
    gone = purge(Path(a.run_dir), job=a.job, matter=a.matter)
    print(f"removed {len(gone)} record(s) from {Path(a.run_dir) / STORE}" + "".join(f"\n  {n}" for n in gone))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())


def hand_to(run_dir: Path, prior_job_id: str, data: Path) -> bool:
    """A revise job's copy of the prior job's records (root, ``drafting_lane._write_job``)."""
    src = Path(run_dir) / STORE / f"{prior_job_id}.json"
    if not src.is_file():
        return False
    Path(data).mkdir(parents=True, exist_ok=True)
    (Path(data) / PRIOR).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return True


#: In a set revision's data dir: each prior job's stored record, by job id.
PRIOR_RECORDS = "prior-records"


def hand_all(run_dir: Path, prior_job_ids: list[str], data: Path) -> list[str]:
    """A set revision's copies of every prior job's record it names (root,
    ``drafting_lane._write_job``); the ids copied."""
    out = []
    for jid in dict.fromkeys(prior_job_ids):
        src = Path(run_dir) / STORE / f"{jid}.json"
        if src.is_file():
            dest = Path(data) / PRIOR_RECORDS
            dest.mkdir(parents=True, exist_ok=True)
            (dest / f"{jid}.json").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            out.append(jid)
    return out


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
