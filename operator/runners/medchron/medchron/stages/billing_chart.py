"""`billing_chart`: the per-provider billing chart on the CHRONOLOGY's
provider list. $0.

A billing label is not a provider (a first version built 18 rows from
whatever name appeared on a bill and a grand total 3x the firm's own lien
report). The chronology's Treatment Timeline is the spine; billing documents
are matched onto it through the firm's `billing.provider_match` table plus a
head-substring fallback, and anything that will not match is REPORTED, never
dropped. Per provider the chart prefers the provider's own account-wide
printed total (a LEDGER's) over a sum of claim forms, which double-counts.
When no ledger exists the total is NOT DERIVABLE and says so: inventing one
is exactly the fabricated figure this pipeline exists to prevent. Money is
parsed, never computed by the model; an amount at or above
`billing.suspect_amount_cents` is a lost decimal until a person looks, and
the worksheet refuses to render over one. On a joint matter the patient
filter quarantines the other plaintiff's bills.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from .base import StageRun, read_json, read_jsonl

NON_PROVIDER = {"VENDOR_INVOICE", "CERTIFICATE", "RECORDS_ONLY", "OTHER"}
ADJ_LABEL = re.compile(r"adjust|write.?off|contractual|disallow", re.I)
MULTI = re.compile(r"multiple providers|see line items|;|,\s*\w+\s*\(PAR\)", re.I)
LEDGER_TOTAL = [
    (re.compile(r"overall\s*-\s*total|grand\s*total", re.I), 3),
    (re.compile(r"^overall$|total\s*charges?$|account\s*total|total\s*billed", re.I), 2),
    (re.compile(r"^charge$|balance|amount\s*due|self.pay", re.I), 1),
]
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


def dt(s: Any) -> str | None:
    m = re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", str(s or ""))
    if not m:
        return None
    mo, dy, yr = (int(x) for x in m.groups())
    yr += 2000 if yr < 100 else 0
    return f"{yr:04d}-{mo:02d}-{dy:02d}" if 1 <= mo <= 12 and 1 <= dy <= 31 else None


class Matcher:
    def __init__(self, cfg: Any, slug_dir) -> None:
        self.match: dict[str, list[str]] = {k: list(v) for k, v in (cfg.get("billing", "provider_match") or {}).items()}
        self.match.update(read_json(slug_dir / "provider_match.json", {}) or {})

    def timeline(self, text: str) -> list[dict[str, Any]]:
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

    def provider(self, label: str | None, spine: list[dict[str, Any]]) -> str | None:
        lab = (label or "").lower()
        for row in spine:
            if any(re.search(pat, lab, re.I) for pat in self.match.get(row["provider"], [])):
                return row["provider"]
        for row in spine:
            head = re.split(r"[,(\-]", row["provider"])[0].strip().lower()
            if len(head) > 5 and head in lab:
                return row["provider"]
        return None


def run(sr: StageRun) -> int:
    d = sr.slug_dir
    unit = sr.unit.unit
    sfx = f"-{unit}" if sr.job.joint else ""
    patient = sr.unit.client_name.lower() if sr.job.joint else None
    suspect_at = int(sr.cfg.get("billing", "suspect_amount_cents") or 50_000_000) / 100.0
    suspect: list[tuple[str, float]] = []
    matcher = Matcher(sr.cfg, d)
    spine = matcher.collapse(matcher.timeline((d / "runs" / unit / "final-chronology.md").read_text(encoding="utf-8")))
    bills: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"totals": [], "docs": set(), "bdates": [], "adjustments": []}
    )
    unmatched: dict[str, dict[str, Any]] = defaultdict(lambda: {"totals": [], "docs": set()})
    vendor: list = []
    subro: list = []
    quarantine: list = []
    failed: list = []
    inherited: list = []
    for r in read_jsonl(d / "billing_extract.jsonl"):
        carry = None
        for c in r.get("chunks") or []:
            if "FAILED_PAGE" in c:
                failed.append((r["file"], c["FAILED_PAGE"]))
                continue
            if not isinstance(c, dict) or "doc_type" not in c:
                continue
            who = c.get("patient") or ""
            if patient and who and patient not in who.lower():
                quarantine.append((r["file"], who, c.get("provider")))
                continue
            kind = c.get("doc_type")
            if kind == "VENDOR_INVOICE":
                vendor.append((r["file"], c.get("provider")))
                continue
            if kind == "LIEN_SUBROGATION":
                subro.append(
                    (r["file"], c.get("provider"), [t.get("amount") for t in c.get("printed_totals") or []][:3])
                )
                continue
            if kind in NON_PROVIDER:
                continue
            raw_provider = (c.get("provider") or "").strip()
            tgt = matcher.provider(c.get("provider"), spine)
            direct = bool(tgt) and not MULTI.search(raw_provider)
            if tgt:
                carry = tgt
            elif not raw_provider and carry:
                # A continuation chunk carries no provider header; the name is
                # printed once on page one. Inherit ONLY when the field is
                # blank, never when a name simply failed to match.
                tgt = carry
                inherited.append((r["file"], c.get("page_first"), carry))
            e = bills[tgt] if tgt else unmatched[str(c.get("provider"))[:44]]
            e["docs"].add(r["file"])
            for t in c.get("printed_totals") or []:
                a = money(t.get("amount"), suspect, suspect_at)
                if a is None:
                    continue
                e["totals"].append((a, t.get("label"), r["file"], t.get("page"), kind))
                lab = str(t.get("label") or "")
                if tgt and direct and ADJ_LABEL.search(lab) and matcher.provider(lab, spine) in (None, tgt):
                    e.setdefault("adjustments", []).append((abs(a), lab, r["file"], t.get("page")))
            if tgt:
                e["bdates"].extend(v for k in ("date_first", "date_last") if (v := dt(c.get(k))))
    lien: dict[str, float] = {}
    lp = read_json(d / "lien_report.json", None)
    if lp:
        for sect in ("medical_lienholders", "medical_balances_no_lien"):
            for r in lp.get(sect) or []:
                lien[matcher.provider(r["provider"], spine) or r["provider"]] = r["total_claim"]
    grand = 0.0
    rows: list[dict[str, Any]] = []
    for row in spine:
        e = bills.get(row["provider"], {"totals": [], "docs": set(), "bdates": [], "adjustments": []})
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
        else:
            ours, obasis = None, "no bill located"
        amt, basis = (lien[row["provider"]], "Firm lien report") if row["provider"] in lien else (ours, obasis)
        grand += amt or 0.0
        alternates = [
            a
            for a in sorted(
                {round(x, 2) for x, lab, _, _, kd in e["totals"] if x > 0 and kd == "LEDGER" and total_rank(lab)},
                reverse=True,
            )
            if amt is None or (abs(a - amt) > max(100.0, 0.01 * amt) and a >= 0.5 * amt)
        ][:6]
        rows.append(
            {
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
            }
        )
        sr.log(f"{row['provider'][:43]:<44}{(f'{amt:,.2f}' if amt is not None else '-'):>15}  {basis[:44]}")
    for k, v in lien.items():
        if not any(r["provider"] == k for r in rows):
            grand += v
            rows.append(
                {
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
                }
            )
    gaps = [r for r in rows if r["total"] is None]
    sr.log(
        f"{'GRAND TOTAL' if not gaps else 'SUBTOTAL'} {grand:,.2f}"
        + (f"  INCOMPLETE: {len(gaps)} provider(s) have no derivable total" if gaps else "")
    )
    if unmatched:
        sr.log(f"!! {len(unmatched)} billing document label(s) not matched to a chronology provider; NOT in the total")
    (d / f"billing_chart{sfx}.json").write_text(
        json.dumps(
            {
                "rows": rows,
                "grand_total": round(grand, 2),
                "inherited_attribution": [list(x) for x in inherited],
                "unmatched": {
                    k: sorted({round(a, 2) for a, _, _, _, _ in v["totals"]}, reverse=True)[:5]
                    for k, v in unmatched.items()
                },
                "subrogation": [[a, str(b), c] for a, b, c in subro],
                "vendor_invoices": [[a, str(b)] for a, b in vendor],
                "quarantined": quarantine,
                "failed_pages": failed,
                "unit": unit,
                "patient_filter": patient,
                "suspect_amounts": [[str(a), b] for a, b in suspect],
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    if suspect:
        sr.log(f"!! {len(suspect)} amount(s) at or above the suspect threshold; likely a LOST DECIMAL in transcription")
    return 0
