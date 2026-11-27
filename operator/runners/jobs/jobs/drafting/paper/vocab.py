"""The vertical's words (design.md section 1, row (a)): what a California PI
discovery paper calls its item labels, its section titles and its types.

These are the jurisdiction's words, the same for every firm in it. How a firm
MARKS them in Word (a heading style, a bold-only line, a caps line, Word's own
numbering) is never written here: the reader tries its generic detectors for
that, and slice 2 learns which one each firm uses.
"""

from __future__ import annotations

import re

#: Item label words, longest first so "REQUEST FOR PRODUCTION" wins over "REQUEST".
LABELS = (
    "SPECIAL INTERROGATORY",
    "SPECIALLY PREPARED INTERROGATORY",
    "REQUEST FOR IDENTIFICATION AND PRODUCTION OF DOCUMENTS",
    "REQUEST FOR IDENTIFICATION AND PRODUCTION",
    "REQUEST FOR PRODUCTION OF DOCUMENTS",
    "REQUEST FOR PRODUCTION",
    "DEMAND FOR PRODUCTION",
    "DEMAND FOR INSPECTION",
    "REQUEST FOR ADMISSION",
    "INTERROGATORY",
    "REQUEST",
    "DEMAND",
)
_LABEL_ALT = "|".join(w.replace(" ", r"\s+") for w in LABELS)
#: "SPECIAL INTERROGATORY NO. 4:" at the start of a block. A decimal number
#: ("FORM INTERROGATORY NO. 2.1") is not an item label here.
LABEL = re.compile(rf"(?i)^[^\w]*({_LABEL_ALT})\s+NOS?\.?\s*(\d+)(?![.\d]*\d)\s*[:.]?")
#: "RESPONSE TO SPECIAL INTERROGATORY NO. 4:" (also supplemental, amended, further).
RESPONSE = re.compile(
    rf"(?i)^[^\w]*(?:(?:SUPPLEMENTAL|AMENDED|FURTHER|VERIFIED)\s+)*RESPONSES?\s+TO\s+({_LABEL_ALT})\s+NOS?\.?\s*(\d+)"
)

#: Section titles by region. A block is a title when one of the reader's title
#: detectors marks it AND its text (punctuation and "SET ONE"-style tails
#: aside) starts with one of these.
TITLES: dict[str, tuple[str, ...]] = {
    "definitions": ("DEFINITIONS", "DEFINITION OF TERMS"),
    "instructions": ("INSTRUCTIONS", "GENERAL INSTRUCTIONS"),
    "front": ("PRELIMINARY STATEMENT", "GENERAL OBJECTIONS", "INTRODUCTION"),
    "declaration": ("DECLARATION FOR ADDITIONAL DISCOVERY", "DECLARATION OF", "DECLARATION"),
    "verification": ("VERIFICATION",),
    "index": ("INDEX OF EXHIBITS", "EXHIBIT INDEX", "INDEX OF DOCUMENTS"),
    "service": ("PROOF OF SERVICE", "CERTIFICATE OF SERVICE"),
    "notes": (
        "NOTES FOR THE ATTORNEY",
        "NOTES FOR ATTORNEY",
        "ATTORNEY NOTES",
        "NOTES TO ATTORNEY",
        "NOTES TO THE ATTORNEY",
        "DRAFTING NOTES",
    ),
}
#: The regions that come after the items; each runs to the next one's title.
ATTACHMENTS = ("declaration", "verification", "index", "service", "notes")
FRONT = ("definitions", "instructions", "front")

#: The identification block's lines.
IDENT = re.compile(r"(?i)^\W*(PROPOUNDING\s+PARTY|RESPONDING\s+PARTY|ASKING\s+PARTY|SET\s+(?:NO\.?|NUMBER)\s*:?)")
SET_NUMBER = re.compile(r"(?i)^\W*SET\s+(?:NO\.?|NUMBER)\s*:?\s*([A-Z]+|\d+)")
_WORD_NUMBERS = ("ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN")

#: The closing's own lines: the date line and the signature lines.
CLOSING = re.compile(r"(?i)^\W*(DATED|DATE:|RESPECTFULLY\s+SUBMITTED|BY:|/s/)")
#: A block a closing may hold besides the date and signature lines: short.
CLOSING_MAX_WORDS = 14

#: Types by the words of the title.
TYPES = (
    ("special_interrogatories", ("SPECIAL INTERROGATOR", "SPECIALLY PREPARED INTERROGATOR")),
    ("form_interrogatories", ("FORM INTERROGATOR",)),
    (
        "requests_for_production",
        (
            "REQUEST FOR PRODUCTION",
            "REQUESTS FOR PRODUCTION",
            "IDENTIFICATION AND PRODUCTION",
            "DEMAND FOR",
            "DEMANDS FOR",
        ),
    ),
    ("requests_for_admission", ("REQUEST FOR ADMISSION", "REQUESTS FOR ADMISSION")),
)
#: A label word to its type, for a paper whose title does not say.
LABEL_TYPES = {
    "SPECIAL INTERROGATORY": "special_interrogatories",
    "SPECIALLY PREPARED INTERROGATORY": "special_interrogatories",
    "INTERROGATORY": "special_interrogatories",
    "REQUEST FOR IDENTIFICATION AND PRODUCTION OF DOCUMENTS": "requests_for_production",
    "REQUEST FOR IDENTIFICATION AND PRODUCTION": "requests_for_production",
    "REQUEST FOR PRODUCTION OF DOCUMENTS": "requests_for_production",
    "REQUEST FOR PRODUCTION": "requests_for_production",
    "DEMAND FOR PRODUCTION": "requests_for_production",
    "DEMAND FOR INSPECTION": "requests_for_production",
    "REQUEST FOR ADMISSION": "requests_for_admission",
    "DEMAND": "requests_for_production",
}
#: Labels that name no type ("REQUEST NO. 4", "INTERROGATORY NO. 4"): the
#: paper's type comes from its title.
GENERIC_LABELS = {"REQUEST", "INTERROGATORY"}
#: A title naming a paper that answers only some items (the challenged ones).
PARTIAL = re.compile(r"(?i)\b(AMENDED|SUPPLEMENTAL)\b")


def norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text).upper().split())


def title_region(text: str) -> str | None:
    """The region a title's words name, or None."""
    t = norm(text)
    for region, words in TITLES.items():
        if any(t == w or t.startswith(w + " ") for w in words):
            return region
    return None


def set_number(text: str) -> int | None:
    m = SET_NUMBER.match(text)
    if not m:
        return None
    v = m.group(1).upper()
    return int(v) if v.isdigit() else (_WORD_NUMBERS.index(v) + 1 if v in _WORD_NUMBERS else None)


def label_word(raw: str) -> str:
    return " ".join(raw.upper().split())
