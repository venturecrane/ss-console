"""`billing_chart`: the per-provider billing chart on the CHRONOLOGY's
provider list. $0.

A billing label is not a provider (a first version built 18 rows from
whatever name appeared on a bill and a grand total 3x the firm's own lien
report). The chronology's Treatment Timeline is the spine; billing documents
are matched onto it through the firm's `billing.provider_match` table, a
head-substring fallback, and then by rule through the facility the provider
cell names (`billing_match.py`, stop audit row 28: a clinician-named spine
with no authored table once matched nothing and rendered a $0.00 worksheet).
One clinician listed twice is one row. A label that still matches nothing is
its own row, titled as printed on the bill and marked so, in the total, and
still REPORTED under "unmatched"; nothing is dropped. Per provider the chart prefers the provider's own account-wide
printed total (a LEDGER's) over a sum of claim forms, which double-counts.
When no ledger exists the total is NOT DERIVABLE and says so: inventing one
is exactly the fabricated figure this pipeline exists to prevent. Money is
parsed, never computed by the model. An amount at or above
`billing.suspect_amount_cents` may be a lost decimal, and it is settled by a
rule rather than by a person (stop audit row 18, 2026-10-24): when the cited
page's own text prints that figure WITH its decimal point it is accepted (a
spinal fusion can legitimately bill past the threshold); otherwise the row is
left out of every total and recorded in `billing_dropped_rows.json` as
{file, page, amount_text, reason}, which the worksheet and the Limitations
section name. No figure is ever guessed in its place. On a joint matter the
patient filter quarantines the other plaintiff's bills.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from .. import billing_paid as BP
from . import billing_match as BM
from .base import StageRun, read_json, read_jsonl

NON_PROVIDER = {"VENDOR_INVOICE", "CERTIFICATE", "RECORDS_ONLY", "OTHER"}
ADJ_LABEL = re.compile(r"adjust|write.?off|contractual|disallow", re.I)
MULTI = re.compile(r"multiple providers|see line items|;|,\s*\w+\s*\(PAR\)", re.I)
LEDGER_TOTAL = [
    (re.compile(r"overall\s*-\s*total|grand\s*total", re.I), 3),
    (re.compile(r"^overall$|total\s*charges?$|account\s*total|total\s*billed", re.I), 2),
    (re.compile(r"^charge$|balance|amount\s*due|self.pay", re.I), 1),
]
NO_LABEL = "(no provider printed on the bill)"
#: A spine provider with no bill of any kind in the file (the worksheet's What
#: Is Needed says exactly that for these rows, never "claim forms").
NO_BILL = "no bill located"
FORM_BOX = re.compile(r"^\s*\d{1,2}\s*[.)]")
SPLIT_COLUMN = re.compile(r"^(\d{1,3}(?:,\d{3})*|\d+)(?:\s+|\s*[Il|!¦]\s*)(\d{2})$")


def total_rank(label: str | None) -> int:
    """How account-wide a printed total is; 0 = a single claim's total (a
    numbered CMS-1500 box is one claim form's total, never an account)."""
    lab = (label or "").strip()
    if FORM_BOX.match(lab):
        return 0
    return next((rank for pat, rank in LEDGER_TOTAL if pat.search(lab)), 0)


def money(s: Any, suspect: list[tuple[str, float]], suspect_at: float) -> float | None:
    if not s:
        return None
    t = str(s).replace("$", "").strip()
    if "?" in t:
        return None
    m = re.match(r"^(\d{1,3}(?:,\d{3})*),(\d{2})$", t)  # a decimal comma
    if m:
        t = m.group(1).replace(",", "") + "." + m.group(2)
    else:
        m2 = SPLIT_COLUMN.match(t)  # dollars and cents in adjacent boxes
        t = (m2.group(1).replace(",", "") + "." + m2.group(2)) if m2 else t.replace(",", "")
    try:
        v = round(float(t), 2)
    except ValueError:
        return None
    if v >= suspect_at:
        suspect.append((str(s), v))
    return v


PRINTED_DECIMAL = re.compile(r"(?<![\d,.])(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2})(?!\d)")


class PageText:
    """The text layer of billing pages, read once per document, for the one
    question a suspect amount raises: does the page print this figure with
    its decimal point?"""

    def __init__(self, slug_dir: Any) -> None:
        spec = read_json(slug_dir / "billing_docs.json", []) or []
        docs = spec.get("docs") if isinstance(spec, dict) else spec
        self.path = {str(t.get("name")): str(t.get("path")) for t in docs or [] if isinstance(t, dict)}
        self._pages: dict[str, list[str]] = {}

    def text(self, name: str, page: int) -> str:
        if name not in self._pages:
            pages: list[str] = []
            if name in self.path:
                import pymupdf

                try:
                    with pymupdf.open(self.path[name]) as doc:
                        pages = [pg.get_text() for pg in doc]
                except (OSError, RuntimeError, ValueError):
                    pages = []
            self._pages[name] = pages
        pages = self._pages[name]
        return pages[page - 1] if 1 <= page <= len(pages) else ""

    def prints(self, name: str, page: int, value: float) -> tuple[bool, str]:
        """(confirmed, why not)."""
        if page < 1:
            return False, "the transcription named no page to confirm the figure against"
        txt = self.text(name, page)
        if not txt.strip():
            return False, "the page has no text layer to confirm the figure against"
        for m in PRINTED_DECIMAL.finditer(txt):
            if round(float(m.group(1).replace(",", "") + "." + m.group(2)), 2) == round(value, 2):
                return True, ""
        return False, "the page's text does not print that figure with its decimal point (likely a lost decimal)"


def _page_no(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def dt(s: Any) -> str | None:
    m = re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", str(s or ""))
    if not m:
        return None
    mo, dy, yr = (int(x) for x in m.groups())
    yr += 2000 if yr < 100 else 0
    return f"{yr:04d}-{mo:02d}-{dy:02d}" if 1 <= mo <= 12 and 1 <= dy <= 31 else None


def quarantined(chunk: dict[str, Any], patient: str | None) -> bool:
    """On a joint matter, a chunk naming another patient is that plaintiff's
    bill. A blank patient field is kept (it cannot be attributed either way)."""
    who = chunk.get("patient") or ""
    return bool(patient and who and patient not in who.lower())


class Matcher:
    def __init__(self, cfg: Any, slug_dir) -> None:
        self.match: dict[str, list[str]] = {k: list(v) for k, v in (cfg.get("billing", "provider_match") or {}).items()}
        self.match.update(read_json(slug_dir / "provider_match.json", {}) or {})

    @staticmethod
    def timeline(text: str) -> list[dict[str, Any]]:
        sec = text.split("## Treatment Timeline", 1)[1].split("\n## ", 1)[0]
        out = []
        for line in sec.splitlines():
            if not line.strip().startswith("|") or "Medical Provider" in line:
                continue
            c = [x.strip() for x in line.strip("|").split("|")]
            if len(c) < 2:
                continue
            ds = re.findall(r"\d{2}/\d{2}/\d{4}", c[1])
            out.append({"provider": c[0], "first": dt(ds[0]) if ds else None, "last": dt(ds[-1]) if ds else None})
        return out

    def collapse(self, spine: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge spine rows no billing label can tell apart (five hospital
        departments the records distinguish and the bills call by one name)."""
        groups: dict[tuple, list] = {}
        for row in spine:
            groups.setdefault(tuple(sorted(self.match.get(row["provider"], [row["provider"]]))), []).append(row)
        out = []
        for rows_ in groups.values():
            if len(rows_) == 1:
                out.append(rows_[0])
                continue
            firsts = [r["first"] for r in rows_ if r["first"]]
            lasts = [r["last"] for r in rows_ if r["last"]]
            head = re.split(r"[,(\-]", rows_[0]["provider"])[0].strip()
            merged = {
                "provider": f"{head} (all departments)",
                "first": min(firsts) if firsts else None,
                "last": max(lasts) if lasts else None,
                "merged_from": [r["provider"] for r in rows_],
            }
            self.match[merged["provider"]] = self.match.get(rows_[0]["provider"], [])
            out.append(merged)
        return sorted(out, key=lambda r: r["first"] or "9999")

    def authored(self, label: str | None, spine: list[dict[str, Any]]) -> str | None:
        """The firm's provider_match table, then a head-substring of the cell."""
        lab = (label or "").lower()
        for row in spine:
            if any(re.search(pat, lab, re.I) for pat in self.match.get(row["provider"], [])):
                return row["provider"]
        for row in spine:
            head = re.split(r"[,(\-]", row["provider"])[0].strip().lower()
            if len(head) > 5 and head in lab:
                return row["provider"]
        return None

    def provider(self, label: str | None, spine: list[dict[str, Any]]) -> str | None:
        """Authored first, then the facility rule (billing_match, row 28)."""
        got = self.authored(label, spine)
        if got or not (label or "").strip():
            return got
        if BM.shared_by(str(label), spine):
            return None  # names several providers and is too broad to merge them: as printed
        hit = BM.hits(str(label), spine)
        return hit[0]["provider"] if hit else None

    def spine(self, text: str, labels: list[str]) -> list[dict[str, Any]]:
        rows = BM.collapse_clinicians(self.collapse(self.timeline(text)))
        return BM.merge_by_labels(rows, labels, lambda lab: self.authored(lab, rows))


def _labels(records: list[dict[str, Any]], patient: str | None) -> list[str]:
    """Every provider label printed on a provider bill of this unit."""
    return sorted(
        {
            str(c.get("provider") or "").strip()
            for r in records
            for c in r.get("chunks") or []
            if isinstance(c, dict)
            and c.get("doc_type") not in NON_PROVIDER | {"LIEN_SUBROGATION", None}
            and not quarantined(c, patient)
        }
    )


def _new_entry() -> dict[str, Any]:
    return {"totals": [], "docs": set(), "bdates": [], "adjustments": [], "printed": []}


class _Tally:
    """One pass over billing_extract.jsonl: every printed total placed on a
    row (or the report), nothing computed but the placement."""

    def __init__(self, sr: StageRun, matcher: Matcher, spine: list[dict[str, Any]], patient: str | None) -> None:
        self.sr, self.matcher, self.spine, self.patient = sr, matcher, spine, patient
        self.suspect_at = int(sr.cfg.get("billing", "suspect_amount_cents") or 50_000_000) / 100.0
        self.pages = PageText(sr.slug_dir)
        self.bills: dict[str, dict[str, Any]] = defaultdict(_new_entry)
        self.unmatched: dict[str, dict[str, Any]] = defaultdict(_new_entry)
        self.dropped: list[dict[str, Any]] = []
        self.dropped_for: dict[str, int] = {}
        self.vendor: list = []
        self.subro: list = []
        self.quarantine: list = []
        self.failed: list = []
        self.inherited: list = []
        #: Spine provider -> the broad labels (one distinctive word, several
        #: matching providers) whose bills may cover it (N5).
        self.shared: dict[str, set[str]] = {}

    def record(self, r: dict[str, Any]) -> None:
        carry = None
        for c in r.get("chunks") or []:
            if "FAILED_PAGE" in c:
                self.failed.append((r["file"], c["FAILED_PAGE"]))
            elif isinstance(c, dict) and "doc_type" in c and not self._set_aside(r, c):
                carry = self._chunk(r, c, carry)

    def _set_aside(self, r: dict[str, Any], c: dict[str, Any]) -> bool:
        kind = c.get("doc_type")
        if quarantined(c, self.patient):
            self.quarantine.append((r["file"], c.get("patient") or "", c.get("provider")))
        elif kind == "VENDOR_INVOICE":
            self.vendor.append((r["file"], c.get("provider")))
        elif kind == "LIEN_SUBROGATION":
            amts = [t.get("amount") for t in c.get("printed_totals") or []][:3]
            self.subro.append((r["file"], c.get("provider"), amts))
        else:
            return kind in NON_PROVIDER
        return True

    def _chunk(self, r: dict[str, Any], c: dict[str, Any], carry: str | None) -> str | None:
        raw_provider = (c.get("provider") or "").strip()
        tgt = self.matcher.provider(c.get("provider"), self.spine)
        direct = bool(tgt) and not MULTI.search(raw_provider)
        if tgt:
            carry = tgt
        elif not raw_provider and carry:
            # A continuation chunk carries no provider header; the name is
            # printed once on page one. Inherit ONLY when the field is blank,
            # never when a name simply failed to match.
            tgt = carry
            self.inherited.append((r["file"], c.get("page_first"), carry))
        if not tgt and raw_provider:
            for prov in BM.shared_by(raw_provider, self.spine):
                self.shared.setdefault(prov, set()).add(raw_provider)
        key = tgt or raw_provider or NO_LABEL
        e = self.bills[tgt] if tgt else self.unmatched[key]
        e["docs"].add(r["file"])
        for t in c.get("printed_totals") or []:
            a = self._amount(r, t, key)
            e["printed"].append(
                {
                    "label": t.get("label"),
                    "amount_text": t.get("amount"),
                    "amount": a,
                    "file": r["file"],
                    "page": t.get("page"),
                }
            )
            if a is None:
                continue
            e["totals"].append((a, t.get("label"), r["file"], t.get("page"), c.get("doc_type")))
            lab = str(t.get("label") or "")
            if tgt and direct and ADJ_LABEL.search(lab) and self.matcher.provider(lab, self.spine) in (None, tgt):
                e["adjustments"].append((abs(a), lab, r["file"], t.get("page")))
        e["bdates"].extend(v for k in ("date_first", "date_last") if (v := dt(c.get(k))))
        return carry

    def _amount(self, r: dict[str, Any], t: dict[str, Any], key: str) -> float | None:
        """The printed figure, or None: unreadable, or over the suspect line
        and not printed on the page with its decimal (row 18: dropped and
        recorded, never guessed)."""
        flagged: list[tuple[str, float]] = []
        a = money(t.get("amount"), flagged, self.suspect_at)
        if a is None or not flagged:
            return a
        page = _page_no(t.get("page"))
        ok, why = self.pages.prints(str(r["file"]), page, a)
        if ok:
            self.sr.log(f"  {t.get('amount')} on {str(r['file'])[:40]} p.{page}: printed on the page, accepted")
            return a
        self.dropped.append(
            {
                "file": str(r["file"]),
                "page": page,
                "amount_text": str(t.get("amount")),
                "reason": f"at or above the ${self.suspect_at:,.2f} check, and {why}; left out of every total",
            }
        )
        self.dropped_for[key] = self.dropped_for.get(key, 0) + 1
        return None

    def own_rows(self, lien: dict[str, float]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """(b) of row 28, as revised: a label matching no chronology provider
        is its own row, as printed on the bill, by the same ledger-over-claims
        rule, but NOT in the grand total (a first version of this worksheet
        once totalled 3x the firm's lien report). A document that already
        supplied a matched row keeps its amounts there only: same paper,
        counted once. A label the lien report carries is the lien's row."""
        matched_docs = set().union(*(e["docs"] for e in self.bills.values())) if self.bills else set()
        out = []
        for k, v in sorted(self.unmatched.items()):
            if k in lien:
                continue
            e = {**v, "totals": [t for t in v["totals"] if t[2] not in matched_docs]}
            e["printed"] = [p for p in v["printed"] if p["file"] not in matched_docs]
            e["docs"] = {f for f in v["docs"] if f not in matched_docs}
            if not e["docs"]:
                continue
            row = {"provider": k, "first": min(v["bdates"], default=None), "last": max(v["bdates"], default=None)}
            out.append(({**row, "as_printed": True}, e))
        return out


def _row(
    row: dict[str, Any], e: dict[str, Any], lien: dict[str, float], dropped: int, shared: set[str] | None = None
) -> dict[str, Any]:
    """One worksheet row: the provider's own ledger total over claim forms;
    the firm's lien report over both; NOT DERIVABLE when neither exists."""
    ranked = sorted(
        ((total_rank(lab) if kd == "LEDGER" else 0, a, lab, fl, pg) for a, lab, fl, pg, kd in e["totals"]),
        key=lambda t: (t[0], t[1]),
        reverse=True,
    )
    best = next((t for t in ranked if t[0] > 0), None)
    if best:
        ours, obasis = best[1], best[3].strip()
    elif ranked:
        claims = {(fl, pg, a) for a, lab, fl, pg, kd in e["totals"] if a > 0}
        ours, obasis = None, f"NOT DERIVABLE - no ledger; {len(claims)} claim form(s) on file, overlapping"
    elif dropped:
        ours, obasis = None, "NOT DERIVABLE - the amount read could not be confirmed on the page; see Amounts Left Out"
    elif shared:
        names = ", ".join(f'"{s}"' for s in sorted(shared))
        ours, obasis = None, f"NOT DERIVABLE - billed under {names}, a name shared by more than one provider here"
    else:
        ours, obasis = None, NO_BILL
    amt, basis = (lien[row["provider"]], "Firm lien report") if row["provider"] in lien else (ours, obasis)
    ledgers = {round(x, 2) for x, lab, _, _, kd in e["totals"] if x > 0 and kd == "LEDGER" and total_rank(lab)}
    alternates = [
        a
        for a in sorted(ledgers, reverse=True)
        if amt is None or (abs(a - amt) > max(100.0, 0.01 * amt) and a >= 0.5 * amt)
    ][:6]
    return {
        **row,
        "total": amt,
        "basis": basis,
        "ours": ours,
        "lien": lien.get(row["provider"]),
        "docs": sorted(e["docs"]),
        "adjustments": [
            {"amount": a, "label": lab, "doc": fl, "page": pg}
            for a, lab, fl, pg in sorted(e.get("adjustments") or [], reverse=True)
        ],
        "alternates": alternates,
        "paid": BP.summarize(e.get("printed") or []),
    }


def _lien(d: Any, matcher: Matcher, spine: list[dict[str, Any]]) -> dict[str, float]:
    lien: dict[str, float] = {}
    lp = read_json(d / "lien_report.json", None)
    for sect in ("medical_lienholders", "medical_balances_no_lien") if lp else ():
        for r in lp.get(sect) or []:
            lien[matcher.provider(r["provider"], spine) or r["provider"]] = r["total_claim"]
    return lien


def _lien_only(k: str, v: float) -> dict[str, Any]:
    return {
        "provider": k,
        "first": None,
        "last": None,
        "total": v,
        "basis": "Firm lien report",
        "ours": None,
        "lien": v,
        "docs": [],
        "adjustments": [],
        "alternates": [],
        "paid": BP.summarize([]),
    }


def run(sr: StageRun) -> int:
    d = sr.slug_dir
    unit = sr.unit.unit
    sfx = f"-{unit}" if sr.job.joint else ""
    patient = sr.unit.client_name.lower() if sr.job.joint else None
    matcher = Matcher(sr.cfg, d)
    records = read_jsonl(d / "billing_extract.jsonl")
    chrono = (d / "runs" / unit / "final-chronology.md").read_text(encoding="utf-8")
    spine = matcher.spine(chrono, _labels(records, patient))
    tally = _Tally(sr, matcher, spine, patient)
    for r in records:
        tally.record(r)
    lien = _lien(d, matcher, spine)
    rows = [
        _row(
            row,
            tally.bills.get(row["provider"], _new_entry()),
            lien,
            tally.dropped_for.get(row["provider"], 0),
            tally.shared.get(row["provider"]),
        )
        for row in spine
    ]
    rows += [_lien_only(k, v) for k, v in lien.items() if not any(r["provider"] == k for r in rows)]
    printed = [_row(row, e, {}, tally.dropped_for.get(row["provider"], 0)) for row, e in tally.own_rows(lien)]
    for r in rows + printed:
        amt = r["total"]
        sr.log(f"{r['provider'][:43]:<44}{(f'{amt:,.2f}' if amt is not None else '-'):>15}  {r['basis'][:44]}")
    grand = sum(r["total"] or 0.0 for r in rows)
    gaps = [r for r in rows if r["total"] is None]
    sr.log(
        f"{'GRAND TOTAL' if not gaps else 'SUBTOTAL'} {grand:,.2f}"
        + (f"  INCOMPLETE: {len(gaps)} provider(s) have no derivable total" if gaps else "")
    )
    if printed:
        sub = sum(r["total"] or 0.0 for r in printed)
        sr.log(
            f"{len(printed)} bill label(s) not matched to a chronology provider: shown as printed under their own "
            f"subtotal {sub:,.2f}, NOT in the grand total"
        )
    (d / f"billing_chart{sfx}.json").write_text(
        json.dumps(
            {
                "rows": rows,
                "grand_total": round(grand, 2),
                "as_printed_rows": printed,
                "as_printed_subtotal": round(sum(r["total"] or 0.0 for r in printed), 2),
                "inherited_attribution": [list(x) for x in tally.inherited],
                "unmatched": {
                    k: sorted({round(a, 2) for a, _, _, _, _ in v["totals"]}, reverse=True)[:5]
                    for k, v in tally.unmatched.items()
                },
                "subrogation": [[a, str(b), c] for a, b, c in tally.subro],
                "vendor_invoices": [[a, str(b)] for a, b in tally.vendor],
                "quarantined": tally.quarantine,
                "failed_pages": tally.failed,
                "unit": unit,
                "patient_filter": patient,
                # Always empty since row 18: every suspect amount is settled by
                # rule. billing_docx refuses a non-empty list as a defect.
                "suspect_amounts": [],
                "dropped_rows": tally.dropped,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    (d / f"billing_dropped_rows{sfx}.json").write_text(json.dumps(tally.dropped, indent=1), encoding="utf-8")
    if tally.dropped:
        sr.log(
            f"!! {len(tally.dropped)} amount(s) at or above the suspect threshold could not be confirmed on the "
            f"page; left out of every total and named in billing_dropped_rows{sfx}.json and on the worksheet"
        )
    return 0
