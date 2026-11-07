"""Every sentence a firm reads about setting up a kind of document from its own files.

The Captain reads this file before any seat is switched on (``logic_in_code.library_setup``).

ONE FILE, BY TEST. ``tests/test_library_setup_wording.py`` fails when another
file of the piece carries a sentence a firm could read, so a change of words is
a change to this file and nowhere else. Each sentence has a short key; the PR
lists them by key.

WHERE EACH IS SAID:

* ``ack`` and ``ack_attorney``: the submit tool's answer when the seat is
  switched on, relayed by the turn that took the request.
* ``refused_kind`` to ``refused_attorney``: the submit's refusal, relayed by that turn.
* ``lead_*``, ``item*``, ``question``, ``none_*``, ``not_rostered``,
  ``ambiguous``, ``outside``: the job's reply, rendered from its ledger row by
  ``render`` when the reply is bound and sent in code (``delivered_matter``).
* ``chosen*`` and the choice's refusals (``refused_no_choice`` on): the choose
  tool's answer to the administrator's reply naming a document.
* ``done_*``, ``changed``, ``unusable``, ``not_a_form``: the reply of the job that made the
  file from the chosen document, rendered like the list's.
* ``ACTIVITY``: the firm's portal activity line for each audit row a
  switched-on seat's setup job writes.

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
    # The administrator's choice, answered by the choose tool on a switched-on seat.
    "chosen": "We are making the firm's {one} file from number {n}.",
    "chosen_attorney": "We are making {attorney}'s {one} file from number {n}.",
    # The choice's refusals: nothing is made.
    "refused_no_choice": "Please reply with the number of the document to use.",
    "refused_two": "Your reply names more than one of the documents. Please reply with just one number.",
    "refused_not_listed": "Your reply names a number that is not on the list. Please reply with one of the numbers shown.",
    "refused_not_requester": "Only the person who asked to set up these {many} can choose the document.",
    "refused_no_list": "There is no list of documents in this thread to choose from.",
    "refused_superseded": "A later request to set up these {many} replaced this list. Please choose from the newest one.",
    "refused_chosen": "A document was already chosen from this list.",
    "refused_stale": "This list cannot be chosen from any more. Please ask again to set up these {many}.",
    "refused_unreadable": "We could not read this thread just now, so nothing was made. Please reply again with the number.",
    # The choice's job, told when it ends.
    "done_firm": (
        "The firm's {one} file is now made from the document saved {saved} in matter {matter}, file {file_id}. "
        "Every word of that case was taken out and its look kept. The firm's {many} are made in it from the "
        "next draft on."
    ),
    "done_attorney": (
        "{attorney}'s {one} file is now made from the document saved {saved} in matter {matter}, file {file_id}. "
        "Every word of that case was taken out and its look kept. {attorney}'s {many} are made in it from the "
        "next draft on."
    ),
    "done_pending": "This seat installs it on its own before that next draft.",
    "changed": (
        "That document was saved again after we looked through it, so the file was not made from it. "
        "Nothing was changed. Please ask again to set up these {many}."
    ),
    "unusable": (
        "We could not make the file from that document without keeping words of its case, so nothing was "
        "changed. You can reply with another number from the list."
    ),
    "not_a_form": (
        "We could not make a file from that document because its layout does not give us the places to "
        "write into, so nothing was changed. You can reply with another number from the list."
    ),
}

#: What the firm's portal activity says for each audit row a switched-on seat's
#: setup job writes (``src/lib/portal/operator/activity-language.ts`` carries
#: these exactly, pinned by ``tests/library-setup-activity.test.ts``). A seat
#: with the switch off writes none.
ACTIVITY: dict[str, str] = {
    "LIBRARY_SETUP_SUBMITTED": "Started looking through the firm's own documents for a library file",
    "LIBRARY_SETUP_DELIVERED": "Told the firm which of its own documents a library file could be made from",
    "LIBRARY_SETUP_CHOSEN": "Was told which document to make a library file from",
    "LIBRARY_SETUP_ACCEPTED": "Made and filed a library file from the firm's own document",
    "LIBRARY_SETUP_HELD": "Paused setting up a library file for SMD to look at",
    "LIBRARY_SETUP_FAILED": "Could not finish setting up a library file",
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


def chosen(kind: str, n: int, attorney: str | None = None) -> str:
    """The choose tool's answer on a switched-on seat."""
    if attorney:
        return say("chosen_attorney", attorney=attorney, n=n, **names(kind))
    return say("chosen", n=n, **names(kind))


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


def _json_field(row: dict[str, Any], key: str) -> dict[str, Any]:
    raw = row.get(key)
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw else {}
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


def _accept_body(row: dict[str, Any], outcome: str, attorney: str, kind_names: dict[str, str]) -> str:
    """The made file's read-back, or a choice that made nothing."""
    if outcome in ("changed", "unusable", "not_a_form"):
        return say(outcome, **kind_names)
    choice = _json_field(row, "choice_json")
    saved, matter, file_id = (
        _saved(str(choice.get("saved_on") or "")),
        choice.get("matter_number"),
        choice.get("file_id"),
    )
    if not (saved and matter and file_id):
        return ""
    values = {"saved": saved, "matter": matter, "file_id": file_id, **kind_names}
    done = say("done_attorney", attorney=attorney, **values) if attorney else say("done_firm", **values)
    return f"{done} {say('done_pending')}" if outcome == "accepted_pending" else done


def _list_body(report: dict[str, Any], attorney: str, kind_names: dict[str, str]) -> str:
    items = _items(report.get("candidates") or [])
    if not items:
        return ""
    lead = say("lead_attorney", attorney=attorney, **kind_names) if attorney else say("lead_firm", **kind_names)
    return "\n".join([lead, *items, "", say("question")])


def render(row: dict[str, Any]) -> str:
    """The reply for a delivered setup job, from its row; "" when the row holds
    nothing this file can say (the bind then refuses: no words, no send)."""
    report = _json_field(row, "report_json")
    kind_names = names(str(row.get("document_class") or ""))
    attorney = str(report.get("attorney_name") or row.get("attorney_name") or "").strip()
    outcome = str(report.get("setup_outcome") or "")
    named = str(row.get("attorney_named") or "").strip()
    if outcome == "attorney_not_rostered" and named:
        body = say("not_rostered", named=named)
    elif outcome == "attorney_ambiguous" and named:
        body = say("ambiguous", named=named)
    elif outcome == "none_found":
        body = say("none_attorney", attorney=attorney, **kind_names) if attorney else say("none_firm", **kind_names)
    elif outcome in ("accepted", "accepted_pending", "changed", "unusable", "not_a_form"):
        body = _accept_body(row, outcome, attorney, kind_names)
    elif outcome == "candidates":
        body = _list_body(report, attorney, kind_names)
    else:
        body = ""
    return f"{body}\n\n{say('outside')}" if body else ""


__all__ = ["ACTIVITY", "KIND_NAMES", "SENTENCES", "ack", "chosen", "names", "render", "say"]
