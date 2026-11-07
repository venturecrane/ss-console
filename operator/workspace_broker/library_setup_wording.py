"""Every sentence a firm reads about setting up a kind of document from its own files.

The Captain reads this file before any seat is switched on (``logic_in_code.library_setup``).

ONE FILE, BY TEST. ``tests/test_library_setup_wording.py`` fails when another
file of the piece carries a sentence a firm could read, so a change of words is
a change to this file and nowhere else. Each sentence has a short key; the PR
lists them by key.

WHERE EACH IS SAID:

* ``ack`` and ``ack_attorney``: the submit tool's answer when the seat is
  switched on, relayed by the turn that took the request.
* ``refused_*``: the submit's refusal, relayed by that turn.
* ``lead_*``, ``item*``, ``question``, ``none_*``, ``not_rostered``,
  ``ambiguous``, ``outside``: the job's reply, rendered from its ledger row by
  ``render`` when the reply is bound and sent in code (``delivered_matter``).

A value the row does not carry leaves its sentence out (an item with no matter
number uses ``item_no_matter``); nothing is guessed. No timeframes, no
promises the code does not keep, no em dashes.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

#: How each kind is named to the firm: (one, many).
KIND_NAMES = {
    "mediation_brief": ("mediation brief", "mediation briefs"),
    "memo": ("memo", "memos"),
}

SENTENCES: dict[str, str] = {
    # The submit tool's answer on a switched-on seat.
    "ack": "We are looking through the firm's own {many} for the ones the firm's {one} file could be made from.",
    "ack_attorney": "We are looking through {attorney}'s own {many} for the ones {attorney}'s {one} file could be made from.",
    # The submit's refusals.
    "refused_kind": "We can set up a file from the firm's own documents for mediation briefs and memos.",
    "refused_admin": "Setting up the firm's files is for the firm's Named Administrators.",
    "refused_underway": "A setup of these {many} is already underway.",
    "refused_seat": "Setting up a file this way is not available on the firm's account.",
    "refused_attorney": "The attorney's name could not be read from the request. Please write the attorney's full name.",
    # The job's reply.
    "lead_firm": "These are the firm's own {many} that the firm's {one} file could be made from, best first:",
    "lead_attorney": "These are {attorney}'s own {many} that {attorney}'s {one} file could be made from, best first:",
    "item": "{n}. Matter {matter}, saved {saved}, file {file_id}",
    "item_no_matter": "{n}. Saved {saved}, file {file_id}",
    "question": (
        "Which one should we use? Reply with its number. The file is made from that document with every "
        "word of the case taken out and its look kept, and nothing changes until you say which."
    ),
    "none_firm": "We looked through the firm's own {many} and found none the firm's {one} file could be made from. Nothing was changed.",
    "none_attorney": "We looked through {attorney}'s own {many} and found none {attorney}'s {one} file could be made from. Nothing was changed.",
    "not_rostered": "{named} is not one of the attorneys the firm has given us, so we cannot set up a file in that name. Nothing was changed.",
    "ambiguous": "{named} matches more than one of the firm's attorneys. Please reply with the attorney's full name. Nothing was changed.",
    "outside": "Nothing has been sent to anyone outside the firm.",
}


def say(key: str, **values: Any) -> str:
    return SENTENCES[key].format(**values)


def names(kind: str) -> dict[str, str]:
    one, many = KIND_NAMES.get(kind, ("document", "documents"))
    return {"one": one, "many": many}


def ack(kind: str, attorney: str | None = None) -> str:
    """The submit tool's answer on a switched-on seat. ``attorney``: the name as
    the request gave it (the job checks it against the roster)."""
    if attorney:
        return say("ack_attorney", attorney=attorney, **names(kind))
    return say("ack", **names(kind))


def _saved(iso_day: str) -> str:
    try:
        d = date.fromisoformat(iso_day)
    except ValueError:
        return ""
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _items(candidates: list[Any]) -> list[str]:
    out = []
    for n, c in enumerate(candidates, 1):
        if not isinstance(c, dict) or not c.get("file_id") or not _saved(str(c.get("saved_on") or "")):
            continue
        values = {"n": n, "saved": _saved(str(c["saved_on"])), "file_id": str(c["file_id"])}
        matter = str(c.get("matter_number") or "").strip()
        out.append(say("item", matter=matter, **values) if matter else say("item_no_matter", **values))
    return out


def _report(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("report_json")
    try:
        report = json.loads(raw) if isinstance(raw, str) and raw else {}
    except ValueError:
        report = {}
    return report if isinstance(report, dict) else {}


def render(row: dict[str, Any]) -> str:
    """The reply for a delivered setup job, from its row; "" when the row holds
    nothing this file can say (the bind then refuses: no words, no send)."""
    report = _report(row)
    kind_names = names(str(row.get("document_class") or ""))
    attorney = str(report.get("attorney_name") or "").strip()
    outcome = str(report.get("setup_outcome") or "")
    named = str(row.get("attorney_named") or "").strip()
    if outcome == "attorney_not_rostered" and named:
        body = say("not_rostered", named=named)
    elif outcome == "attorney_ambiguous" and named:
        body = say("ambiguous", named=named)
    elif outcome == "none_found":
        body = say("none_attorney", attorney=attorney, **kind_names) if attorney else say("none_firm", **kind_names)
    elif outcome == "candidates":
        items = _items(report.get("candidates") or [])
        if not items:
            return ""
        lead = say("lead_attorney", attorney=attorney, **kind_names) if attorney else say("lead_firm", **kind_names)
        body = "\n".join([lead, *items, "", say("question")])
    else:
        return ""
    return f"{body}\n\n{say('outside')}"


__all__ = ["KIND_NAMES", "SENTENCES", "ack", "names", "render", "say"]
