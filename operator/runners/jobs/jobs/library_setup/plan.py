"""Whether the kind's plan can make a library file from a document: asked at
hunt time, before a candidate is listed, so the firm is never shown a document
that cannot be its file.

THE SAME PATH AS THE ACCEPT. ``fits`` runs the kind's own adopt
(``drafting/brief_base.adopt``, ``drafting/memo_base.adopt``), the very read the
accept job makes, on the candidate's bytes. It writes nothing. A refusal is
named by a short code read from the plan's own reason (``reason_code``), so the
hunt report counts candidates dropped by why, never by what they said.

WHY (the A&P rehearsal, 2026-11-07). Every memo candidate the firm would have
been shown was refused by the memo plan ("the firm's memo has no heading to read
its roles from"): an administrator would have picked one and been told it kept
words of its case, the wrong reason. Now such a candidate is not listed, and a
kind whose every candidate is dropped reads back as none found.
"""

from __future__ import annotations

import re
import zipfile
from typing import Any

from lxml import etree

from ..firmbase import FirmBaseUnfit, Source

#: The plan's reasons, by the words it uses, to a code; the first that matches.
REASONS = (
    (re.compile(r"(?i)no heading"), "no_heading"),
    (re.compile(r"(?i)no caption table"), "no_caption_table"),
    (re.compile(r"(?i)no chart"), "no_chart"),
    (re.compile(r"(?i)no signature"), "no_signature"),
    (re.compile(r"(?i)no title"), "no_title"),
    (re.compile(r"(?i)no plain paragraph"), "no_plain_paragraph"),
    (re.compile(r"(?i)more than one table"), "front_tables"),
    (re.compile(r"(?i)operator's own"), "operator_lineage"),
    (re.compile(r"(?i)survived the blanking"), "words_survived"),
)


def reason_code(exc: BaseException) -> str:
    text = str(exc)
    for pattern, code in REASONS:
        if pattern.search(text):
            return code
    return "not_a_word_file" if isinstance(exc, (zipfile.BadZipFile, etree.XMLSyntaxError, KeyError)) else "other"


def adopter(kind: str) -> Any:
    from ..drafting import brief_base, memo_base

    return {"mediation_brief": brief_base.adopt, "memo": memo_base.adopt}[kind]


#: A plan that raised something it does not name: the candidate is refused and
#: counted, and the hunt goes on (one firm file must never end a hunt).
PLAN_ERROR = "plan_error"


def fits(kind: str, blob: bytes, source: Source) -> str:
    """The empty string when the kind's plan can adopt ``blob``, else the refusal's code."""
    from ..drafting.outcome import DraftingFailed

    try:
        adopter(kind)(blob, source)
    except (DraftingFailed, FirmBaseUnfit, KeyError, ValueError, zipfile.BadZipFile, etree.XMLSyntaxError) as exc:
        return reason_code(exc)
    except Exception:  # noqa: BLE001 - any other fault of the plan on one file refuses that file, never the hunt
        return PLAN_ERROR
    return ""


__all__ = ["PLAN_ERROR", "REASONS", "adopter", "fits", "reason_code"]
