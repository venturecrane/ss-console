"""The fixed words of a delivered (or, for demand and drafting, held) reply on
a client matter: the medical chronology, the demand and the drafted document.
The same rule as ``delivered_wording``: every sentence is code's, every value
comes from the job's ledger row or the broker's own read at bind time, and a
sentence whose value is not there is omitted.

The chronology's allowance sentence is the one value that is not on the row:
what this delivery used and what remains of the firm's page allowance for the
period are read from the medchron ledger when the reply binds (``reads``),
exactly as ``medchron_allowance`` answers it, so the reply and the tool can
never disagree.
"""

from __future__ import annotations

from typing import Any

from .delivered_wording import OUTSIDE, and_list, compose, files_of, fill, first, lines, name, names, obj, paragraph
from .delivered_wording import positive, whole

SENTENCES: dict[str, dict[str, str]] = {
    "medchron": {
        "lead": "The medical chronology for matter {number} is filed on the matter, in its own folder.",
        "lead_joint": (
            "The medical chronologies for matter {number} are filed on the matter, one per client, each in its "
            "own folder: {n} in all."
        ),
        "lead_update": (
            "The chronology for matter {number} was updated in its own folder, and the earlier version of the "
            "document was replaced by the updated one."
        ),
        "documents": "Documents read: {n}.",
        "pages": "Pages: {n}.",
        "allowance": "This delivery used {consumed} {unit}; {remaining} {unit} remain for {month}.",
        "current_lead": "The chronology for matter {number} already includes these records.",
        "current_named_inside": "Of the {named} files named (and the {inside} found inside them), none adds a visit:",
        "current_named": "Of the {named} files named, none adds a visit:",
        "current_kind": "{phrase}: {n}.",
        "current_review": "The records review filed beside the chronology lists each file, its pages, and what it is.",
    },
    "demand": {
        "lead_client": (
            "The demand for matter {number} is filed in its dated folder in the client matter, in the firm's house "
            'format and ready for attorney review: "{demand}".'
        ),
        "lead_library": (
            "The demand for matter {number} is filed in its dated folder in the firm's library matter, not the "
            'client matter, in the firm\'s house format and ready for attorney review: "{demand}".'
        ),
        "lead_client_bare": (
            "The demand for matter {number} is filed in its dated folder in the client matter, in the firm's house "
            "format and ready for attorney review."
        ),
        "lead_library_bare": (
            "The demand for matter {number} is filed in its dated folder in the firm's library matter, not the "
            "client matter, in the firm's house format and ready for attorney review."
        ),
        "gap": 'The missing-records list is filed beside it as the Gap Audit, "{gap}".',
        "notes": 'The attorney notes are filed beside it as "{notes}".',
        "reserved": (
            "The demand figure, any statement that damages exceed the limits, and what the firm does on expiry are "
            "reserved to the attorney and are marked in the draft."
        ),
        "order": (
            "Want me to order the records this list says are missing? Reply yes and I'll prepare the orders for "
            "your OK."
        ),
        "coverage_client": (
            "No demand was drafted for matter {number}: the file cannot carry a demand yet. The coverage report "
            'filed in the client matter as "{coverage}" names what is missing or unreadable.'
        ),
        "coverage_library": (
            "No demand was drafted for matter {number}: the file cannot carry a demand yet. The coverage report "
            'filed in the firm\'s library matter, not the client matter, as "{coverage}" names what is missing or '
            "unreadable."
        ),
        "coverage_bare": "No demand was drafted for matter {number}: the file cannot carry a demand yet.",
        "held_variant": (
            "The demand for matter {number} is not drafted yet: the file does not say whether this is a pre-suit "
            "or a litigation demand. Nothing was filed."
        ),
        "held": "The demand for matter {number} is not drafted yet. Nothing is needed from you.",
        "outside": OUTSIDE,
    },
    "drafting": {
        "addition_not_covered": (
            "What you wrote later in this thread is not in this document, because the drafting had started when it "
            'reached us: "{words}" Reply here if you want it added, and we will make the change to the filed document.'
        ),
        "addition_not_covered_held": (
            "What you wrote later in this thread is not covered here, because the drafting had started when it "
            'reached us: "{words}"'
        ),
        "lead_client": (
            "The document for matter {number} is filed in the client matter, in the firm's house style and ready "
            "for attorney review: {drafts}."
        ),
        "lead_library": (
            "The document for matter {number} is filed in the firm's library matter, not the client matter, in the "
            "firm's house style and ready for attorney review: {drafts}."
        ),
        "lead_client_bare": (
            "The document for matter {number} is filed in the client matter, in the firm's house style and ready "
            "for attorney review."
        ),
        "lead_library_bare": (
            "The document for matter {number} is filed in the firm's library matter, not the client matter, in the "
            "firm's house style and ready for attorney review."
        ),
        "notes": "The attorney notes are filed beside it as {notes}.",
        "attorney": "For the attorney, marked in the draft: {items}.",
        "not_in_record": "Not in the file, marked in the draft: {items}.",
        "client": "Only the client knows, marked in the draft: {items}.",
        "verification": "The verification is left for the client to sign.",
        "discrepancies": "Where the court paper and the practice-management record differ:",
        "discrepancy": '{field}: the court paper says "{document}"; the practice-management record says "{record}".',
        "corrections": "Record fields corrected from the court's own paper:",
        "correction": '{field}: the record read "{before}" and now reads "{after}", corrected from "{source}".',
        "held_lead": "The document for matter {number} is not drafted yet.",
        "held_needs": "The request is missing what the draft needs: {needs}.",
        "held_no_requests": "The attached documents carry no discovery requests to place in the caption.",
        "held_no_documents": "The matter file has no readable documents to draft from.",
        "held_record": "The matter record could not be read in full.",
        "held_wall": (
            "Some documents in the file are held back from drafting by the privilege wall and reached the draft; "
            "the attorney should check them before this is drafted again."
        ),
        "held_ours": "Nothing is needed from you.",
        "revise_lead": (
            "The document for matter {number} and its attorney notes are filed as new versions of the same files in "
            "the same folder, each named exactly as filed: {files}."
        ),
        "revise_lead_bare": (
            "The document for matter {number} and its attorney notes are filed as new versions of the same files in "
            "the same folder, each named exactly as filed."
        ),
        "revise_history": "The earlier versions stay in each file's version history.",
        "revise_notes": "The attorney notes list each change asked for, whether it was made, and why any was not.",
        "revise_unchanged": (
            "No new version of the document for matter {number} was filed, because nothing in the brief would "
            "change: {why}."
        ),
        "revise_held": "The changes to the document for matter {number} are not all made yet.",
        "outside": OUTSIDE,
    },
}

#: An already-current update's counts by kind, in the chronology skill's words.
CURRENT_KINDS = {
    "billing_only": "billing pages only",
    "read_no_new_visit": "read in full; no visit or finding the chronology does not already list",
    "pages_already_delivered": "every page is already in the chronology's exhibits",
    "repeats_another_new_file": "every page repeats another file in this request",
    "duplicate": "exact copies of files already read",
    "no_record_text": "no record text on any page (photographs or blank pages)",
    "no_page_to_read": "only billing, repeated or blank pages",
    "medlegal_report": "medical-legal reports, reviewed and not itemized",
    "excluded_by_rule": "left out under the firm's own file rules",
    "left_out_by_rule": "name a person other than the client, so they were left out",
    "delivered_package": "files of the delivered chronology itself",
    "deleted": "deleted on the matter",
    "read_for_the_delivered_chronology": (
        "read for the delivered chronology, which recorded why each adds nothing (the review names the reason)"
    ),
    "unreadable": "could not be opened (password-protected or damaged; the review names each)",
    "not_retrieved": "not retrievable from the matter",
    "email": "emails whose attachments were read as their own files",
}
#: The marker kinds a drafted document reports, in the order the reply lists them.
MARKER_KEYS = (("ATTORNEY", "attorney"), ("NOT IN RECORD", "not_in_record"), ("CLIENT", "client"))
_NEEDS = "request_incomplete: the request is missing what the draft needs: "
_NO_REQUESTS = "request_incomplete: the attached documents carry no discovery requests"
_VARIANT = "the file does not say whether this is a pre-suit or a litigation demand"
#: The runner's reason when a revision would change nothing (drafting/revise/run.py
#: ``_hold_if_unchanged``). What follows it is the runner's own item-by-item why.
_UNCHANGED = "revise_unchanged: no new version was filed, because nothing in the brief would change: "


def _line_start(phrase: str | None) -> str | None:
    """A phrase that starts a line, with its first letter capitalised."""
    return phrase[:1].upper() + phrase[1:] if phrase else None


def _s(lane: str, key: str, **values: Any) -> str | None:
    return fill(SENTENCES, lane, key, **values)


def _number(row: dict[str, Any]) -> str | None:
    return str(row.get("matter_number") or "").strip() or None


# -- the medical chronology -------------------------------------------------------


def _allowance(row: dict[str, Any], reads: dict[str, Any]) -> str | None:
    """What this delivery used (its pages less SMD's credits on it) and what
    remains for the period, in the allowance's own unit and period words."""
    state, credited = reads.get("allowance"), whole(reads.get("job_pages_credited"))
    if not isinstance(state, dict) or state.get("authored") is not True or credited is None:
        return None
    pages = whole(row.get("pages"))
    consumed = max(0, pages - credited) if pages is not None else None
    return _s(
        "medchron",
        "allowance",
        consumed=consumed,
        unit=state.get("unit"),
        remaining=whole(state.get("remaining")),
        month=state.get("month"),
    )


def _already_current(row: dict[str, Any], summary: dict[str, Any], allowance: str | None) -> str:
    named, files = whole(summary.get("requested")), whole(summary.get("files"))
    inside = positive(files - named) if named is not None and files is not None else None
    raw = summary.get("by_reason")
    kinds: dict[str, Any] = raw if isinstance(raw, dict) else {}
    rows = [
        _s("medchron", "current_kind", phrase=_line_start(CURRENT_KINDS.get(str(k))), n=positive(v.get("files")))
        for k, v in kinds.items()
        if isinstance(v, dict)
    ]
    return compose(
        paragraph(
            _s("medchron", "current_lead", number=_number(row)),
            first(
                _s("medchron", "current_named_inside", named=named, inside=inside),
                _s("medchron", "current_named", named=named),
            ),
        ),
        lines(*rows),
        paragraph(_s("medchron", "current_review") if summary.get("review_file") else None, allowance),
    )


def medchron(row: dict[str, Any], reads: dict[str, Any]) -> str:
    """A delivered chronology's reply. The folder's name and each client's are
    not on the row (wave 2 adds them), so the reply names neither."""
    delivery = obj(row, "delivery_json")
    allowance = _allowance(row, reads)
    current = delivery.get("already_current")
    if isinstance(current, dict):
        return _already_current(row, current, allowance)
    listed = delivery.get("folders")
    folders: list[Any] = listed if isinstance(listed, list) else []
    number = _number(row)
    lead = first(
        _s("medchron", "lead_update", number=number) if delivery.get("replaced") else None,
        _s("medchron", "lead_joint", number=number, n=len(folders)) if len(folders) > 1 else None,
        _s("medchron", "lead", number=number),
    )
    return compose(
        paragraph(
            lead,
            _s("medchron", "documents", n=positive(row.get("documents"))),
            _s("medchron", "pages", n=positive(row.get("pages"))),
        ),
        allowance or "",
    )


# -- the demand -------------------------------------------------------------------


def _in_client_matter(row: dict[str, Any]) -> bool:
    return str(row.get("file_to_matter_id") or "").strip().lower() == str(row.get("matter_id") or "").strip().lower()


def demand(row: dict[str, Any]) -> str:
    """A demand's reply: delivered, delivered with a coverage report, or held."""
    number = _number(row)
    if row.get("state") == "held":
        variant = _VARIANT in str(row.get("reason") or "")
        return _s("demand", "held_variant" if variant else "held", number=number) or ""
    files = files_of(row)
    where = "client" if _in_client_matter(row) else "library"
    if obj(row, "delivery_json").get("coverage_report") is True:
        return paragraph(
            first(
                _s("demand", f"coverage_{where}", number=number, coverage=name(files, "coverage_report")),
                _s("demand", "coverage_bare", number=number),
            ),
            _s("demand", "outside"),
        )
    gap = name(files, "gap_audit")
    return compose(
        paragraph(
            first(
                _s("demand", f"lead_{where}", number=number, demand=name(files, "demand")),
                _s("demand", f"lead_{where}_bare", number=number),
            ),
            _s("demand", "gap", gap=gap),
            _s("demand", "notes", notes=name(files, "attorney_notes")),
        ),
        library_edit_line(row, "demand_letter"),
        paragraph(_s("demand", "reserved"), _s("demand", "outside")),
        (_s("demand", "order") or "") if gap else "",
    )


# -- the drafted document ---------------------------------------------------------


def _quoted(items: list[str]) -> str | None:
    return "; ".join(f'"{i}"' for i in items) or None


def _markers(delivery: dict[str, Any]) -> str:
    marks = [m for m in delivery.get("markers") or [] if isinstance(m, dict)]
    out = []
    for kind, key in MARKER_KEYS:
        texts = list(dict.fromkeys(" ".join(str(m.get("text") or "").split()) for m in marks if m.get("kind") == kind))
        out.append(_s("drafting", key, items=_quoted([t for t in texts if t])))
    return lines(*out)


def _text(value: Any) -> str | None:
    return " ".join(str(value).split()) if isinstance(value, str) and value.strip() else None


def _field(value: Any) -> str | None:
    got = _text(value)
    return _line_start(got.replace("_", " ")) if got else None


def _caption(delivery: dict[str, Any]) -> str:
    found = [d for d in delivery.get("caption_discrepancies") or [] if isinstance(d, dict)]
    fixed = [c for c in delivery.get("caption_corrections") or [] if isinstance(c, dict)]
    diffs = [
        _s("drafting", "discrepancy", field=_field(d.get("field")), document=_text(d.get("document_value")),
           record=_text(d.get("record_value")) or "(empty)")
        for d in found
    ]  # fmt: skip
    fixes = [
        _s("drafting", "correction", field=_field(c.get("field")), before=_text(c.get("from")) or "(empty)",
           after=_text(c.get("to")), source=_text(c.get("source_document")))
        for c in fixed
    ]  # fmt: skip
    return compose(
        lines(_s("drafting", "discrepancies") if any(diffs) else None, *diffs),
        lines(_s("drafting", "corrections") if any(fixes) else None, *fixes),
    )


def _revise_held(row: dict[str, Any], reason: str) -> str:
    """A held revision: a filed document exists, so "not drafted yet" would be
    false. The changes it holds for are the attorney's own, named by the runner."""
    number = _number(row)
    if reason.startswith(_UNCHANGED):
        why = _text(reason[len(_UNCHANGED) :].rstrip(". "))
        said = _s("drafting", "revise_unchanged", number=number, why=why)
        if said:
            return said
    return paragraph(_s("drafting", "revise_held", number=number), _s("drafting", "held_ours"))


def _revise_delivered(row: dict[str, Any], files: list[Any], delivery: dict[str, Any]) -> str:
    """A delivered revision: new versions of the filed files, in place. What was
    and was not made is in the attorney notes; the row does not carry it (wave 2),
    so the reply points to the notes rather than claim every change was made."""
    named = [f'"{n}"' for role in ("draft", "attorney_notes") for n in names(files, role)]
    number = _number(row)
    return compose(
        paragraph(
            first(
                _s("drafting", "revise_lead", number=number, files=and_list(named) or None),
                _s("drafting", "revise_lead_bare", number=number),
            ),
            _s("drafting", "revise_history"),
            _s("drafting", "revise_notes"),
        ),
        _markers(delivery),
        _not_covered(delivery),
        _s("drafting", "outside") or "",
    )


def _drafting_held(row: dict[str, Any]) -> str:
    reason = str(row.get("reason") or "")
    if row.get("revise_of"):
        return _revise_held(row, reason)
    code = reason.split(":", 1)[0].strip()
    if reason.startswith(_NEEDS):
        why = _s("drafting", "held_needs", needs=_text(reason[len(_NEEDS) :].rstrip(". ")))
    elif reason.startswith(_NO_REQUESTS):
        why = _s("drafting", "held_no_requests")
    else:
        why = {
            "no_readable_documents": _s("drafting", "held_no_documents"),
            "record_unreadable": _s("drafting", "held_record"),
            "gate_refused": _s("drafting", "held_wall"),
        }.get(code)
    return paragraph(_s("drafting", "held_lead", number=_number(row)), why or _s("drafting", "held_ours"))


def _not_covered(delivery: dict[str, Any], key: str = "addition_not_covered") -> str:
    """What the requester added once the drafting had started, said as a fact:
    he was told when it arrived, and the filed document does not hold it."""
    found = delivery.get("additions_not_covered")
    items = found if isinstance(found, list) else []
    return lines(*(_s("drafting", key, words=a.get("words")) for a in items if isinstance(a, dict)))


def drafting(row: dict[str, Any]) -> str:
    """A drafted document's reply: delivered or held. A "set up our ..." job's
    row (``library_setup_ledger``) is worded by ``library_setup_wording``."""
    if row.get("job_kind") == "library_setup":
        from . import library_setup_wording

        return library_setup_wording.render(row)
    delivery = obj(row, "delivery_json")
    if row.get("state") == "held":
        return compose(_drafting_held(row), _not_covered(delivery, "addition_not_covered_held"))
    files = files_of(row)
    if row.get("revise_of"):
        return _revise_delivered(row, files, delivery)
    where = "client" if _in_client_matter(row) else "library"
    drafts = [f'"{n}"' for n in names(files, "draft")]
    notes = [f'"{n}"' for n in names(files, "attorney_notes")]
    return compose(
        paragraph(
            first(
                _s("drafting", f"lead_{where}", number=_number(row), drafts=and_list(drafts) or None),
                _s("drafting", f"lead_{where}_bare", number=_number(row)),
            ),
            _s("drafting", "notes", notes=and_list(notes) or None),
        ),
        lines(
            _markers(delivery),
            _s("drafting", "verification") if row.get("document_class") == "discovery_response" else None,
        ),
        _caption(delivery),
        _not_covered(delivery),
        library_edit_line(row),
        _s("drafting", "outside") or "",
    )


def library_edit_line(row: dict[str, Any], kind: str = "") -> str:
    """The firm's own library-file save was not used: one sentence, only with
    ``logic_in_code.library_edit_notice: enforce`` (``library_setup_wording``)."""
    if not isinstance(obj(row, "delivery_json").get("library_edit"), dict):
        return ""
    from . import library_setup_wording as w

    return w.edit_not_used(row, notice=w.edit_notice_on(), kind=kind)


# -- the stored words: a negotiation notice and an SMD note -------------------------

SIGN_OFF = "Thanks,"


def stored(row: dict[str, Any]) -> str:
    """A negotiation notice's message or an SMD note's text, composed in code
    (or approved by SMD) and recorded; the reply is those words and the sign-off
    the skills always added."""
    text = str(row.get("text") or "").strip()
    return f"{text}\n\n{SIGN_OFF}" if text else ""


__all__ = ["CURRENT_KINDS", "MARKER_KEYS", "SENTENCES", "SIGN_OFF", "demand", "drafting", "medchron", "stored"]
