"""The Howell table: per provider, dates of service, billed, paid and
outstanding, every cell carrying the document it came from.

A mediation brief's damages section turns on what was PAID, not what was
billed (Howell v. Hamilton Meats). The table is computed here, in code, and the
compose prompt RECEIVES it; the model never adds a column. Two halves:

* ``extract``: each billing document (a bill, an EOB, a lien letter, a payment
  ledger) is read once by the digest model into rows of figures, and every
  figure is then checked against the document's own text: a figure or a date
  the document does not carry is dropped and noted, never kept.
* ``build``: deterministic. Per provider, billed from the bills (else the EOBs,
  else the matter's Medicals tab), paid from the EOBs, ledgers and lien
  letters, outstanding from a stated balance. Per source document a stated
  total is used when printed, else its line items, never both; across
  documents the same figures count once, different dates add, and a
  disagreement is the attorney's (``_combine``). A paid figure the record does
  not carry is ``{{NOT IN RECORD}}``: it is never derived from billed, and an
  outstanding balance is never billed minus paid (a write-off is not a
  payment).
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

NOT_IN_RECORD = "{{NOT IN RECORD}}"
MEDICALS_TAB = {"id": "matter-record", "name": "Medicals tab (matter record)"}
BILLING_NAME = re.compile(
    r"(?i)\b(bills?|billing|itemi[sz]ed|statements?|ledgers?|eobs?|explanation of benefits|liens?|payments?|"
    r"invoices?|ub-?04|hcfa|cms-?1500|balance)\b"
)
BILLING_TEXT = re.compile(r"(?i)(explanation of benefits|amount paid|total charges|balance due|lien)")
#: Signals that a document is itself a bill or ledger, not a letter or record
#: that mentions one (a 2026-10-07 large-matter dry run read 438 documents on the
#: phrase test alone: demand letters, depositions and records that say "lien").
_AMOUNT = re.compile(r"(?<![\d.])\$?\s?\d{1,3}(?:,\d{3})*\.\d{2}\b")
_TABLE = re.compile(
    r"(?i)\b(cpt|hcpcs|date of service|dates of service|\bdos\b|charges?\s+(payments?|adj)|"
    r"adjustments?|amount billed|billed amount|patient responsibility|allowed amount)\b"
)
_LIEN = re.compile(r"(?i)\b(lien|balance due|amount owed|outstanding balance)\b")
SHORT_DOC_CHARS = 15_000
_STRONG = re.compile(r"(?i)explanation of benefits|itemi[sz]ed statement|amount paid|total charges|patient balance")
#: Documents that state money but are not a provider's charges: the case's own
#: settlement papers, releases, retainers, funding agreements, court forms.
_NOT_A_BILL = re.compile(
    r"(?i)\b(releases?|settlement|set\.? ?smt|smt|retainer|loan|bill of sale|dismissal|summons|"
    r"agreement|stipulation|demand|deposition|complaint)\b"
)
KINDS = ("bill", "eob", "lien", "ledger")
DOC_CHARS = 60_000
_STOP = {"medical", "center", "centers", "group", "inc", "llc", "the", "and", "of", "health", "care", "clinic", "md"}
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y")

PROMPT = """You read ONE billing document from a personal-injury file: a bill, an \
explanation of benefits (EOB), a lien letter, or a payment ledger. Output ONLY a JSON \
array, one object per line item and one per stated total, each:
{"provider": "<provider name as printed>", "kind": "bill|eob|lien|ledger", \
"row_type": "line_item|stated_total", "payer": "<who paid, as printed, or null>", \
"date_of_service": "<as printed, or null>", "billed": "<amount as printed, or null>", \
"paid": "<amount actually paid as printed, or null>", "outstanding": "<balance stated as \
still owed, as printed, or null>"}
Rules: copy every amount and date EXACTLY as printed; never compute, never infer; a \
figure the document does not print is null. A line item is one charge or one payment; \
a stated total is a total or balance the document prints for several of them, and is \
its own row with row_type "stated_total", never folded into a line item. "paid" is \
money actually paid (not an allowed amount, not an adjustment, not a write-off). \
Output [] when the document carries no billing figures."""


class ExtractionError(RuntimeError):
    """A billing document's extraction did not finish or could not be read:
    our machinery (failed, resumable), never an empty table."""


def is_billing(row: dict[str, Any], head: str) -> bool:
    """A billing document: named as one, or reading as one. A text-only
    candidate needs a ledger's shape (five or more cents-precise amounts and a
    column or code signal) or a lien/balance statement carrying an amount; a
    letter that only mentions a lien is not read for figures."""
    name = str(row.get("name") or "")
    if BILLING_NAME.search(name):
        return True  # "Medical Lien Agreement" is a lien before it is an agreement
    if _NOT_A_BILL.search(name):
        return False
    if BILLING_NAME.search(str(row.get("folder") or "")):
        return True
    h = head[:4000]
    amounts = len(_AMOUNT.findall(h))
    if amounts >= 5 and _TABLE.search(h):
        return True
    if amounts >= 2 and _STRONG.search(h):
        return True  # an EOB or itemized statement with few lines
    # A lien or balance letter is short; a demand or deposition that mentions
    # a lien is not (SHORT_DOC_CHARS of extracted text).
    short = int(row.get("chars") or len(head)) <= SHORT_DOC_CHARS
    return amounts >= 1 and short and bool(_LIEN.search(h))


def money(raw: Any) -> Decimal | None:
    s = re.sub(r"[,$\s]", "", str(raw or ""))
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return Decimal(s) if re.fullmatch(r"-?\d+(\.\d{1,2})?", s) else None
    except InvalidOperation:
        return None


def fmt(v: Decimal) -> str:
    return f"${v:,.2f}"


def parse_date(raw: Any) -> date | None:
    s = str(raw or "").strip()
    for f in _DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


def provider_key(name: str) -> str:
    toks = [t for t in re.findall(r"[a-z0-9]+", name.lower()) if t not in _STOP]
    return " ".join(toks)


def _in_text(raw: Any, text: str) -> bool:
    """The figure as printed is in the document: digits and separators only."""
    s = str(raw or "").strip().lstrip("$").strip()
    if not s:
        return False
    flat = re.sub(r"\s+", " ", text)
    return s in flat or s.replace(",", "") in flat.replace(",", "")


def verify(rows: list[Any], text: str, doc: dict[str, str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep only what the document prints. Returns the kept rows (tagged with
    the document) and a note for every dropped figure."""
    kept, notes = [], []
    for r in rows:
        if not isinstance(r, dict) or not str(r.get("provider") or "").strip():
            continue
        row: dict[str, Any] = {
            "provider": str(r["provider"]).strip(),
            "kind": r.get("kind") if r.get("kind") in KINDS else "bill",
            "row_type": "stated_total" if r.get("row_type") == "stated_total" else "line_item",
            "payer": str(r.get("payer") or "").strip() or None,
            "doc": doc,
        }
        dos = r.get("date_of_service")
        row["date_of_service"] = dos if dos and _in_text(dos, text) and parse_date(dos) else None
        for k in ("billed", "paid", "outstanding"):
            raw = r.get(k)
            if raw in (None, ""):
                row[k] = None
            elif money(raw) is not None and _in_text(raw, text):
                row[k] = str(raw).strip()
            else:
                row[k] = None
                notes.append(f"{doc['name']}: a {k} figure ({raw}) is not printed in the document; dropped")
        kept.append(row)
    return kept, notes


RETRY = "Your answer was not a JSON array. Answer again with ONLY the JSON array described, and nothing else; [] when this part of the document carries no billing figures."


def _parse_json_array(text: str) -> list[Any] | None:
    """The first well-formed JSON array in the answer; None when there is none.
    Decodes from each '[' in turn, so prose or bracketed words around the
    array cannot corrupt it (a greedy first-'[' to last-']' match did)."""
    dec = json.JSONDecoder()
    for m in re.finditer(r"\[", text):
        try:
            out, _ = dec.raw_decode(text, m.start())
        except ValueError:
            if re.match(r"\[\s*\{", text[m.start() :]):
                return None  # an array of objects that does not close: truncated, never its inner part
            continue
        if isinstance(out, list) and all(isinstance(x, dict) for x in out):
            return out
    return None


def extract(
    data: Path, doorway: Any, model: str, workers: int, log: Callable[[str], None]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Every billing document in the corpus into verified rows, one call per
    document piece at the model's output maximum. Resumes from
    ``howell/<id>.json``; a piece that stops at the ceiling or answers with
    something that is not a JSON array raises ``ExtractionError`` and nothing
    is cached for that document, so a resume reads it again rather than
    trusting an empty answer."""
    from ..demand.gapaudit import output_max

    rows = [json.loads(ln) for ln in (data / "extracted.jsonl").read_text(encoding="utf-8").splitlines() if ln]
    out_dir = data / "howell"
    out_dir.mkdir(exist_ok=True)
    todo = []
    skipped: list[str] = []
    for r in rows:
        p = r.get("text_path")
        if not p or not Path(p).is_file():
            continue
        text = Path(p).read_text(encoding="utf-8", errors="replace")
        if is_billing(r, text):
            todo.append((r, text))
        elif _NOT_A_BILL.search(str(r.get("name") or "")) and _AMOUNT.search(text[:4000]):
            skipped.append(str(r.get("name")))
    max_tokens = output_max(model, 64_000)

    def one(item: tuple[dict[str, Any], str]) -> tuple[list[dict[str, Any]], list[str]]:
        r, text = item
        doc = {"id": str(r.get("id")), "name": str(r.get("name"))}
        cache = out_dir / f"{doc['id']}.json"
        if cache.is_file():
            got = json.loads(cache.read_text(encoding="utf-8"))
            return got["rows"], got["notes"]
        raw: list[Any] = []
        for i in range(0, max(len(text), 1), DOC_CHARS):
            piece = i // DOC_CHARS
            messages: list[dict[str, Any]] = [
                {"role": "user", "content": f"DOCUMENT: {doc['name']}\n\n{text[i : i + DOC_CHARS]}"}
            ]
            got = None
            for attempt in range(2):  # one corrective retry, then the read fails
                res = doorway.call(
                    "howell",
                    model=model,
                    system=PROMPT,
                    messages=messages,
                    max_tokens=max_tokens,
                    stream=True,
                    custom_id=f"howell-{doc['id'][:24]}-{piece}-{attempt}",
                )
                if res.stop_reason == "max_tokens":
                    raise ExtractionError(f"the billing read of {doc['name']} stopped at its output ceiling")
                got = _parse_json_array(res.text)
                if got is not None:
                    break
                # Keep the unparseable answer: a failure nobody can read cannot be diagnosed.
                (out_dir / f".{doc['id']}.{piece}.{attempt}.unparsed.txt").write_text(res.text or "", encoding="utf-8")
                messages = [
                    *messages,
                    {"role": "assistant", "content": res.text or "(no answer)"},
                    {"role": "user", "content": RETRY},
                ]
            if got is None:
                raise ExtractionError(f"the billing read of {doc['name']} did not answer with a JSON array")
            raw += got
        kept, notes = verify(raw, text, doc)
        tmp = out_dir / f".{doc['id']}.json.tmp"
        tmp.write_text(json.dumps({"rows": kept, "notes": notes}, indent=1), encoding="utf-8")
        tmp.replace(cache)
        return kept, notes

    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(one, todo))
    log(f"  howell: {len(todo)} billing document(s) read")
    notes = [n for _, ns in results for n in ns]
    notes += [f"not read for billing (settlement, release, agreement or similar by name): {s}" for s in skipped]
    return [r for rs, _ in results for r in rs], notes


# ---- the deterministic table ---------------------------------------------------------


@dataclass
class Cell:
    value: str
    sources: list[dict[str, str]]

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "sources": self.sources}


def _uniq(docs: list[dict[str, str]]) -> list[dict[str, str]]:
    seen, out = set(), []
    for d in docs:
        if d["id"] not in seen:
            seen.add(d["id"])
            out.append(d)
    return out


Record = tuple[Any, Decimal]  # (date of service or None, amount)


def _combine(per_doc: dict[str, tuple[dict[str, str], list[Record]]], what: str) -> Cell | None:
    """One figure from several documents' records, never double counted.

    Within a document every record counts (two real same-day charges are two
    charges). Across documents: one document is its own sum; documents whose
    records are the same set are one account of the same figures and count
    once; documents whose records are all dated and on different dates are
    different services and add. Anything else (an undated figure beside
    another document's, records that overlap without matching) is a
    disagreement the attorney settles: the marker names each document's
    figure."""
    docs = [(doc, recs) for doc, recs in per_doc.values() if recs]
    if not docs:
        return None
    sums = [(doc, sum((a for _d, a in recs), Decimal("0")), recs) for doc, recs in docs]
    srcs = _uniq([doc for doc, _ in docs])
    if len(sums) == 1:
        return Cell(fmt(sums[0][1]), srcs)
    sets = [sorted((str(d), a) for d, a in recs) for _doc, _s, recs in sums]
    if all(s == sets[0] for s in sets):
        return Cell(fmt(sums[0][1]), srcs)
    dated = all(d is not None for _doc, _s, recs in sums for d, _a in recs)
    date_sets = [{d for d, _a in recs} for _doc, _s, recs in sums]
    disjoint = dated and all(
        not (date_sets[i] & date_sets[j]) for i in range(len(date_sets)) for j in range(i + 1, len(date_sets))
    )
    if disjoint:
        return Cell(fmt(sum((s for _doc, s, _r in sums), Decimal("0"))), srcs)
    each = " vs ".join(f"{fmt(s)} ({doc['name']})" for doc, s, _r in sums)
    return Cell(f"{{{{ATTORNEY: confirm {what} amount, sources disagree: {each}}}}}", srcs)


def _per_doc(
    rows: list[dict[str, Any]], field_: str, kinds: tuple[str, ...]
) -> dict[str, tuple[dict[str, str], list[Record]]]:
    """Per source document, its records of ``field_``: the document's stated
    total when it prints one, else its line items, never both."""
    out: dict[str, tuple[dict[str, str], list[Record]]] = {}
    for r in rows:
        if r["kind"] not in kinds or not r.get(field_):
            continue
        amt = money(r[field_])
        if amt is None:
            continue
        doc = r["doc"]
        entry = out.setdefault(doc["id"], (doc, []))
        entry[1].append((r.get("date_of_service"), amt, r["row_type"]))  # type: ignore[arg-type]
    final: dict[str, tuple[dict[str, str], list[Record]]] = {}
    for key, (doc, recs) in out.items():
        totals = [(d, a) for d, a, t in recs if t == "stated_total"]  # type: ignore[misc]
        items = [(d, a) for d, a, t in recs if t != "stated_total"]  # type: ignore[misc]
        if totals:
            distinct = {a for _d, a in totals}
            final[key] = (doc, [(None, max(distinct))] if len(distinct) > 1 else [(None, totals[0][1])])
        else:
            final[key] = (doc, items)
    return final


def _billed(rows: list[dict[str, Any]], tab: list[dict[str, Any]]) -> Cell:
    for kinds in (("bill", "ledger"), ("eob",)):
        cell = _combine(_per_doc(rows, "billed", kinds), "billed")
        if cell:
            return cell
    recs = [(c.get("start"), money(c.get("amount"))) for m in tab for c in m.get("charges") or []]
    recs = [(d, a) for d, a in recs if a is not None]
    if recs:
        return Cell(fmt(sum((a for _d, a in recs), Decimal("0"))), [MEDICALS_TAB])  # type: ignore[misc]
    return Cell(NOT_IN_RECORD, [])


def _paid(rows: list[dict[str, Any]]) -> Cell:
    return _combine(_per_doc(rows, "paid", ("eob", "ledger", "lien", "bill")), "paid") or Cell(NOT_IN_RECORD, [])


def _outstanding(rows: list[dict[str, Any]]) -> Cell:
    stated = [(money(r["outstanding"]), r["doc"]) for r in rows if r.get("outstanding")]
    stated = [(v, d) for v, d in stated if v is not None]
    if not stated:
        return Cell(NOT_IN_RECORD, [])
    values = sorted({v for v, _ in stated})
    if len(values) == 1:
        return Cell(fmt(values[0]), _uniq([d for _, d in stated]))
    each = "; ".join(f"{fmt(v)} ({d['name']})" for v, d in stated)
    return Cell(f"{{{{ATTORNEY: the record states different balances: {each}}}}}", _uniq([d for _, d in stated]))


def _dates(rows: list[dict[str, Any]], tab: list[dict[str, Any]]) -> Cell:
    pts = [(parse_date(r.get("date_of_service")), r["doc"]) for r in rows]
    pts += [(parse_date(c.get("start")), MEDICALS_TAB) for m in tab for c in m.get("charges") or []]
    pts = [(d, doc) for d, doc in pts if d is not None]
    if not pts:
        return Cell(NOT_IN_RECORD, [])
    lo, hi = min(d for d, _ in pts), max(d for d, _ in pts)
    span = lo.strftime("%m/%d/%Y") if lo == hi else f"{lo.strftime('%m/%d/%Y')} to {hi.strftime('%m/%d/%Y')}"
    return Cell(span, _uniq([doc for _, doc in pts]))


#: Words that name a kind of provider, not which one ("Northgate Orthopedic
#: Consultants" and "Westfield Orthopedic Consultants" are different providers).
_GENERIC = {
    "orthopedic",
    "orthopaedic",
    "orthopedics",
    "consultants",
    "therapy",
    "physical",
    "rehab",
    "surgery",
    "surgical",
    "center",
    "massage",
    "therapist",
    "office",
    "imaging",
    "radiology",
    "medical",
    "associates",
    "services",
    "foundation",
    "hospital",
    "health",
    "doctor",
    "spine",
    "treatment",
    "interventional",
    "chiropractic",
    "clinic",
    "group",
    "open",
    "reduced",
}


#: A billing row naming a treating provider, and one naming a vendor or payer.
_MEDICAL = re.compile(
    r"(?i)\b(m\.?d\.?|d\.?o\.?|d\.?c\.?|p\.?t\.?|dpt|clinic|medical|hospital|imaging|radiology|mri|surgery|"
    r"surgical|orthop\w*|chiropractic|therapy|pain|anesthesia|physicians?)\b"
)
#: Applied only to a row that ALSO reads as medical: the strong signs of a
#: litigation vendor or payer ("Valley Pain Associates, LLC" is a treater).
_VENDOR = re.compile(
    r"(?i)\b(legal|litigation|law office|attorney|funding|records? retrieval|mediation|nurse consult\w*)\b"
)
OFF_TAB = "{{ATTORNEY: this provider is not on the matter's Medicals tab; confirm it is the client's treatment}}"


def _distinct(name: str) -> list[str]:
    """The name's identifying words: five letters or more, not a kind of
    provider, with any parenthetical or "/staff" suffix removed."""
    base = re.sub(r"\(.*?\)|/.*$", " ", name)
    return [t for t in re.findall(r"[a-z]+", base.lower()) if len(t) >= 5 and t not in _GENERIC and t not in _STOP]


def match_provider(name: str, spine: list[str]) -> str | None:
    """The Medicals-tab provider a billing row's provider is, or None. A word
    matches at 0.85 similarity, so "Halverson"/"Halvorsen" and "Jaek"/"Jack"
    are one provider; a tie between two tab providers matches neither."""
    from difflib import SequenceMatcher

    spine = list(dict.fromkeys(spine))  # a provider listed twice on the tab is one provider
    mine = _distinct(name)
    if not mine:  # a short or generic name: the whole-name key must match
        hits = [c for c in spine if provider_key(c) == provider_key(name)]
        return hits[0] if len(hits) == 1 else None
    best, score, tie = None, 0, False
    for cand in spine:
        theirs = _distinct(cand) or [t for t in provider_key(cand).split() if len(t) >= 3]
        s = sum(1 for a in mine if any(SequenceMatcher(None, a, b).ratio() >= 0.85 for b in theirs))
        if s > score:
            best, score, tie = cand, s, False
        elif s == score and s > 0:
            tie = True
    return None if tie or score == 0 else best


def build(
    rows: list[dict[str, Any]], medicals: list[dict[str, Any]], unmatched: list[str] | None = None
) -> list[dict[str, Any]]:
    """One row per provider, ordered by first date of service then name.

    When the matter's Medicals tab lists providers, the tab is the spine: a
    billing row joins the tab provider it names, and a row naming none of them
    (a litigation vendor, the firm's own invoice, a funding company) is left
    out of the table and appended to ``unmatched`` for the attorney notes."""
    groups: dict[str, dict[str, Any]] = {}
    spine = [str(m.get("provider") or "").strip() for m in medicals if str(m.get("provider") or "").strip()]
    for m in medicals:
        name = str(m.get("provider") or "").strip()
        if name:
            g = groups.setdefault(provider_key(name), {"provider": name, "rows": [], "tab": []})
            g["tab"].append(m)
    off_tab: set[str] = set()
    for r in rows:
        if spine:
            hit = match_provider(r["provider"], spine)
            if hit is None and _MEDICAL.search(r["provider"]) and not _VENDOR.search(r["provider"]):
                # A treating provider the tab does not list: its own row, marked,
                # never silently dropped (the tab is not always complete).
                key = "offtab:" + provider_key(re.sub(r"\(.*?\)|/.*$", "", r["provider"]))
                off_tab.add(key)
            elif hit is None:
                if unmatched is not None:
                    unmatched.append(f"{r['provider']} ({r['doc']['name']})")
                continue
            else:
                key = provider_key(hit)
        else:
            key = provider_key(r["provider"])
        g = groups.setdefault(key, {"provider": r["provider"], "rows": [], "tab": []})
        g["rows"].append(r)
    table = []
    for key in groups:
        g = groups[key]
        table.append(
            {
                "provider": g["provider"] + (f" {OFF_TAB}" if key in off_tab else ""),
                "dates_of_service": _dates(g["rows"], g["tab"]).as_dict(),
                "billed": _billed(g["rows"], g["tab"]).as_dict(),
                "paid": _paid(g["rows"]).as_dict(),
                "outstanding": _outstanding(g["rows"]).as_dict(),
            }
        )

    def order(t: dict[str, Any]) -> tuple[str, str]:
        first = str(t["dates_of_service"]["value"]).split(" to ")[0]
        d = parse_date(first)
        return (d.isoformat() if d else "9999", t["provider"].lower())

    return sorted(table, key=order)


def totals(table: list[dict[str, Any]]) -> dict[str, str]:
    """Billed and paid totals; a total over a column with any missing figure
    is the marker, never a partial sum presented as the whole."""
    out = {}
    for col in ("billed", "paid", "outstanding"):
        vals = [money(t[col]["value"]) for t in table]
        out[col] = fmt(sum(vals, Decimal("0"))) if vals and all(v is not None for v in vals) else NOT_IN_RECORD  # type: ignore[arg-type]
    return out


def _cell_md(c: dict[str, Any]) -> str:
    src = "; ".join(s["name"] for s in c["sources"])
    v = str(c["value"]).replace("|", "/")
    return f"{v} (source: {src.replace('|', '/')})" if src else v


def markdown(table: list[dict[str, Any]]) -> str:
    """The table as the compose prompt receives it: every cell with its source."""
    lines = [
        "| Provider | Dates of service | Billed | Paid (Howell) | Outstanding |",
        "|---|---|---|---|---|",
    ]
    for t in table:
        cells = [_cell_md(t[k]) for k in ("dates_of_service", "billed", "paid", "outstanding")]
        lines.append(f"| {t['provider'].replace('|', '/')} | " + " | ".join(cells) + " |")
    tot = totals(table)
    lines.append(f"| Total | | {tot['billed']} | {tot['paid']} | {tot['outstanding']} |")
    return "\n".join(lines)
