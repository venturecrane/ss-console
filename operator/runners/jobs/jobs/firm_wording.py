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

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smokeball_connector import logic_in_code

from . import seat_layout, wording_rules_store

ENV_DIR = "JOBS_SPEC_DIR"
#: The spec applier's install dir (``spec_applier/__main__.py``).
DEFAULT_DIR = seat_layout.SPECS
#: The job's frozen selection, beside its state.
FILE = "wording-rules.json"
#: The seat switch (``logic_in_code.WORDING_RULES``).
SWITCH = logic_in_code.WORDING_RULES
#: The proof alert that puts an untrustworthy store on SMD's desk: the code the
#: layout rules already page with (``proof/gate.py`` CODES), one vocabulary.
UNUSABLE_CODE = "rule_store_unusable"


#: The document kind a demand job writes.
DEMAND_KIND = "demand_letter"


class StoreUnusable(RuntimeError):
    """The installed wording store is present and cannot be trusted."""


def _spec_dir() -> Path:
    return Path(os.environ.get(ENV_DIR) or DEFAULT_DIR)


def switched_on() -> bool:
    return logic_in_code.load_switch(SWITCH).enforcing


def _said(value: Any) -> str:
    """Words as two rows are compared: spacing, case and curly quotes folded."""
    return " ".join(str(value or "").translate(_QUOTES).split()).casefold()


def _text_key(rule: dict[str, Any]) -> str:
    """A rule as two rows are compared: its text, its wording and its phrases.
    A rule restated with corrected wording or phrases is a different rule, never
    folded into the old one."""
    parts = [_said(rule.get("text"))]
    for field in ("wording", "phrases"):
        got = rule.get(field)
        parts.append("\x1f".join(_said(w) for w in got) if isinstance(got, list) else "")
    return hashlib.sha256("\x1e".join(parts).encode("utf-8")).hexdigest()


_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})
_NEVER = datetime.min.replace(tzinfo=timezone.utc)


def _when(rule: dict[str, Any]) -> datetime:
    """When the rule was stated; a missing or unreadable ``at`` reads as oldest."""
    try:
        got = datetime.fromisoformat(str(rule.get("at") or "").replace("Z", "+00:00"))
    except ValueError:
        return _NEVER
    return got if got.tzinfo else got.replace(tzinfo=timezone.utc)


def fold(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rules whose words are the same, folded to one (2026-11-23: the firm's
    rule store held the same rule twice, and every rule was asked about and
    applied twice). The folded rule keeps the OLDEST row's id, so a job's notes
    name the rule as first stated, and carries the NEWEST row's ``at``, so a
    later rule's precedence over an earlier one is by the last time it was said;
    it stands where its newest row stood. The other ids are kept as ``aliases``.
    A list with no repeated words is returned as it came."""
    groups: dict[str, list[int]] = {}
    for i, rule in enumerate(rules):
        groups.setdefault(_text_key(rule), []).append(i)
    out: list[tuple[int, dict[str, Any]]] = []
    for idx in groups.values():
        if len(idx) == 1:
            out.append((idx[0], rules[idx[0]]))
            continue
        oldest = min(idx, key=lambda i: (_when(rules[i]), i))
        newest = max(idx, key=lambda i: (_when(rules[i]), i))
        aliases = [str(rules[i]["rule_id"]) for i in idx if i != oldest]
        words = {k: rules[newest][k] for k in ("wording", "phrases") if k in rules[newest]}
        out.append((newest, {**rules[oldest], **words, "at": rules[newest].get("at", ""), "aliases": aliases}))
    return [rule for _i, rule in sorted(out, key=lambda pair: pair[0])]


def freeze(data: Path, kind: str, attorney: Any, reader: str | None = None) -> list[dict[str, Any]]:
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
    rules = fold(wording_rules_store.applicable(store.rules, kind, attorney, reader))
    record = {"kind": kind, "attorney": attorney or None, "reader": reader, "rules": rules}
    tmp = path.with_name(f".{FILE}.tmp")
    tmp.write_text(json.dumps(record, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return rules


def demand_kind(job: Any) -> str | None:
    """A demand job's kind for the rules, or None when it drafts no letter."""
    return DEMAND_KIND if job.wants("demand") else None


def freeze_or_hold(
    data: Path, kind: str | None, facts: dict[str, Any], hold: type[Exception], reader: str | None = None
) -> None:
    """:func:`freeze` for the attorney the matter record names and the document's
    ``reader``, raising ``hold`` (the lane's held outcome) when the store cannot
    be trusted. ``kind`` None: the job writes no document the rules cover, and
    nothing is read."""
    if kind is None:
        return
    try:
        freeze(data, kind, facts.get("responsible_attorney"), reader)
    except StoreUnusable as exc:
        raise hold(hold_reason(exc)) from None


def attach(drafter: Any, data: Path) -> Any:
    """``drafter`` with the job's frozen rules set on it: every drafter a lane builds."""
    drafter.wording = block_for(data)
    return drafter


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
    "SWITCH",
    "UNUSABLE_CODE",
    "StoreUnusable",
    "alert_unusable",
    "attach",
    "block_for",
    "fold",
    "freeze",
    "demand_kind",
    "freeze_or_hold",
    "hold_reason",
    "switched_on",
]
