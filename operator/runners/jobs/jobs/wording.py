"""The firm's required wording: one home for every phrase a writer prompt makes
the model write, so the writers and the checkers cannot disagree about it.

WHY (2026-10-23). The A&P chronology lane has never delivered unattended, and
on the last job (Curtis, 3CH59XBY) three of its stops were the pre-delivery
review flagging wording the writer prompts REQUIRE: the map prompt orders
"(provider not stated in the record)" when no page names the clinician, and
the "[NTD: ...]" note on a handwritten record, and the review graded both
"the system's own language" (check 3, MUST-FIX). The phrase lived in the
writer prompt, a copy lived in ``update/illegible.py``, another in
``update/handwritten.py``, and the checkers had never heard of any of them.

Now the phrase lives here. Writer prompts carry ``{{WORDING:NAME}}`` and
``prompts.load`` fills it; the checker prompts get ``{{WORDING_EXEMPT}}``, a
list rendered from the same constants; the code checkers are proven against
them (``tests/test_wording_contract.py``, which also fails when a writer prompt
mandates a phrase that is not here).

Two kinds:

* ``FIRM``: wording that reaches the attorney on purpose. Never a finding.
* ``INTERNAL``: markers one pass writes for the next, removed before anything
  is rendered (``merge`` strips the continuation marker). These are NOT
  exempt: one that leaked into the delivered document is a real defect.
"""

from __future__ import annotations

import re

#: map-system.md: the facility when no page of the file names the clinician.
PROVIDER_NOT_STATED = "(provider not stated in the record)"
#: map-system.md, verbatim house note on a handwritten or illegible portion
#: (the update lane re-inserts the same note, ``update/illegible.py``).
NTD_ILLEGIBLE = (
    "[NTD: A portion of the records were handwritten and illegible. Therefore, we were unable to annotate or "
    "decipher a portion of these records and were unable to draft a complete medical summary for this treatment.]"
)
#: map-system.md: a handwritten log row whose log states no year anywhere
#: (``update/handwritten.py`` writes it on a date line too).
YEAR_NOT_STATED = "(year not stated in the record)"
#: map-system.md: the label on an entry keyed to the only legible date, a bill's.
BILLING_DATE = "(billing statement date)"
#: merge-system.md: appended when two fragments of one visit disagree; the
#: house style is fidelity over reconciliation, so both readings stay, cited.
RECORDS_DIFFER = "The records differ on this point."
#: map-system.md: the standardized label for the qualifying event.
SUBJECT_INCIDENT = "the Subject Incident"
#: map-system.md: a page code whose legend meaning is not legible.
CODE_NOT_DEFINED = "a code the legend does not legibly define"
#: map-system.md: the rest of a partially legible passage.
NOT_LEGIBLE = "not legible"
#: update-finish-system.md (``update/finisher.py`` AS_READ): what a handwritten
#: form shows that no reader can settle.
MARKED_AS_SHOWN = "marked as shown on the page"
AS_WRITTEN = "as written on the page"

#: map-system.md -> merge-system.md: a fragment that runs on into the next chunk.
CONTINUES = "[entry may continue in next chunk]"

FIRM: dict[str, str] = {
    "PROVIDER_NOT_STATED": PROVIDER_NOT_STATED,
    "NTD_ILLEGIBLE": NTD_ILLEGIBLE,
    "YEAR_NOT_STATED": YEAR_NOT_STATED,
    "BILLING_DATE": BILLING_DATE,
    "RECORDS_DIFFER": RECORDS_DIFFER,
    "SUBJECT_INCIDENT": SUBJECT_INCIDENT,
    "CODE_NOT_DEFINED": CODE_NOT_DEFINED,
    "NOT_LEGIBLE": NOT_LEGIBLE,
    "MARKED_AS_SHOWN": MARKED_AS_SHOWN,
    "AS_WRITTEN": AS_WRITTEN,
}
INTERNAL: dict[str, str] = {"CONTINUES": CONTINUES}
ALL: dict[str, str] = {**FIRM, **INTERNAL}

#: What a reviewer's finding names when one of these is its subject: the
#: phrase itself, or the fragment a person quoting it would use (a reviewer
#: writes "the NTD note", not the 190-character note).
_MARKERS: dict[str, tuple[str, ...]] = {
    "PROVIDER_NOT_STATED": ("provider not stated",),
    "NTD_ILLEGIBLE": ("[ntd", "ntd:", "ntd note", "ntd paragraph", "handwritten and illegible"),
    "YEAR_NOT_STATED": ("year not stated",),
    "BILLING_DATE": ("billing statement date",),
    "RECORDS_DIFFER": ("records differ on this point", "the records differ"),
    "SUBJECT_INCIDENT": ("subject incident",),
    "CODE_NOT_DEFINED": ("does not legibly define",),
    "NOT_LEGIBLE": ("not legible",),
    "MARKED_AS_SHOWN": ("marked as shown on the page",),
    "AS_WRITTEN": ("as written on the page",),
}

TOKEN = re.compile(r"\{\{WORDING:([A-Z_]+)\}\}")
EXEMPT_TOKEN = "{{WORDING_EXEMPT}}"


def fill(text: str) -> str:
    """`text` with every ``{{WORDING:NAME}}`` replaced by its phrase and
    ``{{WORDING_EXEMPT}}`` by the exemption list. An unknown name raises: a
    prompt that names wording nobody defined must not reach a model."""

    def one(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in ALL:
            raise KeyError(f"prompt names unknown wording {name!r}")
        return ALL[name]

    return TOKEN.sub(one, text).replace(EXEMPT_TOKEN, exempt_list())


def exempt_list() -> str:
    """The firm's required wording, one quoted line each, for a checker prompt."""
    return "\n".join(f'- "{p}"' for p in FIRM.values())


def subjects(text: str) -> list[str]:
    """The FIRM phrases (by name) a finding's text names as its subject."""
    low = " ".join((text or "").lower().split())
    return [k for k, ms in _MARKERS.items() if any(m in low for m in ms)]


def names_records_differ(text: str) -> bool:
    return "RECORDS_DIFFER" in subjects(text)
