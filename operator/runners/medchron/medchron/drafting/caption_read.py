"""The caption, read off page 1 of the court's paper by a model, and each value
checked against the page's own text layer.

WHY A MODEL. The fixed patterns this replaced were unreliable on real filings
(2026-10-08, a firm-wide survey): numbered pleading paper interleaves line
numbers with the caption, Judicial Council forms (PLD-PI-001) print a label
far from its value, and an OCR layer breaks lines anywhere. The pattern once
returned "The relief sought in this complaint is within the jurisdicti..." as a
plaintiff. A model reading the rendered page sees the caption the way a person
does.

WHY VERIFIED. A model can misread a scanned digit or regularize a spelling. A
value counts as VERIFIED only when its words appear, in order and contiguous,
in the page's own text layer: then the court's paper, not the model, is the
authority. Only a verified value may ever correct the firm's record; a scanned
page (no usable text layer) verifies nothing, so it corrects nothing.

The read is one paid call (the transcription model, stage ``caption``), with
one retry on an unparseable answer. A read that still fails leaves the caption
unread; the job continues without it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from ..stages.vision import render_page

STAGE = "caption"
MAX_TOKENS = 2_000
#: Below this many characters page 1 has no usable text layer (a scan).
MIN_TEXT_CHARS = 200
SCALARS = ("document_title", "case_number", "court_name", "county", "courthouse_or_branch")
ATTORNEY = (
    "name",
    "state_bar_number",
    "firm",
    "street",
    "city_state_zip",
    "phone",
    "fax",
    "email",
    "attorney_for",
)
SYSTEM = (
    "COPY THE CAPTION. You read page 1 of a document filed in a court case. Copy the caption exactly as it is "
    "printed on the page: the same words, spelling, capitals and punctuation. Never correct, complete, reorder or "
    "infer anything; a field the page does not print is null. Reply with ONE JSON object of exactly this shape and "
    "nothing else:\n"
    '{"document_title": str|null, "case_number": str|null, "court_name": str|null, "county": str|null, '
    '"courthouse_or_branch": str|null, "plaintiffs": [str], "defendants": [str], '
    '"attorney": {"name": str|null, "state_bar_number": str|null, "firm": str|null, "street": str|null, '
    '"city_state_zip": str|null, "phone": str|null, "fax": str|null, "email": str|null, "attorney_for": str|null}}\n'
    "court_name is the court's name only (e.g. the line naming the superior court), county the county it names, "
    "courthouse_or_branch a courthouse or branch the page names. plaintiffs and defendants are each party's name "
    "as printed, one entry per party, without descriptions such as 'an individual'; a cross-complaint's parties "
    "go on the side the page puts them. attorney is the attorney block at the top of the page."
)
RETRY = "Reply with the JSON object only."


class Unread(RuntimeError):
    """The caption could not be read; the job continues without it."""


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text).lower())


def verified(value: Any, page: list[str]) -> bool:
    """The value's words appear contiguously, in order, in the page's words."""
    want = words(value) if isinstance(value, str) else []
    if not want or len(want) > len(page):
        return False
    n = len(want)
    return any(page[i : i + n] == want for i in range(len(page) - n + 1))


def parse(text: str) -> dict[str, Any] | None:
    """The outermost ``{...}`` of the answer, as an object of the caption's
    shape; None when there is none or it is not that shape."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None

    def s(v: Any) -> str | None:
        return " ".join(v.split()) if isinstance(v, str) and v.strip() else None

    out: dict[str, Any] = {k: s(obj.get(k)) for k in SCALARS}
    for side in ("plaintiffs", "defendants"):
        raw = obj.get(side)
        out[side] = [x for x in (s(v) for v in raw) if x] if isinstance(raw, list) else []
    raw_att = obj.get("attorney")
    att: dict[str, Any] = raw_att if isinstance(raw_att, dict) else {}
    out["attorney"] = {k: s(att.get(k)) for k in ATTORNEY}
    return out


def page_one(source: str, log: Callable[[str], None], name: str) -> tuple[str, str]:
    """(page 1 as base64 PNG at 150 dpi, page 1's text layer)."""
    import pymupdf

    with pymupdf.open(source) as doc:
        page = doc[0]
        return render_page(page, log, name, 1), page.get_text() or ""


def read(doorway: Any, model: str, doc: dict[str, Any], log: Callable[[str], None]) -> dict[str, Any]:
    """{"caption": the parsed caption, "text_chars": page 1's text-layer size}.
    Raises ``Unread`` when the page cannot be rendered or the answer will not
    parse after one retry. A ``LimitHold`` from the doorway passes through."""
    from ..limits import LimitHold

    name = str(doc.get("name") or "")
    try:
        b64, text = page_one(str(doc["source_path"]), log, name)
    except Exception as exc:  # noqa: BLE001 - an unrenderable page leaves the caption unread, never fails the job
        raise Unread(f"page 1 could not be rendered ({type(exc).__name__})") from None
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                {"type": "text", "text": "Copy this page's caption."},
            ],
        }
    ]
    for attempt in range(2):
        try:
            res = doorway.call(STAGE, model=model, system=SYSTEM, messages=messages, max_tokens=MAX_TOKENS, effort="")
        except LimitHold:
            raise
        except Exception as exc:  # noqa: BLE001 - a failed read leaves the caption unread, never fails the job
            raise Unread(f"the caption read did not finish ({type(exc).__name__})") from None
        got = parse(res.text)
        if got is not None:
            return {"caption": got, "text_chars": len(text.strip()), "text": text}
        if attempt == 0:
            messages = [
                *messages,
                {"role": "assistant", "content": res.text or "{}"},
                {"role": "user", "content": RETRY},
            ]
    raise Unread("the caption read did not return the caption's JSON after one retry")


def fields(got: dict[str, Any], firm_domains: tuple[str, ...], ours: bool) -> dict[str, Any]:
    """The caption as the comparison and the compose prompt read it: each value
    ``{value, verified, quote}``. ``ours`` is False for an answer, whose
    attorney block is opposing counsel's: its attorney fields are left out."""
    cap, text = got["caption"], got.get("text") or ""
    page = words(text) if int(got.get("text_chars") or 0) >= MIN_TEXT_CHARS else []

    def one(v: str) -> dict[str, Any]:
        ok = verified(v, page)
        return {"value": v, "verified": ok, "quote": _line(text, v) if ok else ""}

    out: dict[str, Any] = {k: one(cap[k]) for k in SCALARS if cap.get(k)}
    for side in ("plaintiffs", "defendants"):
        out[side] = [one(v) for v in cap.get(side) or []]
    att = cap.get("attorney") or {}
    email = att.get("email")
    if ours and email and email.rsplit("@", 1)[-1].lower() in firm_domains:
        out["attorney_email"] = one(email)
    if ours:
        out["attorney"] = {k: v for k, v in att.items() if v}
    return out


def _line(text: str, value: str) -> str:
    """The text-layer line the value starts on, for the attorney's notes."""
    first = words(value)[:1]
    for ln in text.splitlines():
        if first and first[0] in words(ln):
            return " ".join(ln.split())[:200]
    return value


def cached(data: Path) -> dict[str, Any] | None:
    p = data / "caption-read.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def save(data: Path, got: dict[str, Any]) -> None:
    tmp = data / ".caption-read.json.tmp"
    tmp.write_text(json.dumps(got, indent=1), encoding="utf-8")
    tmp.replace(data / "caption-read.json")
