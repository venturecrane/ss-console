"""Which billing records an update's worksheet refresh reads, decided from the
matter listing alone. $0, no seat, no model.

WHY (2026-11-17). The first build planned every billing-named file on the
matter. On a two-client matter, an update for one client (a one-unit job)
would have read the other client's ledgers as this client's: 18 files of the
other client's folders, a fire department's "No Record Statement", and one of
the other client's delivered exhibits. The rules, in order, for each PDF or
image the firm's billing patterns (``billing.name_patterns``) match:

1. never a file inside ANY delivered chronology folder (a folder holding a
   chronology by the firm's own file name): an exhibit is never a billing
   source;
2. routed exactly as ``build_units`` routes a joint matter's files: a folder
   under this client's ``folder_prefix``, else under another client's
   (``job.matter_clients``, every client a delivered chronology on the matter
   was for, stamped by the broker), else this client's name token in the file
   name, else another client's: this client's is read, another's is set aside;
3. a file no rule routes (a shared folder, the matter root) is read, and every
   billing page that names another patient is set aside by the chart's own
   patient check (``billing_chart.quarantined``), listed on the worksheet under
   its "Excluded" section, as a joint matter's worksheet does.

A billing-named file that is not a bill (a no-record statement, a records
vendor's invoice to the firm) is read like any other: the full run's billing
read types it (``RECORDS_ONLY``, ``VENDOR_INVOICE``), the chart keeps a vendor
invoice out of every total, and the date-of-service check reads only bills and
ledgers (``dos_check.DOS_TYPES``), so neither ever yields a billed date.

The command shows the plan for a job dir, from the listing the job saved::

    python -m jobs.medchron.billing_plan <job dir> [--include <file id>,...] [--json]

It prints each file it would read (folder, size, pages when its bytes are on
disk), each it sets aside and why, the totals and the projected dollars.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from ..filing import full_name

EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
OWN, SHARED = "this client's folder or name", "no client's folder; pages naming another patient are set aside"
IN_CHRONOLOGY = "inside a delivered chronology folder: an exhibit is never a billing source"
OTHER_CLIENT = "in another client's folder or named for another client"


def _token(token: str, text: str) -> bool:
    from ..stages.units import token_hit

    return bool(token) and token_hit(token, text)


def chronology_folders(listing: list[dict[str, Any]], template: str) -> set[str]:
    """Folders holding a chronology by the firm's file name, for any client."""
    stem, _, ext = template.rpartition(".")
    rx = re.escape(stem).replace(re.escape("{CLIENT}"), ".+").replace(re.escape("{MM-DD-YY}"), r"\d{2}-\d{2}-\d{2}")
    pat = re.compile(rf"^{rx}(?: \(\d+\))?$", re.I)
    return {str(d.get("folderId")) for d in listing if d.get("folderId") and pat.match(str(d.get("name") or ""))}


def prefix_of(raw: str | None, tops: list[str]) -> str | None:
    """A unit's ``folder_prefix`` as the folder paths carry it: one leading
    slash, matched to the real top-level folder ignoring case and punctuation
    (``decision_rules.resolve_prefix``, the one ``decisions.selection`` uses).
    2026-11-17: the envelope carries the bare folder name and every folder path
    starts with "/", so an unnormalised prefix routed nothing."""
    if not raw or not str(raw).strip("/"):
        return None
    from .decision_rules import resolve_prefix

    return resolve_prefix(str(raw), tops) or f"/{str(raw).strip('/')}"


def _under(path: str, prefix: str | None) -> bool:
    return bool(prefix) and (path == prefix or path.startswith(f"{prefix}/"))


def _route(path: str, name: str, unit: Any, others: list[dict[str, str]], tops: list[str]) -> str | None:
    """OWN, OTHER_CLIENT or None (no rule routes it)."""
    if _under(path, prefix_of(unit.folder_prefix, tops)):
        return OWN
    if any(_under(path, prefix_of(o.get("folder_prefix"), tops)) for o in others):
        return OTHER_CLIENT
    if _token(unit.name_token or unit.surname, name):
        return OWN
    if any(_token(str(o.get("surname") or ""), name) for o in others):
        return OTHER_CLIENT
    return None


def decide(
    listing: list[dict[str, Any]],
    folder_paths: dict[str, str],
    patterns: list[str],
    unit: Any,
    others: list[dict[str, str]],
    chronology_template: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(the files read, each with ``why``; the files set aside, each with ``why``)."""
    pats = [re.compile(str(p), re.I) for p in patterns]
    me = {str(unit.client_name).lower(), str(unit.surname).lower()}
    others = [o for o in others if str(o.get("client_name") or "").lower() not in me]
    chron = chronology_folders(listing, chronology_template)
    tops = sorted({p.strip("/").split("/", 1)[0] for p in folder_paths.values() if p.strip("/")})
    read, aside = [], []
    for d in listing:
        name, ext = str(d.get("name") or ""), str(d.get("ext") or "").lower()
        if d.get("deleted") or ext not in EXTS or not any(rx.search(name) for rx in pats):
            continue
        fid, folder = str(d.get("folderId") or ""), folder_paths.get(str(d.get("folderId") or ""), "/(root)")
        row = {"id": str(d["id"]), "name": name, "ext": ext, "size": d.get("size"), "folder": folder}
        if fid in chron:
            aside.append({**row, "why": IN_CHRONOLOGY})
            continue
        route = _route(folder, name, unit, others, tops)
        if route == OTHER_CLIENT:
            aside.append({**row, "why": OTHER_CLIENT})
        else:
            read.append({**row, "why": OWN if route == OWN else SHARED, "folderId": fid})
    return read, aside


def _pages(path: Path) -> int | None:
    import pymupdf

    try:
        with pymupdf.open(str(path)) as doc:
            return len(doc)
    except Exception:  # noqa: BLE001 - a file it cannot open has no known page count here
        return None


def report(job_dir: Path, include: list[str] | None = None, firm_config: str | None = None) -> dict[str, Any]:
    """The plan for a job dir, from the listing and folders the job saved."""
    from . import config as config_mod, job as job_mod
    from ..stages import billing_update as BU

    job = job_mod.load(job_dir)
    sd = job.data_root / job.slug
    man = json.loads((sd / "manifest.json").read_text(encoding="utf-8"))
    listing = man["documents"] if isinstance(man, dict) else man
    folders = {f["id"]: f["path"] for f in json.loads((sd / "folders.json").read_text(encoding="utf-8"))}
    cfg = config_mod.load(firm_config)
    inc = json.loads((sd / "include.json").read_text(encoding="utf-8")) if (sd / "include.json").is_file() else {}
    named = include or [str(x) for x in inc.get("include_file_ids") or []]
    read, aside = decide(
        listing,
        folders,
        cfg.get("billing", "name_patterns") or [],
        job.units[0],
        job.matter_clients,
        str(cfg.get("delivery", "chronology_name_template") or "{CLIENT} - Medical Chronology {MM-DD-YY}.docx"),
    )
    from ..stages.base import read_jsonl

    pulled = {r["id"]: r for r in read_jsonl(sd / "billing" / "pulled.jsonl") if r.get("ok")}
    for r in read:
        p = pulled.get(r["id"], {}).get("path")
        r["pages"] = _pages(Path(p)) if p and Path(p).is_file() else None
        r["too_large"] = BU.too_large(r)  # never asked for; named on the worksheet as not read
    known = sum(r["pages"] or 0 for r in read)
    rate = BU.rate_for(cfg) or 0.0
    pats = [re.compile(str(p), re.I) for p in cfg.get("billing", "name_patterns") or []]
    by_id = {str(d.get("id")): d for d in listing}
    named_billing = [i for i in named if i in by_id and any(rx.search(str(by_id[i].get("name") or "")) for rx in pats)]
    return {
        "client": job.units[0].client_name,
        "named_billing_records": len(named_billing),
        "refreshes": bool(named_billing),
        "read": read,
        "set_aside": aside,
        "pages_known": known,
        "files_pages_unknown": sum(1 for r in read if r["pages"] is None),
        "bytes": sum(int(r.get("size") or 0) for r in read),
        "rate_usd_per_page": rate,
        "projected_usd_known_pages": round(known * rate, 4),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobs.medchron.billing_plan", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("job_dir", type=Path)
    ap.add_argument("--include", default="", help="comma-separated file ids the request names (default: include.json)")
    ap.add_argument("--firm-config", default=None, help="the firm config (default: the seat's)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    from ..stages import billing_update as BU

    rep = report(a.job_dir, [x for x in a.include.split(",") if x] or None, a.firm_config)
    if a.json:
        print(json.dumps(rep, indent=1))
        return 0
    print(
        f"client: {rep['client']}; named billing records: {rep['named_billing_records']}; refreshes: {rep['refreshes']}"
    )
    for r in rep["read"]:
        pages = (
            BU.TOO_LARGE
            if r["too_large"]
            else ("pages unknown until pulled" if r["pages"] is None else f"{r['pages']} pp")
        )
        print(f"READ   {full_name(r)} | {r['folder']} | {int(r.get('size') or 0) // 1024} KB | {pages} | {r['why']}")
    for r in rep["set_aside"]:
        print(f"ASIDE  {full_name(r)} | {r['folder']} | {r['why']}")
    print(
        f"total: {len(rep['read'])} file(s) read, {len(rep['set_aside'])} set aside; {rep['pages_known']} known page(s), "
        f"{rep['files_pages_unknown']} file(s) with pages unknown; projected about {rep['projected_usd_known_pages']:.2f} USD "
        f"for the known pages at {rep['rate_usd_per_page'] * 100:.2f} cents a page"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
