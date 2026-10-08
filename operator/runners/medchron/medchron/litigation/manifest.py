"""The lane's memory between runs, and the change detection that reads it.

On the volume (``$MEDCHRON_DATA_DIR/litigation/state/``, the runner's own)::

    matters/<matter_id>.json   the last delivered result for the matter (every
                               value {value|date, source:{file_id,name,doc_date}})
    manifest/<matter_id>.json  {file_id: dateModified} as of that delivery
    baseline.json              where the first prior state came from (seed provenance)

Written only after a delivery is read back (``commit``), so a held or failed
run leaves the next run's baseline exactly where the last good one put it.

Change detection is CANDIDATE-FILTERED. A matter is re-read when a file that
is new or whose ``dateModified`` moved is one a read would open: a court- or
discovery-named file, a process server's record, a recent email whose name
points at the case, or one of the matter's ``email_recent_n`` newest emails. A
new medical bill does not re-read a matter; a new proof of service does. A
matter with no prior state is read in full. A matter whose prior state lacks
a field group (a seed that predates discovery) gets that group read alone. A
matter seeded from a two-pass run is audited (pass 3) in full once.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any

from . import vocab
from .firm import LitigationFirm

STATE_ENV = "MEDCHRON_LITIGATION_STATE_DIR"
DATA_ENV = "MEDCHRON_DATA_DIR"
DEFAULT_DATA_DIR = "/opt/data/medchron"
EMAIL_EXTS = (".msg", ".eml")
#: Per-matter fetch caps (``fetch_caps`` in the firm config overrides any).
FETCH_CAPS = {"court": 60, "server": 15, "discovery": 30, "email": 25, "total": 120}


def state_dir(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit)
    if os.environ.get(STATE_ENV):
        return Path(os.environ[STATE_ENV])
    return Path(os.environ.get(DATA_ENV) or DEFAULT_DATA_DIR) / "litigation" / "state"


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _atomic(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    tmp.replace(path)


def load_prior(state: Path, matter_id: str) -> dict[str, Any] | None:
    v = _read(state / "matters" / f"{matter_id}.json")
    return v if isinstance(v, dict) else None


def load_manifest(state: Path, matter_id: str) -> dict[str, str]:
    v = _read(state / "manifest" / f"{matter_id}.json")
    return {str(k): str(x or "") for k, x in v.items()} if isinstance(v, dict) else {}


def load_baseline(state: Path) -> dict[str, Any] | None:
    v = _read(state / "baseline.json")
    return v if isinstance(v, dict) else None


def tracked(state: Path) -> set[str]:
    d = state / "matters"
    return {p.stem for p in d.glob("*.json")} if d.is_dir() else set()


def current_manifest(files: list[dict[str, Any]]) -> dict[str, str]:
    return {str(f["id"]): str(f.get("modified") or f.get("created") or "") for f in files if not f.get("deleted")}


def diff(now: dict[str, str], prior: dict[str, str]) -> dict[str, list[str]]:
    return {
        "new": sorted(k for k in now if k not in prior),
        # A seed row carries "" (the hand inventory had no dateModified): the
        # file existed at the seed, and only a later run's value can move.
        "changed": sorted(k for k in now if k in prior and prior[k] and now[k] != prior[k]),
        "removed": sorted(k for k in prior if k not in now),
    }


def file_date(f: dict[str, Any]) -> str:
    return str(f.get("modified") or f.get("created") or "")[:10]


def full_name(f: dict[str, Any]) -> str:
    name, ext = str(f.get("name") or ""), str(f.get("ext") or "")
    return name if not ext or name.lower().endswith(ext.lower()) else name + ext


def is_email(f: dict[str, Any]) -> bool:
    return str(f.get("ext") or "").lower() in EMAIL_EXTS


def newest_emails(files: list[dict[str, Any]], n: int) -> list[str]:
    mail = [f for f in files if is_email(f) and not f.get("deleted")]
    mail.sort(key=lambda f: (file_date(f), str(f.get("modified") or "")), reverse=True)
    return [str(f["id"]) for f in mail[:n]]


def _recent(f: dict[str, Any], firm: LitigationFirm, today: dt.date) -> bool:
    try:
        when = dt.date.fromisoformat(file_date(f))
    except ValueError:
        return True  # an undated file is read rather than assumed old
    return (today - when).days <= 31 * int(firm.get("email_months"))


def classify(f: dict[str, Any], firm: LitigationFirm, today: dt.date) -> str | None:
    """Why a read would open this file by its name, or None.

    * ``court``: a court paper by name (any age; the case's own record);
    * ``server``: a process server's record (a file of any age; an email
      only when recent: the hand method read service emails of the last year);
    * ``discovery``: a discovery paper by name (a file of any age);
    * ``email``: a recent email whose name points at the case (court,
      discovery, service or settlement words).
    The newest emails regardless of name are added by ``select``."""
    if f.get("deleted"):
        return None
    name = full_name(f)
    court = any(p.search(name) for p in firm.court_rx)
    server = any(s in name.lower() for s in firm.process_servers)
    disc = any(p.search(name) for p in firm.discovery_rx)
    if is_email(f):
        named = court or server or disc or any(p.search(name) for p in firm.settlement_rx)
        return "email" if named and _recent(f, firm, today) else None
    if court:
        return "court"
    if server:
        return "server"
    return "discovery" if disc else None


def is_candidate(f: dict[str, Any], firm: LitigationFirm, today: dt.date) -> bool:
    return classify(f, firm, today) is not None


def _newest_first(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda f: (file_date(f), str(f.get("modified") or "")), reverse=True)


def select(files: list[dict[str, Any]], firm: LitigationFirm, today: dt.date) -> list[tuple[str, str]]:
    """The files a read is handed, in the order they are handed, with the
    class each came in by: court papers (newest first, plus the earliest
    complaint-named paper so the filing is never capped away), the N newest
    emails, process-server records, discovery papers, then named recent
    emails. Each class has its own cap and the matter a total
    (``fetch_caps``); what is capped out stays on the list ``list_files``
    shows, so a read can still open it."""
    caps = {**FETCH_CAPS, **(firm.get("fetch_caps") or {})}
    by: dict[str, list[dict[str, Any]]] = {}
    for f in files:
        c = classify(f, firm, today)
        if c:
            by.setdefault(c, []).append(f)
    court = _newest_first(by.get("court", []))
    complaint = [f for f in reversed(court) if re.search(r"(?i)complaint", full_name(f))][:1]
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def take(rows: list[dict[str, Any]], cls: str, cap: int) -> None:
        n = 0
        for f in rows:
            fid = str(f["id"])
            if n >= cap or len(out) >= caps["total"]:
                return
            if fid not in seen:
                seen.add(fid)
                out.append((fid, cls))
                n += 1

    take(complaint, "court", 1)
    take(court, "court", caps["court"] - len(complaint))
    by_id = {str(f["id"]): f for f in files}
    take([by_id[i] for i in newest_emails(files, int(firm.get("email_recent_n")))], "newest_email", caps["total"])
    take(_newest_first(by.get("server", [])), "server", caps["server"])
    take(_newest_first(by.get("discovery", [])), "discovery", caps["discovery"])
    take(_newest_first(by.get("email", [])), "email", caps["email"])
    return out


def candidates(files: list[dict[str, Any]], firm: LitigationFirm, today: dt.date) -> list[str]:
    """The ids ``select`` hands a read, in order."""
    return [fid for fid, _c in select(files, firm, today)]


def _missing_groups(prior: dict[str, Any]) -> list[str]:
    # A present marker is authoritative, EMPTY included: a seed whose groups
    # were all struck for a re-read must read them all. Inferring from the
    # fields here (the old test was "falsy") read an emptied marker as "no
    # marker" and treated a struck defendants group as read (2026-10-08).
    marked = prior.get("fields_read")
    have = set(marked) if isinstance(marked, list) else set()
    if not isinstance(marked, list):  # a seed without the marker: infer from the fields it carries
        have = {g for g, keys in vocab.GROUP_FIELDS.items() if all(k in prior for k in keys)}
    return [g for g in vocab.GROUPS if g not in have]


def plan_matter(
    files: list[dict[str, Any]],
    prior: dict[str, Any] | None,
    prior_manifest: dict[str, str],
    firm: LitigationFirm,
    today: dt.date,
) -> dict[str, Any]:
    sel = select(files, firm, today)
    cands = [fid for fid, _c in sel]
    classes: dict[str, int] = {}
    for _fid, c in sel:
        classes[c] = classes.get(c, 0) + 1
    out = _plan(files, prior, prior_manifest, cands, today)
    return {**out, "candidate_classes": classes}


def _plan(
    files: list[dict[str, Any]],
    prior: dict[str, Any] | None,
    prior_manifest: dict[str, str],
    cands: list[str],
    today: dt.date,
) -> dict[str, Any]:
    if prior is None:
        return {
            "reason": "new",
            "read_groups": list(vocab.GROUPS),
            "audit": "all",
            "trigger_files": [],
            "candidates": cands,
        }
    d = diff(current_manifest(files), prior_manifest)
    moved = set(d["new"]) | set(d["changed"])
    trigger = [c for c in cands if c in moved]
    two_pass = bool((prior.get("provenance") or {}).get("two_pass"))
    if trigger:
        return {
            "reason": "changed",
            "read_groups": list(vocab.GROUPS),
            "audit": "all" if two_pass else "changed",
            "trigger_files": trigger,
            "candidates": cands,
        }
    missing = _missing_groups(prior)
    return {
        "reason": "missing_fields" if missing else ("two_pass_audit" if two_pass else "unchanged"),
        "read_groups": missing,
        "audit": "all" if two_pass else ("changed" if missing else "none"),
        "trigger_files": [],
        "candidates": cands,
    }


def commit(state: Path, matter_id: str, result: dict[str, Any], files: list[dict[str, Any]]) -> None:
    _atomic(state / "matters" / f"{matter_id}.json", result)
    _atomic(state / "manifest" / f"{matter_id}.json", current_manifest(files))
