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
  letters, outstanding from a stated balance. A paid figure the record does
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
KINDS = ("bill", "eob", "lien", "ledger")
DOC_CHARS = 60_000
_STOP = {"medical", "center", "centers", "group", "inc", "llc", "the", "and", "of", "health", "care", "clinic", "md"}
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y")

PROMPT = """You read ONE billing document from a personal-injury file: a bill, an \
explanation of benefits (EOB), a lien letter, or a payment ledger. Output ONLY a JSON \
array, one object per line item or per stated total, each:
{"provider": "<provider name as printed>", "kind": "bill|eob|lien|ledger", \
"date_of_service": "<as printed, or null>", "billed": "<amount as printed, or null>", \
"paid": "<amount actually paid as printed, or null>", "outstanding": "<balance stated as \
still owed, as printed, or null>"}
Rules: copy every amount and date EXACTLY as printed; never compute, never infer; a \
figure the document does not print is null. "paid" is money actually paid (not an \
allowed amount, not an adjustment, not a write-off). Output [] when the document \
carries no billing figures."""


def is_billing(row: dict[str, Any], head: str) -> bool:
    return bool(BILLING_NAME.search(str(row.get("name") or ""))) or bool(BILLING_TEXT.search(head[:4000]))


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


def _parse_json_array(text: str) -> list[Any]:
    m = re.search(r"\[.*\]", text, flags=re.S)
    if not m:
        return []
    try:
        out = json.loads(m.group(0))
    except ValueError:
        return []
    return out if isinstance(out, list) else []


def extract(
    data: Path, doorway: Any, model: str, workers: int, log: Callable[[str], None]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Every billing document in the corpus into verified rows. Resumes from
    ``howell/<id>.json``. Returns (rows, notes)."""
    rows = [json.loads(ln) for ln in (data / "extracted.jsonl").read_text(encoding="utf-8").splitlines() if ln]
    out_dir = data / "howell"
    out_dir.mkdir(exist_ok=True)
    todo = []
    for r in rows:
        p = r.get("text_path")
        if not p or not Path(p).is_file():
            continue
        text = Path(p).read_text(encoding="utf-8", errors="replace")
        if is_billing(r, text):
            todo.append((r, text))

    def one(item: tuple[dict[str, Any], str]) -> tuple[list[dict[str, Any]], list[str]]:
        r, text = item
        doc = {"id": str(r.get("id")), "name": str(r.get("name"))}
        cache = out_dir / f"{doc['id']}.json"
        if cache.is_file():
            got = json.loads(cache.read_text(encoding="utf-8"))
            return got["rows"], got["notes"]
        raw: list[Any] = []
        for i in range(0, max(len(text), 1), DOC_CHARS):
            res = doorway.call(
                "howell",
                model=model,
                system=PROMPT,
                messages=[{"role": "user", "content": f"DOCUMENT: {doc['name']}\n\n{text[i : i + DOC_CHARS]}"}],
                max_tokens=16_000,
                stream=True,
                custom_id=f"howell-{doc['id'][:24]}-{i // DOC_CHARS}",
            )
            raw += _parse_json_array(res.text)
        kept, notes = verify(raw, text, doc)
        cache.write_text(json.dumps({"rows": kept, "notes": notes}, indent=1), encoding="utf-8")
        return kept, notes

    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(one, todo))
    log(f"  howell: {len(todo)} billing document(s) read")
    return [r for rs, _ in results for r in rs], [n for _, ns in results for n in ns]


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


def _sum_cell(items: list[tuple[Any, Decimal, dict[str, str]]]) -> Cell | None:
    """Sum figures, the same (date, amount) counted once across documents."""
    seen: set[tuple[Any, Decimal]] = set()
    total, docs = Decimal("0"), []
    for dos, amt, doc in items:
        if (dos, amt) in seen:
            docs.append(doc)
            continue
        seen.add((dos, amt))
        total += amt
        docs.append(doc)
    return Cell(fmt(total), _uniq(docs)) if items else None


def _billed(rows: list[dict[str, Any]], tab: list[dict[str, Any]]) -> Cell:
    for kinds in (("bill", "ledger"), ("eob",)):
        items = [
            (r.get("date_of_service"), money(r["billed"]), r["doc"])
            for r in rows
            if r["kind"] in kinds and r.get("billed")
        ]
        cell = _sum_cell([i for i in items if i[1] is not None])
        if cell:
            return cell
    items = [(c.get("start"), money(c.get("amount")), MEDICALS_TAB) for m in tab for c in m.get("charges") or []]
    return _sum_cell([i for i in items if i[1] is not None]) or Cell(NOT_IN_RECORD, [])


def _paid(rows: list[dict[str, Any]]) -> Cell:
    items = [
        (r.get("date_of_service"), money(r["paid"]), r["doc"])
        for r in rows
        if r["kind"] in ("eob", "ledger", "lien") and r.get("paid")
    ]
    return _sum_cell([i for i in items if i[1] is not None]) or Cell(NOT_IN_RECORD, [])


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


def build(rows: list[dict[str, Any]], medicals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per provider, ordered by first date of service then name."""
    groups: dict[str, dict[str, Any]] = {}
    for r in rows:
        g = groups.setdefault(provider_key(r["provider"]), {"provider": r["provider"], "rows": [], "tab": []})
        g["rows"].append(r)
    for m in medicals:
        name = str(m.get("provider") or "").strip()
        if name:
            g = groups.setdefault(provider_key(name), {"provider": name, "rows": [], "tab": []})
            g["tab"].append(m)
    table = []
    for key in groups:
        g = groups[key]
        table.append(
            {
                "provider": g["provider"],
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
