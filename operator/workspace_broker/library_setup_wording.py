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
* ``library_*``, ``kind_*``, ``now_*``, ``demand_not_listed``: the reply to "set up the
  document library" (or "refresh" it), one section per kind, rendered by ``render``.
* ``pointed_*``, ``matter_not_found``, ``point``: a list made from what the
  administrator pointed at (a matter's number or words of a document's name).
* ``chosen_library`` and its parts, ``refused_which_kind`` on: the choose tool's
  answer to a reply naming one document per kind.
* ``edit_not_used``: appended to a drafted document's or a demand's reply when
  the firm's own save of its library file was not used (piece 4), and ONLY
  with ``logic_in_code.library_edit_notice: enforce``, off on every seat.
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
    "discovery_set": ("discovery set", "discovery sets"),
    "discovery_response": ("discovery response", "discovery responses"),
    "mediation_brief": ("mediation brief", "mediation briefs"),
    "memo": ("memo", "memos"),
    "depo_outline": ("deposition outline", "deposition outlines"),
}
#: "Set up the document library": every kind above, in this order.
LIBRARY = "library"
#: The kinds a firm's own library-file save can be considered for that "set up
#: our ..." does not take; named for ``edit_not_used`` only.
EDIT_KIND_NAMES = {"demand_letter": ("demand letter", "demand letters")}

SENTENCES: dict[str, str] = {
    # The submit tool's answer on a switched-on seat.
    "ack": "We are looking through the firm's own {many} for the ones the firm's {one} file could be made from.",
    "ack_attorney": "We are looking through {attorney}'s own {many} for the ones {attorney}'s {one} file could be made from.",
    "ack_library": "We are looking through the firm's own documents for each kind of document we draft.",
    "ack_library_attorney": "We are looking through {attorney}'s own documents for each kind of document we draft.",
    "ack_pointed_matter": "We are looking at matter {matter} for the firm's {many}.",
    "ack_pointed_name": "We are looking for the {one} you named.",
    # The submit's refusals.
    "refused_kind": (
        "We can set up the firm's discovery sets, discovery responses, mediation briefs, memos and deposition "
        "outlines from the firm's own documents, one kind at a time or the whole document library at once."
    ),
    "refused_admin": "Setting up the firm's files is for the firm's Named Administrators.",
    "refused_underway": "A setup of these {many} is already underway.",
    "refused_underway_library": "Setting up the document library is already underway.",
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
    "point": ("To use a document that is not listed, reply with its matter number or its name and we will look again."),
    # A list made from what the administrator pointed at.
    "pointed_matter_lead": "These are the documents on matter {matter} that the firm's {one} file could be made from, best first:",
    "pointed_name_lead": "These are the documents named as you wrote that the firm's {one} file could be made from, best first:",
    "pointed_matter_none": "We looked at matter {matter} and found nothing the firm's {one} file could be made from. Nothing was changed.",
    "pointed_name_none": "We found no document named as you wrote that the firm's {one} file could be made from. Nothing was changed.",
    "matter_not_found": "We could not find matter {matter} in the firm's case system. Nothing was changed.",
    # "Set up the document library" (or "refresh" it): one section per kind.
    "library_lead": (
        "We looked through the firm's own documents for each kind of document we draft. Each file we would "
        "make keeps the document's page, headers, footers and styles, with every word of its case taken out."
    ),
    "library_lead_attorney": (
        "We looked through {attorney}'s own documents for each kind of document we draft. Each file we would "
        "make keeps the document's page, headers, footers and styles, with every word of its case taken out."
    ),
    "kind_found": "{Many}: we found {found} of the firm's own. These can be made into the firm's file, the one we recommend first, then newest first:",
    "kind_found_attorney": "{Many}: we found {found} of {attorney}'s own. These can be made into {attorney}'s file, the one we recommend first, then newest first:",
    "kind_none_made": "{Many}: we found {found} of the firm's own and none the firm's file could be made from, so they stay as they are.",
    "kind_none_made_attorney": "{Many}: we found {found} of {attorney}'s own and none {attorney}'s file could be made from, so they stay as they are.",
    "kind_none": "{Many}: we found none of the firm's own, so they stay as they are.",
    "kind_none_attorney": "{Many}: we found none of {attorney}'s own, so they stay as they are.",
    "now_accepted": "In use now: the file the firm chose before.",
    "now_pinned": "In use now: a file made from one of the firm's own documents.",
    "now_standard": "In use now: our standard format.",
    "demand_not_listed": "Demand letters are already written from the firm's own demand, so they are not on this list.",
    "library_question": (
        'Reply with one line for each kind you want changed, for example "discovery sets: use 2" or '
        '"memos: keep the current one". A kind you leave out stays as it is, and nothing changes until you '
        "say which."
    ),
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
    # A reply to the document library's lists: one choice per kind.
    "refused_which_kind": (
        "Your reply does not say which kind of document each number is for, so nothing was made. Please put "
        'each on its own line, for example "memos: use 2".'
    ),
    "refused_kind_not_listed": "There is no list of {many} in this thread to choose from, so nothing was made.",
    "refused_chosen_kind": "A document was already chosen for the firm's {many} from this list, so nothing was made.",
    "chosen_library": "We are on it: {parts}.",
    "chosen_part": "the {one} file from number {n}",
    "kept_part": "the {many} stay as they are",
    "pointed_part_matter": "we are looking at matter {matter} for {many}",
    "pointed_part_name": "we are looking for the {one} you named",
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
    # A drafted document's reply, when the firm's own save of its library file was
    # not used. Said ONLY with logic_in_code.library_edit_notice: enforce (the
    # Captain's call; off on every seat), and appended by ``edit_not_used``.
    "edit_not_used": (
        "The firm's latest save of its {one} file was not used for this {one}, so it was made in that file "
        "as it was before. The saved file itself was not changed."
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


def ack_library(attorney: str | None = None) -> str:
    """The submit tool's answer to "set up the document library" on a switched-on seat."""
    return say("ack_library_attorney", attorney=attorney) if attorney else say("ack_library")


def ack_pointed(kind: str, matter: str | None, named: bool) -> str:
    """The answer when the request points at a matter or at a document's name."""
    if matter:
        return say("ack_pointed_matter", matter=matter, **names(kind))
    return say("ack_pointed_name", **names(kind)) if named else ack(kind)


def chosen_library(parts: list[tuple[str, str, Any]]) -> str:
    """The choose tool's answer to a reply naming a choice per kind: each part is
    (kind, ``pick`` | ``keep`` | ``matter`` | ``name``, the number or matter)."""
    said = []
    for kind, what, value in parts:
        values = names(kind)
        if what == "pick":
            said.append(say("chosen_part", n=value, **values))
        elif what == "keep":
            said.append(say("kept_part", **values))
        elif what == "matter":
            said.append(say("pointed_part_matter", matter=value, **values))
        else:
            said.append(say("pointed_part_name", **values))
    return say("chosen_library", parts="; ".join(said))


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


def _now(current: Any) -> str:
    """Which file the kind uses now, from the job's ``current``."""
    if isinstance(current, dict) and current.get("file_id"):
        return say("now_accepted")
    if isinstance(current, dict) and current.get("pinned"):
        return say("now_pinned")
    return say("now_standard") if current is None or isinstance(current, dict) else ""


def _pointed(row: dict[str, Any]) -> str:
    """``matter:<number>``, ``name`` or "" (``library_setup_ledger.pointed_of``)."""
    return str(row.get("pointed") or "")


def _list_body(report: dict[str, Any], attorney: str, kind_names: dict[str, str], pointed: str = "") -> str:
    items = _items(report.get("candidates") or [])
    if not items:
        return ""
    if pointed.startswith("matter:"):
        lead = say("pointed_matter_lead", matter=pointed.split(":", 1)[1], **kind_names)
    elif pointed == "name":
        lead = say("pointed_name_lead", **kind_names)
    elif attorney:
        lead = say("lead_attorney", attorney=attorney, **kind_names)
    else:
        lead = say("lead_firm", **kind_names)
    now = _now(report["current"]) if "current" in report else ""
    return "\n".join([lead, *items, *([now] if now else []), "", say("question"), say("point")])


def _kind_section(entry: dict[str, Any], attorney: str) -> str:
    kind = str(entry.get("kind") or "")
    if kind not in KIND_NAMES:
        return ""
    one, many = KIND_NAMES[kind]
    values = {"one": one, "many": many, "Many": many[:1].upper() + many[1:], "attorney": attorney}
    items = _items(entry.get("candidates") or [])
    raw = entry.get("found")
    found = raw if isinstance(raw, int) else 0
    if items:
        lead = say("kind_found_attorney" if attorney else "kind_found", found=max(found, len(items)), **values)
        lines = [lead, *items]
    elif found:
        lines = [say("kind_none_made_attorney" if attorney else "kind_none_made", found=found, **values)]
    else:
        lines = [say("kind_none_attorney" if attorney else "kind_none", **values)]
    now = _now(entry.get("current"))
    return "\n".join([*lines, *([now] if now else [])])


def _library_body(report: dict[str, Any], attorney: str) -> str:
    """One section per kind, the demand sentence, and the question."""
    raw = report.get("kinds")
    kinds: list[Any] = raw if isinstance(raw, list) else []
    sections = [_kind_section(e, attorney) for e in kinds if isinstance(e, dict)]
    sections = [x for x in sections if x]
    if not sections:
        return ""
    lead = say("library_lead_attorney", attorney=attorney) if attorney else say("library_lead")
    any_items = any(_items(e.get("candidates") or []) for e in kinds if isinstance(e, dict))
    ask = [say("library_question"), say("point")] if any_items else [say("point")]
    return "\n\n".join([lead, *sections, say("demand_not_listed"), " ".join(ask)])


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
    elif outcome == "matter_not_found" and _pointed(row).startswith("matter:"):
        body = say("matter_not_found", matter=_pointed(row).split(":", 1)[1])
    elif outcome == "none_found" and _pointed(row).startswith("matter:"):
        body = say("pointed_matter_none", matter=_pointed(row).split(":", 1)[1], **kind_names)
    elif outcome == "none_found" and _pointed(row) == "name":
        body = say("pointed_name_none", **kind_names)
    elif outcome == "none_found":
        body = say("none_attorney", attorney=attorney, **kind_names) if attorney else say("none_firm", **kind_names)
    elif outcome == "library":
        body = _library_body(report, attorney)
    elif outcome in ("accepted", "accepted_pending", "changed", "unusable", "not_a_form"):
        body = _accept_body(row, outcome, attorney, kind_names)
    elif outcome == "candidates":
        body = _list_body(report, attorney, kind_names, _pointed(row))
    else:
        body = ""
    return f"{body}\n\n{say('outside')}" if body else ""


def edit_not_used(row: dict[str, Any], *, notice: bool, kind: str = "") -> str:
    """The ``edit_not_used`` sentence for a delivered document whose job did not
    use the firm's own save of its library file (``delivery_json.library_edit``,
    from ``jobs/library_edit``), or "". ``notice``: the
    ``logic_in_code.library_edit_notice`` switch is ``enforce``; off, the firm is
    told nothing (SMD is paged either way)."""
    edit = _json_field(row, "delivery_json").get("library_edit")
    if not notice or not isinstance(edit, dict) or edit.get("used") is not False:
        return ""
    kind = kind or str(row.get("document_class") or "")
    one, many = EDIT_KIND_NAMES.get(kind) or KIND_NAMES.get(kind, ("document", "documents"))
    return say("edit_not_used", one=one, many=many)


def edit_notice_on() -> bool:
    """Whether ``logic_in_code.library_edit_notice`` is ``enforce`` on this seat.
    An unreadable switch is not "on": the notice is words to a firm, so the
    door that fails closed here is the one that says nothing."""
    from .logic_in_code import ENFORCE, LIBRARY_EDIT_NOTICE, load_switch

    sw = load_switch(LIBRARY_EDIT_NOTICE)
    return sw.error is None and sw.mode == ENFORCE


__all__ = [
    "ACTIVITY",
    "EDIT_KIND_NAMES",
    "KIND_NAMES",
    "LIBRARY",
    "SENTENCES",
    "ack",
    "ack_library",
    "ack_pointed",
    "chosen",
    "chosen_library",
    "edit_not_used",
    "edit_notice_on",
    "names",
    "render",
    "say",
]
