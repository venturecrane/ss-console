"""An update that names a billing record refreshes the billing worksheet (2026-11-17).

WHY. An update named two patient ledgers whose dates of service appear in no
medical record on file. The house rule (``prompts/map-system.md``,
``dos_check`` ``billed_no_record``) is that a billing-only source makes no
chronology entry and its dates belong on the billing worksheet. The update
dropped the ledgers by name and never ran a billing stage, so those dates
reached no filed document, while the firm had been told they "will show as
billing only".

WHAT. Only when the request names at least one file the firm's billing
patterns (``billing.name_patterns``) match:

* ``update_billing_docs`` ($0), right after ``decide_selection`` and before
  any paid stage, so a hold on another client's unknown folder costs nothing
  (2026-11-20): every billing-named PDF or image on the matter outside the
  delivered chronology's folder, the delivered ones included, so the
  refreshed worksheet is complete. Written to ``billing/plan.json``.

The rest run after the proofs and before the manifest:

* ``update_billing_extract`` (paid, the transcription tier, nothing else):
  each planned file pulled into ``billing/raw/`` (never ``raw/``: a billing
  file is never a chronology source and never goes to vision), then the full
  run's ``billing_extract`` over them. A file already in this matter's
  ``billing_extract.jsonl`` is not read again. The projected cost (pages x
  the rate below) is logged before any call, and every call passes the job's
  cap (the doorway's per-call check). The update's page charge never counts
  these pages: they are not in ``extracted.jsonl``.
* ``update_dos_check`` ($0): the billed dates against the WHOLE updated
  chronology; the ones no record carries are listed (``dos_report.json``).
* ``update_billing_chart`` and ``update_billing_docx`` ($0): the full run's
  chart, laid against the updated chronology, and its worksheet, which lists
  those dates under "Dates billed with no medical record on file".

The new worksheet goes into the manifest; ``update_upload`` retires the
delivered one by id when it is still SMD's unedited copy
(``update_supersede``), else files beside it and the reply says so. The reply's
one billing sentence is code's (``reply_line``), from ``billing_refresh.json``.

RATE. ``billing_extract`` sends 3 page images per call at 115 dpi with an
8000-token output cap. Per page that is about ``IN_TOKENS_PER_PAGE`` input
tokens (one page image plus a third of the prompt) and ``OUT_TOKENS_PER_PAGE``
output tokens, priced at the transcription model's rate from the pricing table:
about 1.3 cents a page on the transcription tier's current model. An estimate,
stated as one; the ledger records what was spent.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .. import budget, llm
from ..filing import full_name
from ..matter import pull as matter_pull
from .base import StageRun, read_json, read_jsonl
from .update_base import update_dir

IN_TOKENS_PER_PAGE = 1900
OUT_TOKENS_PER_PAGE = 800
EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
REFRESH = "billing_refresh.json"
#: ``update_billing_docs`` holds: another client's folder on the matter, and no client named for it
EXIT_UNKNOWN = 2
#: ``billing/not_read.json``: each planned billing record no read reached, with why
NOT_READ = "not_read.json"
TOO_LARGE = "too large to read (over the seat's read limit)"


#: The Smokeball connector's read limit (``smokeball_connector.client._MAX_DOWNLOAD_BYTES``,
#: 25 MiB): a file the listing says is larger is never asked for (2026-11-17: a
#: 29.5 MB billing file "could not be pulled" and the run went on silently).
READ_LIMIT_BYTES = 25 * 1024 * 1024


def too_large(t: dict[str, Any]) -> bool:
    size = t.get("size")
    return isinstance(size, int) and not isinstance(size, bool) and size > READ_LIMIT_BYTES


def not_read_why(row: dict[str, Any]) -> str:
    """Why a planned billing record was not read, in plain words."""
    err = str(row.get("error") or "")
    return TOO_LARGE if "read limit" in err or too_large(row) else "it could not be pulled from the matter"


def _dir(sr: StageRun) -> Path:
    return sr.slug_dir / "billing"


def _patterns(sr: StageRun) -> list[re.Pattern]:
    return [re.compile(str(p), re.I) for p in sr.cfg.get("billing", "name_patterns") or []]


def _is_billing(name: str, pats: list[re.Pattern]) -> bool:
    return any(rx.search(name) for rx in pats)


def asked(sr: StageRun) -> list[str]:
    """The names of the requested files the billing patterns match."""
    ids = {str(x) for x in (read_json(sr.slug_dir / "include.json", {}) or {}).get("include_file_ids") or []}
    pats = _patterns(sr)
    return [
        str(d.get("name") or "")
        for d in sr.manifest()
        if str(d.get("id")) in ids and _is_billing(str(d.get("name") or ""), pats)
    ]


def run_docs(sr: StageRun) -> int:
    """``billing/plan.json``: what the worksheet is refreshed from, scoped to
    this client (``medchron.billing_plan``), or [] when the request names no
    billing record (then no billing stage does anything). The files set aside
    are kept in ``billing/set_aside.json`` with why."""
    from ..medchron import billing_plan as BP

    out = _dir(sr)
    out.mkdir(parents=True, exist_ok=True)
    named = asked(sr)
    read: list[dict[str, Any]] = []
    aside: list[dict[str, Any]] = []
    if named:
        template = str(
            sr.cfg.get("delivery", "chronology_name_template") or "{CLIENT} - Medical Chronology {MM-DD-YY}.docx"
        )
        pats = sr.cfg.get("billing", "name_patterns") or []
        classes = (
            sr.cfg.get("selection", "shared_folder_classes") or [],
            sr.cfg.get("selection", "exclude_folder_classes") or [],
        )
        try:
            read, aside = BP.decide(
                sr.manifest(), sr.folder_paths(), pats, sr.unit, sr.job.matter_clients, template, *classes
            )
        except BP.Unknown as exc:
            sr.log(str(exc))
            return EXIT_UNKNOWN
    by_id = {str(d.get("id")): d for d in sr.manifest()}
    targets = [{**matter_pull.target(by_id[r["id"]]), "why": r["why"]} for r in read]
    (out / "plan.json").write_text(json.dumps(targets, indent=1), encoding="utf-8")
    (out / "set_aside.json").write_text(json.dumps(aside, indent=1), encoding="utf-8")
    if not named:
        sr.log("update_billing_docs: the request names no billing record; the billing worksheet is not refreshed")
    else:
        sr.log(
            f"update_billing_docs: {len(named)} named billing record(s); the worksheet is refreshed from "
            f"{len(targets)} of this client's billing file(s); {len(aside)} set aside"
        )
    return 0


def patient(sr: StageRun) -> str:
    """The patient every billing page is checked against: this client, always
    (a one-unit update on a joint matter is not ``job.joint``)."""
    return str(sr.unit.client_name).lower()


def rate_usd(sr: StageRun) -> float | None:
    return rate_for(sr.cfg)


def rate_for(cfg: Any) -> float | None:
    """Dollars per billing page on the transcription tier, or None unpriced."""
    model = llm.model_for(cfg, "transcription")
    tree = Path(__file__).resolve().parents[4] / "adapter" / "cost_telemetry" / "anthropic_pricing.json"
    for path in (os.environ.get(budget.PRICING_ENV), budget.PRICING_DEFAULT, str(tree)):
        try:
            if path and Path(path).is_file():
                r = budget.Pricing.load(Path(path)).rate_for(model)
                cents = (
                    IN_TOKENS_PER_PAGE * r.input_per_million_cents + OUT_TOKENS_PER_PAGE * r.output_per_million_cents
                )
                return cents / 1e8
        except (OSError, ValueError, KeyError, budget.BudgetError):
            continue
    return None


def _pages(path: str) -> int:
    import pymupdf

    try:
        with pymupdf.open(path) as doc:
            return len(doc)
    except Exception:  # noqa: BLE001 - an unopenable file is counted as one page; billing_extract names it a gap
        return 1


def todo(sr: StageRun) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(planned files with bytes on disk not yet extracted, planned files not yet pulled)."""
    done = {r.get("file") for r in read_jsonl(sr.slug_dir / "billing_extract.jsonl")}
    pulled = {r["id"]: r for r in read_jsonl(_dir(sr) / "pulled.jsonl") if r.get("ok") and r.get("path")}
    have, missing = [], []
    for t in read_json(_dir(sr) / "plan.json", []) or []:
        if too_large(t):
            continue  # never asked for: named as not read (``run_extract``)
        r = pulled.get(t["id"])
        if r is None:
            missing.append(t)
        elif t.get("name") not in done and Path(r["path"]).is_file():
            have.append({"id": t["id"], "name": t.get("name"), "path": r["path"]})
    return have, missing


def settled(sr: StageRun) -> bool:
    """Nothing left to pull or read: the stage would pay nothing."""
    have, missing = todo(sr)
    return not have and not missing


def projection_lines(sr: StageRun) -> list[str]:
    """What the extract would read and pay, for the log and ``jobs rehearse``."""
    have, missing = todo(sr)
    pages = sum(_pages(h["path"]) for h in have)
    rate = rate_usd(sr)
    money = "unpriced" if rate is None else f"about {pages * rate:.2f} USD at {rate * 100:.1f} cents a page"
    out = [f"billing: {len(have)} file(s), {pages} page(s) on disk to read: {money}"]
    if missing:
        size = sum(int(t.get("size") or 0) for t in missing)
        out.append(
            f"billing: {len(missing)} file(s) not pulled yet ({size // 1024} KB); their pages are counted when pulled"
        )
    return out


def run_extract(sr: StageRun) -> int:
    from . import billing

    targets = read_json(_dir(sr) / "plan.json", []) or []
    if not targets:
        (sr.slug_dir / "billing_extract.jsonl").touch()
        (sr.slug_dir / "billing_docs.json").write_text(json.dumps({"docs": []}), encoding="utf-8")
        return 0
    _have, missing = todo(sr)
    # The seat is opened only for a file not pulled yet: a resume (or a
    # rehearsal copy) with every file on disk touches nothing outside.
    big = [t for t in targets if too_large(t)]
    small = [t for t in targets if not too_large(t)]
    if missing:
        rows = matter_pull.fetch_all(sr.seat, sr.job.matter_id, small, _dir(sr), sr.log)
    else:
        pulled = {r["id"]: r for r in read_jsonl(_dir(sr) / "pulled.jsonl")}
        rows = [pulled[t["id"]] for t in small]
    docs = [{"id": r["id"], "name": r.get("name"), "path": r["path"]} for r in rows if r.get("ok") and r.get("path")]
    lost = [{"name": full_name(t), "why": TOO_LARGE} for t in big]
    lost += [{"name": full_name(r), "why": not_read_why(r)} for r in rows if not r.get("ok")]
    (_dir(sr) / NOT_READ).write_text(json.dumps(lost, indent=1), encoding="utf-8")
    for x in lost:
        sr.log(
            f"billing: a billing record was not read ({x['why']}): {x['name'][:60]}; the worksheet and the reply name it"
        )
    (sr.slug_dir / "billing_docs.json").write_text(json.dumps({"docs": docs}, indent=1), encoding="utf-8")
    for line in projection_lines(sr):
        sr.log(line)
    if not todo(sr)[0]:  # every file already read on this matter: no model is opened
        billing.write_gaps(sr, read_jsonl(sr.slug_dir / "billing_extract.jsonl"))
        return 0
    return billing.run(sr)


def run_dos(sr: StageRun) -> int:
    from ..medchron import dos_check
    from .billing_chart import Matcher
    from ..medchron.update import billing_page_dates

    # Recorded on every update; with no billing record named (no plan), nothing else happens.
    got = billing_page_dates.write(sr.slug_dir, update_dir(sr), [sr.unit])
    if got:
        sr.log(f"dos_check (update): {len(got)} date(s) of service on set-aside billing pages recorded")
    if not read_json(_dir(sr) / "plan.json", []):
        return 0
    rd = sr.slug_dir / "runs" / sr.unit.unit
    md = (update_dir(sr) / "updated-chronology.md").read_text(encoding="utf-8")
    rep = dos_check.check(sr.slug_dir, rd, sr.job.incident_date, patient(sr), md, Matcher(sr.cfg, sr.slug_dir).match)
    dos_check.write(rd, rep)
    sr.log(
        f"dos_check (update): {rep['billed_dates']} billed date(s); with no medical record on file {len(rep['billed_no_record'])}"
    )
    if rep.get("billed_no_record_dates"):  # what the set-aside billing pages WOULD add (record only)
        sr.log(f"dos_check (update): no-record table dates, record only: {rep['billed_no_record_dates']}")
    return 0


def run_chart(sr: StageRun) -> int:
    from . import billing_chart

    if not read_json(_dir(sr) / "plan.json", []):
        return 0
    return billing_chart.run(sr, update_dir(sr) / "updated-chronology.md", patient=patient(sr))


def worksheet(sr: StageRun) -> Path | None:
    out = sr.slug_dir / "out" / sr.unit.unit
    glob = str(sr.cfg.get("delivery", "worksheet_glob") or "* - Medical Billing Worksheet *.docx")
    hits = sorted(out.glob(glob.replace("*", sr.unit.client_name, 1)))
    return hits[-1] if hits else None


def run_docx(sr: StageRun) -> int:
    from . import billing_docx

    if not read_json(_dir(sr) / "plan.json", []):
        return 0
    rc = billing_docx.run(sr)
    if rc:
        return rc
    rep = read_json(sr.slug_dir / "runs" / sr.unit.unit / "dos_report.json", {}) or {}
    ws = worksheet(sr)
    rec = {
        "worksheet": ws.name if ws else None,
        "billed_no_record": len(rep.get("billed_no_record") or []),
        "not_read": read_json(_dir(sr) / NOT_READ, []) or [],
    }
    (update_dir(sr) / REFRESH).write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return 0


def reply_line(ud: Path) -> str | None:
    """The DELIVER wake's billing sentence, code-authored, or None when no
    worksheet was refreshed."""
    rec = read_json(ud / REFRESH, None)
    if not isinstance(rec, dict) or not rec.get("worksheet"):
        return None
    n = int(rec.get("billed_no_record") or 0)
    line = "Billing: the billing worksheet was refreshed from every billing record on the matter"
    line += (
        f"; {n} date(s) of service appear only in billing records and are listed on it under "
        f"'Dates billed with no medical record on file'."
        if n
        else "; every billed date of service is in the chronology."
    )
    lost = [x for x in rec.get("not_read") or [] if isinstance(x, dict)]
    if lost:
        named = "; ".join(f"{x.get('name')} ({x.get('why')})" for x in lost[:5])
        line += f" {len(lost)} billing record(s) could not be read and are named on the worksheet: {named}."
    sup = read_json(ud / "superseded.json", {}) or {}
    left = [x for x in sup.get("left") or [] if isinstance(x, dict)]
    if any(x.get("kind") == "worksheet" for x in left):
        line += " The earlier billing worksheet is still in the folder; the one filed with this update is current."
    return line


__all__ = [
    "REFRESH",
    "asked",
    "projection_lines",
    "rate_usd",
    "reply_line",
    "run_chart",
    "run_docs",
    "run_docx",
    "run_dos",
    "run_extract",
    "settled",
    "todo",
    "worksheet",
]
