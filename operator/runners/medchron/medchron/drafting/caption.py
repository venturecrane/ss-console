"""Which court document the caption is read from, the matter record's side of
it, and where the two disagree.

A court document's caption is the authority for a pleading's caption: the
latest complaint, amended complaint or cross-complaint; failing one, an answer
(whose attorney block is opposing counsel's, so its attorney fields are never
used); failing that, the latest court notice, order or summons. The caption
itself is read off that document's page 1 by ``caption_read``; this module
compares it to the Smokeball matter record:

    case number, court, county, party names, attorney email

A difference is reported only at high confidence, and each quotes the line of
the court's paper it rests on:

* a field the court's paper carries and the record carries differently, CLOSE
  to it (a typo, a transposed digit, a misspelled name), or carries empty
  (a missing case number or email);
* a record that could not be read is never compared and never reported as
  agreeing: the run ends failed (``RecordUnreadable``);
* anything ambiguous (a party name close to two record names, two very
  different names that are probably two different people) is not reported.
  A wrong discrepancy sent to the firm costs more than a missed one.

A difference the court's paper settles beyond doubt (``caption_rules``) also
carries a ``fix``: ``caption_fix`` corrects the record from it.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any, Callable

from . import caption_rules as rules

ANSWER_NAME = re.compile(r"(?i)\banswer\b")
PLEADING_NAME = re.compile(r"(?i)\b(first |second |third |fourth )?(amended )?(cross-?\s?)?complaint\b")
NOTICE_NAME = re.compile(r"(?i)\b(notice|minute order|order|summons)\b")
#: The label is case-blind; the value is not: upper-case letters, digits and
#: hyphens, with at least one digit ("Case No. pending" is no case number).
CASE_NO = re.compile(r"(?i:\bcase\s+(?:no|number)\.?\s*:?\s*)([A-Z0-9][A-Z0-9-]{4,30})\b")
COURT = re.compile(r"(?i)superior court of")
#: What page 1 can be rendered from (a .docx cannot).
RENDERABLE = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff")
CASE_KEY = "Matter/CaseDetails/StandardCaseDetails/CaseNumber"
HEAD_CHARS = 6000
#: Ranked: a complaint over an answer over a court notice.
RANKS = {"pleading": 3, "answer": 2, "notice": 1}

Report = Callable[..., None]


def _norm(s: str) -> str:
    return " ".join(str(s).split()).strip(" ,.").casefold()


def _dated(row: dict[str, Any]) -> str:
    """A sort key: the date in the name, else the name (stable)."""
    m = re.search(r"(\d{1,2})[-./](\d{1,2})[-./](\d{2,4})", str(row.get("name") or ""))
    if not m:
        return "0000"
    y = int(m.group(3))
    y = y + 2000 if y < 100 else y
    return f"{y:04d}{int(m.group(1)):02d}{int(m.group(2)):02d}"


def _kind(name: str) -> str | None:
    if ANSWER_NAME.search(name):
        return "answer"
    if PLEADING_NAME.search(name):
        return "pleading"
    return "notice" if NOTICE_NAME.search(name) else None


def source_document(data: Path) -> dict[str, Any] | None:
    """The latest complaint, else the latest answer, else the latest court
    notice, whose text carries a court caption and whose page 1 can be
    rendered; None when the file has none. The row gains ``kind``."""
    rows = [json.loads(ln) for ln in (data / "extracted.jsonl").read_text(encoding="utf-8").splitlines() if ln]
    cands = []
    for r in rows:
        p, src = r.get("text_path"), str(r.get("source_path") or "")
        if not p or not Path(p).is_file() or not src.lower().endswith(RENDERABLE) or not Path(src).is_file():
            continue
        text = Path(p).read_text(encoding="utf-8", errors="replace")[:HEAD_CHARS]
        if not (CASE_NO.search(text) or COURT.search(text)):
            continue
        name = str(r.get("name") or "")
        kind = _kind(name)
        if kind:
            cands.append((RANKS[kind], _dated(r), name, {**r, "kind": kind}))
    if not cands:
        return None
    return max(cands, key=lambda c: (c[0], c[1], c[2]))[3]


def _court(s: str) -> list[str]:
    """A court's words with its interchangeable ones out ("of the State of",
    "for the County of" and "County of" read the same)."""
    return [w for w in re.findall(r"[a-z]+", str(s).lower()) if w not in ("of", "the", "state", "for")]


def _county(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", str(s).lower()) if w not in ("of", "the", "county")]


def _within(inner: list[str], outer: list[str]) -> bool:
    n = len(inner)
    return bool(inner) and any(outer[i : i + n] == inner for i in range(len(outer) - n + 1))


class RecordUnreadable(RuntimeError):
    """The matter record could not be read: never compared, never "agrees"."""


def _parties(record: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(client-side, other-side) parties as ``{name, contact_id}``. A record
    without ``parties`` (a seat's own reader) gives plaintiffs as the client
    side and defendants as the other, with no contact to write to."""
    if isinstance(record.get("parties"), list):
        rows = [p for p in record["parties"] if isinstance(p, dict) and p.get("name")]
        return [p for p in rows if p.get("side") == "client"], [p for p in rows if p.get("side") == "other"]
    return (
        [{"name": n} for n in record.get("plaintiffs") or [] if n],
        [{"name": n} for n in record.get("defendants") or [] if n],
    )


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, " ".join(rules.tokens(a)), " ".join(rules.tokens(b))).ratio()


def _score(caption: list[dict[str, Any]], record: list[dict[str, Any]]) -> float:
    return sum(max((_ratio(c["value"], p["name"]) for p in record), default=0.0) for c in caption)


def client_side(caption: dict[str, Any], record: dict[str, Any]) -> str:
    """Which caption side ("plaintiffs" or "defendants") is the firm's client:
    the one whose names best match the record's client roles."""
    ours, theirs = _parties(record)
    pl, df = caption.get("plaintiffs") or [], caption.get("defendants") or []
    straight = _score(pl, ours) + _score(df, theirs)
    crossed = _score(df, ours) + _score(pl, theirs)
    return "defendants" if crossed > straight else "plaintiffs"


def compare(caption: dict[str, Any], record: dict[str, Any], doc_name: str) -> tuple[list[dict[str, Any]], list[str]]:
    """(the discrepancies worth reporting, the fields actually compared).

    ``caption`` is ``caption_read.fields``: each value ``{value, verified,
    quote}``, ``plaintiffs`` / ``defendants`` lists of them. ``record`` carries
    ``case_number``, ``court``, ``county`` and ``attorney_email`` (a string, ""
    when read and empty, None when there is no such field) and ``parties``
    (``{side, name, contact_id}``). None is never compared or reported.

    A discrepancy the court's paper settles beyond doubt carries ``fix``."""
    out: list[dict[str, Any]] = []
    compared: list[str] = []

    def report(field: str, c: dict[str, Any], record_value: str | None, why: str, fix: Any = None) -> None:
        row = {
            "field": field,
            "document_value": c["value"],
            "record_value": record_value or "",
            "why": why,
            "source": doc_name,
            "quote": c.get("quote") or "",
        }
        if fix:
            row["fix"] = fix
        out.append(row)

    _case_number(caption, record, compared, report)
    _court_fields(caption, record, compared, report)
    _email(caption, record, compared, report)
    _names(caption, record, compared, report)
    return out, compared


def _case_number(caption: dict[str, Any], record: dict[str, Any], compared: list[str], report: Report) -> None:
    rec, c = record.get("case_number"), caption.get("case_number")
    if c is None or rec is None:
        return
    compared.append("case_number")
    v = c["value"]
    if rec == "":
        fix = {"kind": "case_number"} if c.get("verified") and rules.fillable(v) else None
        report("case_number", c, None, "the matter record carries no case number", fix)
    elif _norm(rec) != _norm(v):
        if c.get("verified") and rules.dropped_zeros(v, rec):
            why = "the matter record's number drops zeros the court's paper prints"
            report("case_number", c, rec, why, {"kind": "case_number"})
        elif rules.close(rec, v):
            report("case_number", c, rec, "differs from the court's paper")


def _court_fields(caption: dict[str, Any], record: dict[str, Any], compared: list[str], report: Report) -> None:
    """Court and county: reported, never corrected."""
    for field, key, same in (
        ("court", "court_name", lambda rec, v: _within(_court(rec), _court(v))),
        ("county", "county", lambda rec, v: _county(rec) == _county(v)),
    ):
        rec = record.get(field)
        if key not in caption or not isinstance(rec, str) or not rec:
            continue
        compared.append(field)
        if not same(rec, caption[key]["value"]):
            report(field, caption[key], rec, "differs from the court's paper")


def _email(caption: dict[str, Any], record: dict[str, Any], compared: list[str], report: Report) -> None:
    rec, c = record.get("attorney_email"), caption.get("attorney_email")
    if c is None or rec is None:
        return
    compared.append("attorney_email")
    if rec == "":
        report("attorney_email", c, None, "the matter record carries no email for the responsible attorney")
    elif _norm(rec) != _norm(c["value"]) and rules.close(rec, c["value"]):
        report("attorney_email", c, rec, "differs from the court's paper")


def _names(caption: dict[str, Any], record: dict[str, Any], compared: list[str], report: Report) -> None:
    ours, theirs = _parties(record)
    mine = client_side(caption, record)
    for side, field in (("plaintiffs", "plaintiff"), ("defendants", "defendant")):
        names = caption.get(side) or []
        pool = ours if side == mine else theirs
        if not names or not pool:
            continue
        compared.append(field)
        for c in names:
            if any(rules.same_name(p["name"], c["value"]) for p in pool):
                continue
            near = [p for p in pool if rules.close(p["name"], c["value"])]
            if len(near) != 1:  # none: another party; two: ambiguous
                continue
            p = near[0]
            fix = None
            if c.get("verified") and p.get("contact_id") and rules.is_misspelling(c["value"], p["name"]):
                fix = {"kind": "contact", "contact_id": p["contact_id"]}
            report(field, c, p["name"], "spelled differently from the court's paper", fix)


def _one(layout: dict[str, Any], suffix: str) -> str | None:
    """The one value the layout carries under keys ending ``/<suffix>``: None
    when no such key exists or two keys disagree."""
    keys = [k for k in layout if str(k).endswith("/" + suffix)]
    vals = {str(layout[k] or "").strip() for k in keys}
    return (next(iter(vals)) if len(vals) == 1 else None) if keys else None


def case_item(client: Any, matter_id: str) -> tuple[str, dict[str, Any]] | None:
    """(the layout item carrying the case-number key, all its values), even
    when the number is empty: the merged layout drops empty values, so an
    empty case number would otherwise read as "no such field"."""
    from smokeball_connector.medicals_layout import _items, layout_values

    for item in _items(client.get(f"/matters/{matter_id}/layouts")):
        item_id = (item.get("id") or item.get("itemId")) if isinstance(item, dict) else None
        if isinstance(item_id, str):
            values = layout_values(client.get(f"/matters/{matter_id}/layouts/{item_id}"))
            if CASE_KEY in values:
                return item_id, values
    return None


def record_from_client(client: Any, matter_id: str, facts: dict[str, Any]) -> dict[str, Any]:
    """The matter record's side, read through the connector's own readers. A
    read that FAILS raises ``RecordUnreadable``: "could not look" must never
    read as "agrees" (the job ends failed, resumable). A field the matter's
    layout does not carry at all is None, never compared.

    The court lives in four layout keys: AuthorityCourt (an id),
    JurisdictionCourt ("Superior Court"), LocationCounty and LocationDivision.
    A pattern on "court" once matched two of them and so read neither: the
    court was never compared. Each is now read by its own key."""
    from smokeball_connector import form_letter_facts as flf

    rec: dict[str, Any] = {"case_number": None, "court": None, "county": None, "attorney_email": None, "parties": []}
    try:
        layout = flf.matter_layout_values(client, matter_id)
        found = case_item(client, matter_id)
        rec["case_number"] = str(found[1].get(CASE_KEY) or "").strip() if found else None
        rec["court"] = _one(layout, "JurisdictionCourt")
        rec["county"] = _one(layout, "LocationCounty")
        for role in flf.read_parties(client, matter_id).roles:
            side = "client" if role.get("isClient") is True else "other" if role.get("isOtherSide") is True else None
            cid = role.get("contactId")
            if side and isinstance(cid, str) and cid:
                n = flf.contact_name(flf.fetch_contact(client, cid))
                if n:
                    rec["parties"].append({"side": side, "name": n, "contact_id": cid})
        if not any(p["side"] == "client" for p in rec["parties"]):
            rec["parties"] += [{"side": "client", "name": n} for n in facts.get("client_names") or [] if n]
        matter = client.get(f"/matters/{matter_id}") or {}
        staff_id = matter.get("personResponsibleStaffId")
        if staff_id:
            staff = client.get(f"/staff/{staff_id}")
            email = staff.get("email") if isinstance(staff, dict) else None
            rec["attorney_email"] = email.strip() if isinstance(email, str) and "@" in email else ""
    except Exception as exc:  # noqa: BLE001 - re-raised as the one error the run turns into record_unreadable
        raise RecordUnreadable(f"{type(exc).__name__}: {str(exc)[:200]}") from None
    return rec


def read_record(seat: Any, matter_id: str, facts: dict[str, Any]) -> dict[str, Any]:
    own = getattr(seat, "caption_record", None)
    if callable(own):
        got = own(matter_id)
        if not isinstance(got, dict):
            raise RecordUnreadable("the seat returned no caption record")
        return {str(k): v for k, v in got.items()}
    client = getattr(seat, "client", None)
    if client is None:
        raise RecordUnreadable("the seat backend has no connector client; the matter record was not read")
    return record_from_client(client, matter_id, facts)


def block(caption: dict[str, Any], doc_name: str | None) -> str:
    """The caption fields as the compose prompt receives them."""
    if not caption:
        return (
            "THE CAPTION: no court document in the file carries a readable caption; "
            "write {{NOT IN RECORD}} for each caption field."
        )
    lines = []
    for k, v in caption.items():
        if isinstance(v, list):
            if v:
                lines.append(f"- {k}: " + "; ".join(x["value"] for x in v))
        elif k == "attorney":
            lines += [f"- attorney {a.replace('_', ' ')}: {x}" for a, x in v.items()]
        else:
            lines.append(f"- {k.replace('_', ' ')}: {v['value']}")
    return (
        f"THE CAPTION (verbatim from {doc_name}; use these exact values in the caption, "
        "never the matter record's):\n" + "\n".join(lines)
    )
