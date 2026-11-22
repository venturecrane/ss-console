"""The reader that rules whether a set-aside update page holds nothing its keeper lacks.

WHY (2026-11-22). An update sets aside a new page judged a copy of a page
already covered. On real updates the rules set aside pages that differ (21
known pages; 75 pages whose facts never reached a delivered update), and the
free text test meant to settle copies at $0 passed 3 of the 21 and 22 of the 75
(workspace replay, 11-22). So every set-aside page that is not pixel-identical
to its keeper is put to this reader, and a page is set aside only on its
"same"; anything else, including no answer, means the page is read.

ONE DIRECTION. The question is only whether every fact on page A is also on
page B. B may say more. (The earlier draft, #4173, also asked whether facts
"differ between them", which contradicts B holding more.)

This module builds the request and reads the reply; it sends nothing. The
paid proof (11-22) and the stage call the same two functions, so what was
proven is what runs.
"""

from __future__ import annotations

import json
from typing import Any

VERSION = "same-reader-1"

SYSTEM = """PAGE CHECK FOR A MEDICAL CHRONOLOGY UPDATE

You are shown PAGE A, one page of a medical record filed for an update, and PAGE B, one to three consecutive pages already covered by the chronology. Your only task: is every fact on PAGE A also stated on PAGE B?

A fact is anything a medical chronology would state: a date of service or of any event, the patient, the provider or facility, a complaint, history, finding, diagnosis, test, result or value, a medication, dose or administration, a procedure, a plan, an order, a referral, an appointment or follow-up, and any signature, addendum or correction.

PAGE B may hold more than PAGE A. That does not matter. Only ask whether anything on PAGE A is missing from PAGE B, or stated on PAGE B with another value.

Ignore: fax headers and footers; print, export or run dates; "printed by" or "requested by" lines; page numbers and counts; Bates or exhibit stamps; scan quality, cropping, position and layout; the order in which the same facts appear.

If any part of PAGE A cannot be read well enough to compare, the answer is "missing".

Answer with one JSON object and nothing else:
{"answer": "all_on_b"}
or
{"answer": "missing", "first": "<a short quote from PAGE A of one fact not on PAGE B>"}"""

ALL_ON_B = "all_on_b"
MISSING = "missing"


def _image(png_b64: str) -> dict[str, Any]:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png_b64}}


def request(page_a: str, page_b: list[str]) -> list[dict[str, Any]]:
    """The user message: PAGE A's picture, then PAGE B's one to three pictures.
    Pictures are base64 PNG."""
    if not page_a or not 1 <= len(page_b) <= 3:
        raise ValueError("one PAGE A picture and one to three PAGE B pictures are required")
    content: list[dict[str, Any]] = [{"type": "text", "text": "PAGE A:"}, _image(page_a)]
    content.append(
        {"type": "text", "text": "PAGE B:" if len(page_b) == 1 else f"PAGE B ({len(page_b)} pages, read as one):"}
    )
    content += [_image(b) for b in page_b]
    return [{"role": "user", "content": content}]


def verdict(reply: str | None) -> str:
    """``all_on_b`` only for a reply that parses to exactly that answer; every
    other reply (empty, refused, malformed, anything else) is ``missing``, so a
    failure can only cause a read, never a dropped fact."""
    text = (reply or "").strip()
    a, b = text.find("{"), text.rfind("}")
    if not 0 <= a < b:
        return MISSING
    try:
        obj = json.loads(text[a : b + 1])
    except json.JSONDecodeError:
        return MISSING
    return ALL_ON_B if isinstance(obj, dict) and obj.get("answer") == ALL_ON_B else MISSING


def both(replies: list[str | None]) -> str:
    """Two independent readers: ``all_on_b`` only when every reply says so."""
    return ALL_ON_B if replies and all(verdict(r) == ALL_ON_B for r in replies) else MISSING
