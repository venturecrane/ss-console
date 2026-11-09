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


def styles_fits(kind: str, blob: bytes, pieces: list[str]) -> str:
    """The empty string when a styles base can be made from ``blob``
    (``styles_make.make_styles``, the very make the accept job runs, with the
    firm's identity), else the refusal's code. Writes nothing."""
    from .make import Unusable
    from .styles_make import make_styles

    try:
        make_styles(kind, blob, pieces)
    except Unusable as exc:
        return exc.code
    except Exception:  # noqa: BLE001 - any other fault on one file refuses that file, never the hunt
        return PLAN_ERROR
    return ""


def marked_fits(kind: str, blob: bytes, source: Source, pieces: list[str]) -> str:
    """The empty string when the kind's marked plan makes the whole library file
    from ``blob`` (adopt, then ``make.make``'s headers and footers judgment and
    marked places proof), else ``plan_<code>`` or ``made_<code>``."""
    from .make import Unusable, make

    why = fits(kind, blob, source)
    if why:
        return f"plan_{why}"
    try:
        make(kind, blob, source, pieces)
    except Unusable as exc:
        return f"made_{exc.code}"
    except Exception:  # noqa: BLE001 - any other fault on one file refuses that file, never the hunt
        return f"made_{PLAN_ERROR}"
    return ""


def contract_for(kind: str, blob: bytes, source: Source, pieces: list[str]) -> tuple[str, list[str]]:
    """(the contract the library file would be made with, the refusals on the way).

    A kind with a marked plan (``MARKED_KINDS``) is made marked when the firm's
    document fits that plan, keeping the firm's own words where they are
    boilerplate; otherwise, and for every other kind, it is made as a styles
    base: the firm's page, headers, footers and styles, the body written fresh.
    ("", refusals) when neither can be made, so the document is never listed."""
    from . import MARKED, MARKED_KINDS, STYLES

    refused: list[str] = []
    if kind in MARKED_KINDS:
        why = marked_fits(kind, blob, source, pieces)
        if not why:
            return MARKED, refused
        refused.append(f"marked_refused_{why}")
    why = styles_fits(kind, blob, pieces)
    if not why:
        return STYLES, refused
    return "", [*refused, f"styles_refused_{why}"]


__all__ = [
    "PLAN_ERROR",
    "REASONS",
    "adopter",
    "contract_for",
    "fits",
    "marked_fits",
    "reason_code",
    "styles_fits",
]
