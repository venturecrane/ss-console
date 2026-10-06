"""Facts a document holds and the matter's fields do not: an insurance card's
member ID, a driver license number, a vehicle's plate. Accepted only when the
cited document itself shows the value beside its label.

WHY. The firm's health-insurer notice prints the member ID from the client's
card, and the SR1 prints the driver's license number from her license. Neither
lives in a Smokeball field: they are on photographs in the matter. The agent
reads the photo (``read_document``) and cites what it read, ``{field: {value,
file_id}}``. A value the agent merely asserts is exactly what the no-fabrication
rule forbids, so this module re-reads the cited file (the extraction cache makes
that free when the agent just read it) and accepts the value only when it
appears ON THE SAME LINE as one of the field's own labels (or on the line right
under it, the way a card prints "Group #" above "W3001999"). The labels are
fixed here, per field; the agent cannot choose them.

WHAT THIS CANNOT CATCH. A vision misread that the agent copies faithfully (an O
read as a 0) passes, because the transcription agrees with itself. That is why
every accepted value is also returned in ``shown`` for the reply to list, so the
person who asked can check it against the card. Short values (a two-letter
state) are refused outright: they match too much to prove anything.

Any value not confirmed prints as ``[Not in the file: <field> (could not be
confirmed on the cited document)]`` and is never filled from the email.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .form_letter_facts import Fact

#: field -> the labels it may sit beside, lowercase, matched as words.
LABELS: dict[str, tuple[str, ...]] = {
    "member_id": (
        "id#",
        "id #",
        "id no",
        "id number",
        "member id",
        "member #",
        "member#",
        "member",
        "subscriber id",
        "id",
    ),
    "group_number": ("group #", "group#", "group number", "group no", "group", "grp"),
    "driver_license_number": ("dl", "dl#", "lic", "lic#", "license", "license no", "license number", "driver license"),
    "vehicle_year": ("year", "yr"),
    "vehicle_make": ("make",),
    "vehicle_model": ("model",),
    "vehicle_plate": ("plate", "license plate", "lic plate", "lic", "license"),
    "vehicle_vin": ("vin", "vehicle id", "vehicle identification"),
    # A Highway Patrol crash card prints "CRASH TIME:", "NCIC NUMBER:" and
    # "OFFICER'S ID NUMBER:" (a real card read on the A&P seat, 2026-10-06); a
    # city police card or receipt prints a report or case number.
    "crash_time": ("crash time",),
    "ncic_number": ("ncic number", "ncic"),
    "officer_id": ("officer's id number", "officer id number", "officer's id", "officer id"),
    "report_number": ("report #", "report#", "report number", "report no", "case #", "case number", "case no"),
}
#: Fields whose value must sit on the label's OWN line, after the label: a
#: crash card lays three short numbers out side by side, and "the line under
#: a label" would let one stand in for another.
SAME_LINE = frozenset({"crash_time", "ncic_number", "officer_id"})
#: What the reply calls each field.
WORDS: dict[str, str] = {
    "member_id": "member ID",
    "group_number": "group number",
    "driver_license_number": "driver license number",
    "vehicle_year": "vehicle year",
    "vehicle_make": "vehicle make",
    "vehicle_model": "vehicle model",
    "vehicle_plate": "license plate",
    "vehicle_vin": "VIN",
    "crash_time": "crash time",
    "ncic_number": "office code",
    "officer_id": "officer number",
    "report_number": "report number",
}
#: Fields the reply may echo back for the person to check. A driver license
#: number is not echoed: it is an identifier the reply has no reason to carry.
SHOWN_IN_REPLY = frozenset(
    {
        "member_id",
        "group_number",
        "vehicle_year",
        "vehicle_make",
        "vehicle_model",
        "crash_time",
        "ncic_number",
        "officer_id",
        "report_number",
    }
)
MIN_LENGTH = 3


@dataclass(frozen=True)
class Confirmed:
    facts: dict[str, Fact]
    #: field -> value, for the reply to list so a person checks it against the document.
    shown: dict[str, str]
    #: field -> why it was not accepted.
    refused: dict[str, str]


def _norm(text: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", text.upper())


def _has_label(line: str, labels: tuple[str, ...]) -> bool:
    low = line.lower().replace("\u2019", "'")
    return any(re.search(rf"(?<![a-z]){re.escape(label)}(?![a-z])", low) for label in labels)


def value_beside_label(text: str, value: str, labels: tuple[str, ...]) -> bool:
    """True when ``value`` (ignoring spacing and punctuation) is on a line that
    carries one of ``labels``, or on the line right under such a line."""
    want = _norm(value)
    if len(want) < MIN_LENGTH:
        return False
    lines = [ln for ln in text.splitlines() if ln.strip()]
    for i, line in enumerate(lines):
        if want not in _norm(line):
            continue
        if _has_label(line, labels):
            return True
        if i > 0 and _has_label(lines[i - 1], labels) and want not in _norm(lines[i - 1]):
            return True
    return False


def value_after_label(text: str, value: str, labels: tuple[str, ...]) -> bool:
    """True when ``value`` sits on a line AFTER one of ``labels`` on that same
    line (the ``SAME_LINE`` fields)."""
    want = _norm(value)
    if len(want) < MIN_LENGTH:
        return False
    for line in text.splitlines():
        low = line.lower().replace("\u2019", "'")
        for label in labels:
            for hit in re.finditer(rf"(?<![a-z]){re.escape(label)}(?![a-z])", low):
                if want in _norm(line[hit.end() :]):
                    return True
    return False


def _document_text(client: Any, matter_id: str, file_id: str) -> tuple[str, str]:
    """(text, file name) of a matter document, through the same extraction road
    ``read_document`` uses (cache first, so a document the agent just read costs
    nothing). Raises on a document that cannot be read."""
    from .extract import extract_text_ex

    info, blob = client.download_file(matter_id, file_id)
    name = str(info.get("name") or file_id)
    result = extract_text_ex(
        blob,
        file_name=name,
        file_extension=str(info.get("fileExtension") or ""),
        allow_vision=True,
    )
    return result.text or "", name


def confirm(client: Any, matter_id: str, cited: Any, wanted: set[str]) -> Confirmed:
    """Check each cited value in ``wanted`` against its document. ``cited`` is
    ``{field: {"value": str, "file_id": str}}`` as the agent passed it; anything
    else is treated as nothing cited."""
    cited = cited if isinstance(cited, dict) else {}
    facts: dict[str, Fact] = {}
    shown: dict[str, str] = {}
    refused: dict[str, str] = {}
    texts: dict[str, tuple[str, str] | None] = {}
    for field in sorted(wanted):
        words = WORDS.get(field, field.replace("_", " "))
        entry = cited.get(field)
        if not isinstance(entry, dict) or not str(entry.get("value") or "").strip():
            facts[field] = Fact(None, "", words)
            continue
        value = " ".join(str(entry["value"]).split())
        file_id = str(entry.get("file_id") or "").strip()
        if field not in LABELS or not file_id:
            facts[field] = Fact(None, "", f"{words} (no document cited for it)")
            refused[field] = "no document cited" if field in LABELS else "not a field read from a document"
            continue
        if file_id not in texts:
            try:
                texts[file_id] = _document_text(client, matter_id, file_id)
            except Exception:  # noqa: BLE001 - a document that cannot be read confirms nothing; the field prints its marker
                texts[file_id] = None
        doc = texts[file_id]
        if doc is None or not doc[0].strip():
            facts[field] = Fact(None, "", f"{words} (the cited document could not be read)")
            refused[field] = "the cited document could not be read"
            continue
        check = value_after_label if field in SAME_LINE else value_beside_label
        if not check(doc[0], value, LABELS[field]):
            facts[field] = Fact(None, "", f"{words} (could not be confirmed on the cited document)")
            refused[field] = f"not found beside a {words} label on {doc[1]!r}"
            continue
        facts[field] = Fact(value, f"read from {doc[1]!r} beside its {words} label")
        if field in SHOWN_IN_REPLY:
            shown[field] = value
    return Confirmed(facts=facts, shown=shown, refused=refused)


__all__ = [
    "LABELS",
    "SAME_LINE",
    "SHOWN_IN_REPLY",
    "WORDS",
    "Confirmed",
    "confirm",
    "value_after_label",
    "value_beside_label",
]
