"""The caption, verbatim from the court's own paper, and where the matter record
disagrees with it.

A court document's caption is the authority for a pleading's caption: the
operative pleading (the latest complaint or amended complaint) or, failing
one, the latest court notice. This module reads the caption fields out of that
document's TEXT with fixed patterns (no model), so each field is the court's
own words, and compares the exact-match fields to the Smokeball matter record:

    case number, court name, party names, attorney email

A discrepancy is reported only at high confidence, and each quotes the line of
the document it rests on:

* a field the document carries one unambiguous value for, and the record
  carries a different value that is CLOSE to it (a typo, a transposed digit,
  a misspelled name), or carries none at all (a missing email);
* anything ambiguous (two case numbers in the document, a party line the
  pattern cannot isolate, two very different names that are probably two
  different people) is not reported. A wrong discrepancy sent to the firm
  costs more than a missed one.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

PLEADING_NAME = re.compile(r"(?i)\b(first |second |third |fourth )?(amended )?complaint\b")
NOTICE_NAME = re.compile(r"(?i)\b(notice|minute order|order|summons)\b")
CASE_NO = re.compile(r"(?i)\bcase\s+(?:no|number)\.?\s*:?\s*([A-Z0-9][A-Z0-9-]{4,30})")
COURT = re.compile(
    r"(?i)(superior court of (?:the state of )?california)[,\s]*\n?\s*(?:for the )?(county of [a-z ]{3,40})"
)
PLAINTIFF = re.compile(r"(?m)^[^\S\n]*([A-Z][A-Za-z .,'&-]{2,80}?),?\s*(?:an individual,?\s*)?\n?\s*Plaintiffs?\b")
DEFENDANT = re.compile(
    r"(?m)^[^\S\n]*v[s]?\.\s*\n?\s*([A-Z][A-Za-z .,'&-]{2,80}?),?\s*(?:an individual,?\s*)?\n?\s*Defendants?\b"
)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
CLOSE = 0.8
HEAD_CHARS = 6000


def _norm(s: str) -> str:
    return " ".join(str(s).split()).strip(" ,.").casefold()


def _line(text: str, needle: str) -> str:
    for ln in text.splitlines():
        if needle.casefold() in ln.casefold():
            return " ".join(ln.split())[:200]
    return needle


def _dated(row: dict[str, Any]) -> str:
    """A sort key: the date in the name, else the name (stable)."""
    m = re.search(r"(\d{1,2})[-./](\d{1,2})[-./](\d{2,4})", str(row.get("name") or ""))
    if not m:
        return "0000"
    y = int(m.group(3))
    y = y + 2000 if y < 100 else y
    return f"{y:04d}{int(m.group(1)):02d}{int(m.group(2)):02d}"


def source_document(data: Path) -> dict[str, Any] | None:
    """The operative pleading (latest complaint) else the latest court notice
    whose text carries a court caption; None when the file has neither."""
    rows = [json.loads(ln) for ln in (data / "extracted.jsonl").read_text(encoding="utf-8").splitlines() if ln]
    cands = []
    for r in rows:
        p = r.get("text_path")
        if not p or not Path(p).is_file():
            continue
        text = Path(p).read_text(encoding="utf-8", errors="replace")[:HEAD_CHARS]
        if not (CASE_NO.search(text) or COURT.search(text)):
            continue
        name = str(r.get("name") or "")
        rank = 2 if PLEADING_NAME.search(name) else 1 if NOTICE_NAME.search(name) else 0
        if rank:
            cands.append((rank, _dated(r), name, {**r, "head": text}))
    if not cands:
        return None
    return max(cands, key=lambda c: (c[0], c[1], c[2]))[3]


def extract(doc: dict[str, Any], firm_domains: tuple[str, ...]) -> dict[str, dict[str, str]]:
    """{field: {value, quote}} for each field the document carries exactly one
    value for. Fields: case_number, court, plaintiff, defendant, attorney_email."""
    text, out = doc["head"], {}

    def one(field: str, values: list[str], quote_of: str | None = None) -> None:
        distinct = {_norm(v): v for v in values if v.strip()}
        if len(distinct) == 1:
            v = " ".join(next(iter(distinct.values())).split()).strip(" ,")
            out[field] = {"value": v, "quote": _line(text, quote_of or v)}

    one("case_number", [m.group(1) for m in CASE_NO.finditer(text)])
    first_court = COURT.search(text)
    one(
        "court",
        [f"{m.group(1)}, {m.group(2)}".upper() for m in COURT.finditer(text)],
        quote_of=first_court.group(1) if first_court else None,
    )
    # "Attorneys for Plaintiff" is counsel's line, not a party.
    one("plaintiff", [m.group(1) for m in PLAINTIFF.finditer(text) if "attorney" not in m.group(1).lower()])
    one("defendant", [m.group(1) for m in DEFENDANT.finditer(text)])
    one("attorney_email", [e for e in EMAIL.findall(text) if e.rsplit("@", 1)[-1].lower() in firm_domains])
    return out


def _court(s: str) -> str:
    """A court's name with its interchangeable words out ("of the State of",
    "for the County of" and "County of" read the same)."""
    t = re.sub(r"(?i)\b(of|the|state|for)\b", " ", s)
    return " ".join(re.findall(r"[a-z]+", t.lower()))


def _close(a: str, b: str) -> bool:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio() >= CLOSE


def compare(caption: dict[str, dict[str, str]], record: dict[str, Any], doc_name: str) -> list[dict[str, str]]:
    """The discrepancies worth reporting. ``record`` carries ``case_number``,
    ``court`` (strings or None), ``plaintiffs`` / ``defendants`` (lists of
    names) and ``attorney_email`` (or None)."""
    out = []

    def report(field: str, record_value: str | None, why: str) -> None:
        c = caption[field]
        out.append(
            {
                "field": field,
                "document_value": c["value"],
                "record_value": record_value or "",
                "why": why,
                "source": doc_name,
                "quote": c["quote"],
            }
        )

    for field in ("case_number", "court", "attorney_email"):
        if field not in caption:
            continue
        rec = record.get(field)
        if not rec:
            if field == "attorney_email":
                report(field, None, "the matter record carries no email for the responsible attorney")
            continue
        same = (
            _court(rec) == _court(caption[field]["value"])
            if field == "court"
            else _norm(rec) == _norm(caption[field]["value"])
        )
        if not same and (field == "case_number" or _close(rec, caption[field]["value"])):
            report(field, rec, "differs from the court's paper")
    for field, key in (("plaintiff", "plaintiffs"), ("defendant", "defendants")):
        names = [n for n in record.get(key) or [] if n]
        if field not in caption or not names:
            continue
        v = caption[field]["value"]
        if any(_norm(n) == _norm(v) for n in names):
            continue
        near = [n for n in names if _close(n, v)]
        if len(near) == 1:  # one record name a near-miss of the caption: a typo, not another party
            report(field, near[0], "spelled differently from the court's paper")
    return out


def record_from_client(client: Any, matter_id: str, facts: dict[str, Any]) -> dict[str, Any]:
    """The matter record's side, read through the connector's own readers. A
    read that fails leaves the field None, and None is never compared."""
    from smokeball_connector import form_letter_facts as flf

    rec: dict[str, Any] = {
        "case_number": None,
        "court": None,
        "plaintiffs": list(facts.get("client_names") or []),
        "defendants": [],
        "attorney_email": None,
    }
    try:
        layout = flf.matter_layout_values(client, matter_id)
    except Exception:  # noqa: BLE001 - unreadable: the field is not compared
        layout = {}
    for field, pat in (("case_number", r"(?i)case.?(no|number)$"), ("court", r"(?i)court(.?name)?$")):
        vals = {str(v).strip() for k, v in layout.items() if re.search(pat, str(k)) and str(v or "").strip()}
        rec[field] = next(iter(vals)) if len(vals) == 1 else None
    try:
        parties = flf.read_parties(client, matter_id)
        for role in parties.roles:
            if role.get("isOtherSide") is True and role.get("contactId"):
                n = flf.contact_name(flf.fetch_contact(client, role["contactId"]))
                if n:
                    rec["defendants"].append(n)
    except Exception:  # noqa: BLE001 - an unreadable record field is left None and never compared
        pass
    try:
        matter = client.get(f"/matters/{matter_id}") or {}
        staff_id = matter.get("personResponsibleStaffId")
        staff = client.get(f"/staff/{staff_id}") if staff_id else {}
        email = staff.get("email") if isinstance(staff, dict) else None
        rec["attorney_email"] = email if isinstance(email, str) and "@" in email else None
    except Exception:  # noqa: BLE001 - an unreadable record field is left None and never compared
        pass
    return rec


def read_record(seat: Any, matter_id: str, facts: dict[str, Any]) -> dict[str, Any]:
    own = getattr(seat, "caption_record", None)
    if callable(own):
        got = own(matter_id)
        if isinstance(got, dict):
            return {str(k): v for k, v in got.items()}
    client = getattr(seat, "client", None)
    if client is None or callable(own):
        return {"case_number": None, "court": None, "plaintiffs": [], "defendants": [], "attorney_email": None}
    return record_from_client(client, matter_id, facts)


def block(caption: dict[str, dict[str, str]], doc_name: str | None) -> str:
    """The caption fields as the compose prompt receives them."""
    if not caption:
        return "THE CAPTION: no court document in the file carries a caption; write {{NOT IN RECORD}} for each caption field."
    lines = [f"- {k.replace('_', ' ')}: {v['value']}" for k, v in caption.items()]
    return (
        f"THE CAPTION (verbatim from {doc_name}; use these exact values in the caption, "
        "never the matter record's):\n" + "\n".join(lines)
    )
