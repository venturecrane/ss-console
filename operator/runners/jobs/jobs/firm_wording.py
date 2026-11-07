"""The firm's confirmed wording rules, put into what a job sends the model.

WHY (2026-11-06). A firm's wording rule ("never cite the medical records in a
brief") was installed on the seat and read only by chat turns, while every
document the firm receives is made by a queued job. So the rule bound to nothing
the firm got, the firm was told "recorded, but not yet in effect", and SMD pasted
rules into prompt files by hand. The seat's spec applier now writes each rule
with the kinds and the attorney it covers to ``classes/work_product/wording.json``
(overlay ``shared/wording_rules.py``); this module reads it through
``wording_rules_store``, the twin the install letter reads through, and hands the
lanes the block of rules in scope.

WHICH RULES, IN CODE. :func:`freeze` runs once per job, after the matter record
is read: it selects by the job's document kind and the attorney the matter
record names (``facts.json`` ``responsible_attorney``, the attorney whose name
the document goes out under) and the document's reader (the kind's reader in the
document-kind registry, ``operator_connector_sdk.library.readers``; for a demand
its variant's, so a demand freezes once its variant is read), and writes the
selection to ``wording-rules.json`` in the job's data. Compose and repair read only that file (:func:`block_for`), so
a resumed job writes by the rules it started with. A rule for another kind or
another attorney is never in it.

DARK UNTIL THE SEAT IS SWITCHED ON. The seat's ``logic_in_code.wording_rules``
(read through the connector's copy of ``logic_in_code.py``, which this venv
installs). In ``observe``, every seat's value until it is switched on,
:func:`freeze` writes nothing and reads nothing, :func:`block_for` returns
``""``, and every lane sends the model the bytes it sent before this existed.

AN UNTRUSTWORTHY STORE HOLDS THE JOB (switched on only). A store that is present
and cannot be trusted (a torn manifest, a digest mismatch, a schema this code
does not read) is not "no rules": a draft written without a rule the firm
confirmed is the defect this exists to end, and ADR 0083's 2026-08-10 amendment
refuses a ``work_product`` spec the seat cannot trust. So :func:`freeze` raises
:class:`StoreUnusable` before anything is paid, the lane holds the job for SMD,
and :func:`alert_unusable` pages from the daemon. This differs on purpose from
the layout rules (``drafting/firm_rules.py``), which draft on the firm's
authored layout and page: a layout the firm already accepted is a safe base to
fall back to, and a document with no word of a confirmed rule is not.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from smokeball_connector import logic_in_code

from . import wording_rules_store

ENV_DIR = "JOBS_SPEC_DIR"
#: The spec applier's install dir (``spec_applier/__main__.py``).
DEFAULT_DIR = "/var/lib/smd-config/specs"
#: The job's frozen selection, beside its state.
FILE = "wording-rules.json"
#: The seat switch (``logic_in_code.WORDING_RULES``).
SWITCH = logic_in_code.WORDING_RULES
#: The proof alert that puts an untrustworthy store on SMD's desk: the code the
#: layout rules already page with (``proof/gate.py`` CODES), one vocabulary.
UNUSABLE_CODE = "rule_store_unusable"


#: The document kind a demand job writes.
DEMAND_KIND = "demand_letter"
#: The frozen record's key for the scoped drafting style file (drafting/style_scope.py).
HOUSE_STYLE = "house_style"


class StoreUnusable(RuntimeError):
    """The installed wording store is present and cannot be trusted."""


def _spec_dir() -> Path:
    return Path(os.environ.get(ENV_DIR) or DEFAULT_DIR)


def switched_on() -> bool:
    return logic_in_code.load_switch(SWITCH).enforcing


def freeze(
    data: Path,
    kind: str,
    attorney: Any,
    reader: str | None = None,
    style_rule: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Select this job's rules once and write them to ``data/FILE``.

    Off: nothing is read or written, ``[]``. Already frozen: the frozen rules.
    """
    path = Path(data) / FILE
    if path.is_file():
        return list(json.loads(path.read_text(encoding="utf-8")).get("rules") or [])
    if not switched_on():
        return []
    store = wording_rules_store.read(_spec_dir())
    if store.status == wording_rules_store.UNUSABLE:
        raise StoreUnusable(store.reason)
    rules = wording_rules_store.applicable(store.rules, kind, attorney, reader)
    record: dict[str, Any] = {"kind": kind, "attorney": attorney or None, "reader": reader, "rules": rules}
    if style_rule is not None and store.status == wording_rules_store.OK:
        # The drafting style file scoped to its attorney (drafting/style_scope.py),
        # selected by the store's own rule: in scope or not, recorded with the rules.
        applies = bool(wording_rules_store.applicable([style_rule], kind, attorney, reader))
        record[HOUSE_STYLE] = {"rule": style_rule, "applies": applies}
    tmp = path.with_name(f".{FILE}.tmp")
    tmp.write_text(json.dumps(record, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return rules


def demand_kind(job: Any) -> str | None:
    """A demand job's kind for the rules, or None when it drafts no letter."""
    return DEMAND_KIND if job.wants("demand") else None


def freeze_or_hold(
    data: Path,
    kind: str | None,
    facts: dict[str, Any],
    hold: type[Exception],
    reader: str | None = None,
    style_rule: dict[str, Any] | None = None,
) -> None:
    """:func:`freeze` for the attorney the matter record names and the document's
    ``reader``, raising ``hold`` (the lane's held outcome) when the store cannot
    be trusted. ``kind`` None: the job writes no document the rules cover, and
    nothing is read."""
    if kind is None:
        return
    try:
        freeze(data, kind, facts.get("responsible_attorney"), reader, style_rule)
    except StoreUnusable as exc:
        raise hold(hold_reason(exc)) from None


def attach(drafter: Any, data: Path) -> Any:
    """``drafter`` with the job's frozen rules set on it: every drafter a lane builds."""
    drafter.wording = block_for(data)
    drafter.house_style = house_style_applies(data)
    return drafter


def house_style_applies(data: Path) -> bool:
    """Whether THE HOUSE STYLE goes to the model: True unless the job froze a
    scoped style file (drafting/style_scope.py) whose attorney is not the
    document's. No file, or no scope recorded: True, as before."""
    path = Path(data) / FILE
    if not path.is_file():
        return True
    scoped = json.loads(path.read_text(encoding="utf-8")).get(HOUSE_STYLE)
    return True if not isinstance(scoped, dict) else bool(scoped.get("applies"))


def block_for(data: Path) -> str:
    """The block compose and repair append, or ``""`` when nothing was frozen."""
    path = Path(data) / FILE
    if not path.is_file():
        return ""
    return wording_rules_store.block(list(json.loads(path.read_text(encoding="utf-8")).get("rules") or []))


def hold_reason(exc: StoreUnusable) -> str:
    """The held verdict's reason: SMD's, never the firm's (ids and causes only)."""
    return f"rule_store_unusable: the firm's installed wording rules cannot be trusted ({exc}); nothing was spent"


def alert_unusable(d: Any, job_id: str) -> None:
    """Before the child runs, switched on: an untrustworthy store goes on SMD's desk."""
    if not switched_on():
        return
    store = wording_rules_store.read(_spec_dir())
    if store.status == wording_rules_store.UNUSABLE:
        from .proof import watch

        watch.alert(d, job_id, UNUSABLE_CODE, store.reason)


__all__ = [
    "DEFAULT_DIR",
    "DEMAND_KIND",
    "ENV_DIR",
    "FILE",
    "HOUSE_STYLE",
    "SWITCH",
    "UNUSABLE_CODE",
    "StoreUnusable",
    "alert_unusable",
    "attach",
    "block_for",
    "freeze",
    "demand_kind",
    "freeze_or_hold",
    "house_style_applies",
    "hold_reason",
    "switched_on",
]
