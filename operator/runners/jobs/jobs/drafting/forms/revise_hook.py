"""Where a form set's revision joins the revise job (``revise/run.py`` ``ReviseRun._walk``).

A revise job whose data dir holds the prior job's saved form records
(``saved.PRIOR``, copied by the lane) and one of them is the filed draft the
revision names takes this path: the change applied to the saved values
(``revise_stage``), the body rendered again by the same code, put into the
delivered paper's own template (caption, preamble and closing as delivered),
then the lane's gate, render and format check, new attorney notes, and the
revise job's own filing as new versions of the filed files. Any other revise
job (a brief, a draft SMD filed by hand: no saved values) is untouched:
``record_for`` is None and the revise job runs as it always has.
"""

from __future__ import annotations

import json
import shutil
from typing import Any

from ...demand import deliver
from ..outcome import DraftingFailed, Verdict
from . import answer_render, answer_stage, revise_stage
from .catalog import load
from .lane_hook import FORM_SET, _policy
from .policy import BODY_SLOT
from .saved import PRIOR, unit_record

RECORD = "form-revision.json"


def record_for(run: Any) -> dict[str, Any] | None:
    """The saved record of the filed draft this revision names, or None."""
    p = run.data / PRIOR
    if not p.is_file():
        return None
    name = str((run._file_entry("draft") or {}).get("name") or "")
    units = json.loads(p.read_text(encoding="utf-8")).get("units") or []
    hits = [r for r in units if r.get("draft_name") == name and r.get("paper_template")]
    return hits[0] if len(hits) == 1 else None


def _inputs(rec: dict[str, Any]) -> answer_stage.Inputs:
    return answer_stage.Inputs(
        checked=tuple(rec["checked"]),
        responding_party=str(rec.get("responding_party") or ""),
        admissions_served=bool(rec.get("admissions_served")),
        matter_record=(),
        medicals=[],
        rules_block="",
        definition_boxes=tuple(rec.get("definition_boxes") or ()),
    )


def revised_paper(run: Any, rec: dict[str, Any]) -> tuple[str, revise_stage.Revised]:
    pol, cat = _policy(run), load(rec["form"], rec["form_revision"])
    inp = _inputs(rec)
    got = revise_stage.revise(
        revise_stage.load_saved(rec),
        run.job.request_text,
        inp,
        pol,
        run.doorway,
        run.firm.model("compose"),
        cat,
        run.log,
    )
    try:
        body = answer_render.body(got.answers, inp.checked, pol, answer_stage.effective_form(pol, inp), cat)
    except answer_render.BodyCheckFailed as exc:
        raise DraftingFailed(f"format_check: the revised form response body failed its checks: {exc}") from None
    return str(rec["paper_template"]).replace(BODY_SLOT, body.strip()), got


def _revise(run: Any, rec: dict[str, Any]) -> None:
    md, got = revised_paper(run, rec)
    run._atomic("draft-final.md", md)
    (run.data / "final-pass.json").write_text(json.dumps({"version": 0, "settled": [], "drifts": []}))
    cat = load(rec["form"], rec["form_revision"])
    new = unit_record(
        label=rec["label"],
        draft_name=rec["draft_name"],
        form=cat.form,
        revision=cat.revision,
        inputs=_inputs(rec),
        answers=got.answers,
        stats={"paper_template": rec["paper_template"], "revision_of": run.revise["prior_job_id"]},
    )
    run._atomic("form-answers.json", json.dumps(new, indent=1))  # the next change revises this version
    notes = [
        f"# Attorney notes: {rec['label']} (revised)",
        "",
        f"Sections changed: {', '.join(got.sections) or 'none (only the layout was set again)'}.",
        *(["", "Per attorney (stated in your request, not checked against the file):"] if got.per_attorney else []),
        *[f"- {n}{' (' + k + ')' if k else ''}" for n, k in got.per_attorney],
        "",
        answer_render.cite_table(rec["label"], answer_stage.cites_of(got.answers)),
    ]
    run._atomic("notes-final.md", "\n".join(notes).rstrip() + "\n")
    run._write(RECORD, {"calls": got.calls, "sections": list(got.sections), "per_attorney": list(got.per_attorney)})


def walk(run: Any, rec: dict[str, Any]) -> Verdict:
    """The revise job's walk for a form set (``ReviseRun._walk`` calls it)."""
    from ..finish import Unit

    if run._is_done("manifest"):
        return run._file()
    run._stage("destination", run._destination)
    run._close_request()
    run._stage("form-revise", lambda: _revise(run, rec))
    u = Unit("", rec["label"], FORM_SET, None, run.data)
    md = run._gate(u, (run.data / "draft-final.md").read_text(encoding="utf-8"))
    run._render_unit(u, md, "")
    filed = deliver.out_dir(run.data) / (run._file_entry("draft") or {})["name"]
    built = run._rendered(u)["path"]
    if str(built) != str(filed):
        shutil.copyfile(built, filed)
    run._stage("manifest", run._manifest)
    return run._file()
