"""Where the form interrogatory path joins the drafting lane (``finish``, ``run``, ``served_plan``).

A response job's unit whose set type is ``form_interrogatories`` and that
carries ``checked`` (the served set's boxes, read by ``read_stage`` or
confirmed by SMD) takes the answer path: ``draft`` writes the unit's final
paper from ``answer_stage`` and ``answer_render``, and the lane's gate, render,
format check, notes, review and filing run on it as on any unit. Every other
unit is untouched: ``takes`` is False for it.

The switch is per firm (:func:`by_operator`): the Operator drafts a firm's form
sets when the firm authored ``form_responses`` (its own sentences; nothing here
supplies one) AND the global ``served.FORM_SETS_BY_OPERATOR`` is on. The global
is the off switch for every firm at once; the firm's section is the on switch.
A firm with no section: SMD drafts its form sets, exactly as before.

Before anything is paid (``before_spend``, from ``served_plan``): the firm's
policy (``policy_missing``), its shell's body slot (``policy_missing``), a
frozen wording rule that records no document kind and that the policy's
override lists do not name (``rule_unplaced``; every other rule is placed by
its own ``doc_kinds``, ``policy.placed``), and ``checked`` on every form unit
(``form_unconfirmed``). Each is a failed job SMD resumes; the
firm is told nothing new.

The first form set a firm's seat produces is held for SMD's read before it
files (``first_set_gate``, ``review_first.gate`` with ``FORM_REVIEW_FIRST``),
counted from the lane's release record by class.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ... import firm_wording, wording_rules_store
from .. import review_first, served as served_mod
from ..evidence import extract
from ..outcome import DraftingFailed
from . import answer_render, answer_stage, saved
from .catalog import CatalogueError, DISC_001, CURRENT, load
from .policy import BODY_SLOT, PolicyMissing, ResponsePolicy, authored, load_policy, placed

FORM_SET = "form_interrogatories"
#: The stops before spend (``outcome.REASON_CODES``): SMD resolves each and resumes.
STOPS = ("form_incomplete", "form_unconfirmed", "policy_missing", "rule_unplaced")
#: The two SMD resolves in the firm's inputs, not in ``served.json``.
POLICY_STOPS = ("policy_missing", "rule_unplaced")
#: Form sets SMD reads before one files on its own (the lane's per-class counter).
FORM_REVIEW_FIRST = 1
FINAL = "draft-final.md"
CITES = "form-cites.md"


def by_operator(firm_data: Any) -> bool:
    """Whether the Operator drafts this firm's form sets: the global switch on
    and the firm's ``form_responses`` authored. Read at call time. The finder
    (``served_find.find``), the hand-file validator (``served.frozen``) and the
    box read (``read_stage.read_found``) are given this answer by the lane."""
    return bool(served_mod.FORM_SETS_BY_OPERATOR) and authored(firm_data or {})


def enabled(run: Any) -> bool:
    """:func:`by_operator` for the run's firm."""
    return by_operator(getattr(getattr(run, "firm", None), "data", None))


def plan_of(run: Any, u: Any) -> dict[str, Any]:
    """The unit's frozen plan entry (state.json ``units``): a served form set's ``checked`` rides there."""
    return next((p for p in run._state().get("units") or [] if p.get("key") == u.key), {})


def _served(run: Any, u: Any) -> Any:
    i = plan_of(run, u).get("served")
    if not isinstance(i, int):
        return None
    return served_mod.frozen(run.data, run.job.job_dir, by_operator(run.firm.data))[i]


def _field(run: Any, u: Any, s: Any, key: str) -> Any:
    v = plan_of(run, u).get(key)
    if v is None and s is not None:
        v = s.get(key) if isinstance(s, dict) else getattr(s, key, None)
    return v


def is_form(u: Any) -> bool:
    return getattr(u, "variant", None) == FORM_SET


def takes(run: Any, u: Any) -> bool:
    """This unit drafts on the answer path."""
    return enabled(run) and is_form(u) and bool(_field(run, u, _served(run, u), "checked"))


def _catalogue(run: Any, u: Any, s: Any) -> Any:
    return load(str(_field(run, u, s, "form") or DISC_001), str(_field(run, u, s, "form_revision") or CURRENT))


def _policy(run: Any) -> ResponsePolicy:
    try:
        return load_policy(run.firm.data)
    except PolicyMissing as exc:
        raise DraftingFailed(f"policy_missing: {exc}; nothing was spent") from None


def _shell(run: Any, pol: ResponsePolicy) -> str:
    try:
        shell = run.firm.read(pol.skeleton)
    except OSError:
        raise DraftingFailed(f"policy_missing: the form-response shell {pol.skeleton} cannot be read") from None
    if shell.count(BODY_SLOT) != 1:
        raise DraftingFailed(f"policy_missing: the form-response shell must carry {BODY_SLOT} exactly once")
    return shell


def _frozen_rules(data: Path) -> list[dict[str, Any]]:
    p = Path(data) / firm_wording.FILE
    return list(json.loads(p.read_text(encoding="utf-8")).get("rules") or []) if p.is_file() else []


def before_spend(run: Any) -> None:
    """Every form unit's prerequisites, before the transcription is paid for."""
    if not enabled(run):
        return
    forms = [u for u in run.units() if is_form(u)]
    if not forms:
        return
    for u in forms:
        s = _served(run, u)
        if not _field(run, u, s, "checked"):
            raise DraftingFailed(f"form_unconfirmed: {u.label}: the checked boxes are not read or confirmed")
        try:
            _catalogue(run, u, s)
        except CatalogueError as exc:
            raise DraftingFailed(f"form_unconfirmed: {u.label}: {exc}") from None
    pol = _policy(run)
    _shell(run, pol)
    _keep, left = placed(pol, _frozen_rules(run.data))
    if left:
        raise DraftingFailed(
            f"rule_unplaced: the firm's wording rule(s) {left} record no document kind and are listed neither to "
            "apply to form sets nor not to; nothing was spent"
        )


def _inputs(run: Any, u: Any, s: Any, pol: ResponsePolicy) -> answer_stage.Inputs:
    facts = run._json("facts.json")
    rules, _left = placed(pol, _frozen_rules(run.data))
    served = served_mod.frozen(run.data, run.job.job_dir, by_operator(run.firm.data))
    return answer_stage.Inputs(
        checked=tuple(_field(run, u, s, "checked")),
        responding_party=str(_field(run, u, s, "responding_party") or ""),
        admissions_served=any(x.set_type == "requests_for_admission" for x in served),
        matter_record=tuple(run._context()),
        medicals=facts.get("medicals") or [],
        rules_block=wording_rules_store.block(rules) if rules else "",
        definition_boxes=tuple(_field(run, u, s, "definition_boxes") or ()),
    )


def draft(run: Any, u: Any, corpus: Any = None) -> str:
    """The unit's final paper on the answer path (idempotent on resume)."""
    out = u.data / FINAL
    if out.is_file():
        return out.read_text(encoding="utf-8")
    s = _served(run, u)
    pol, cat = _policy(run), _catalogue(run, u, s)
    inp = _inputs(run, u, s, pol)
    corpus = corpus if corpus is not None else extract.load_corpus(run.data)
    res = answer_stage.answer(inp, pol, corpus, run.doorway, run.firm.model("compose"), cat, u, run.log)
    try:
        body = answer_render.body(res.answers, inp.checked, pol, answer_stage.effective_form(pol, inp), cat)
    except answer_render.BodyCheckFailed as exc:
        raise DraftingFailed(f"format_check: the form response body failed its checks: {exc}") from None
    cp = run.data / "caption.json"
    cap = json.loads(cp.read_text(encoding="utf-8")) if cp.is_file() else None
    template = answer_render.paper(_shell(run, pol), cap, pol, BODY_SLOT)
    md = template.replace(BODY_SLOT, body.strip())
    rec = saved.unit_record(
        label=u.label,
        draft_name=str(run._names(u)["draft"]) if hasattr(run, "_names") else u.label,
        form=cat.form,
        revision=cat.revision,
        inputs=inp,
        answers=res.answers,
        stats={"calls": res.calls, "unverified_values": res.failed_values, "paper_template": template},
    )
    _atomic(u.data / saved.UNIT_FILE, json.dumps(rec, indent=1))  # what a change request revises (saved.py)
    _atomic(u.data / CITES, answer_render.cite_table(u.label, res.cites))
    _atomic(u.data / "final-pass.json", json.dumps({"version": 0, "settled": [], "drifts": []}))
    _atomic(out, md)
    run._done(u.stage("form_answers"), calls=res.calls, unverified=res.failed_values)
    return md


def _atomic(p: Path, text: str) -> None:
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)


def with_cite_tables(run: Any, notes_md: str) -> str:
    """The attorney notes with each form set's cite table appended (code, not the notes model)."""
    tables = [(u.data / CITES).read_text(encoding="utf-8") for u in run.units() if (u.data / CITES).is_file()]
    tables = [t for t in tables if t.strip()]
    return notes_md.rstrip("\n") + "\n\n" + "\n\n".join(tables) + "\n" if tables else notes_md


def first_set_gate(run: Any, manifest: list[dict[str, Any]]) -> bool:
    """The first form set a seat files is held for SMD (``review_first``); True on SMD's release."""
    if not enabled(run) or not any(takes(run, u) for u in run.units()):  # off: no plan is read
        return False
    return review_first.gate(
        FORM_REVIEW_FIRST,
        getattr(run.job, "form_releases", None),
        run.job.job_dir,
        run.data,
        manifest,
        what="form interrogatory response set",
    )
