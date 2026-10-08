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
from pathlib import Path
from typing import Any

from . import vocab
from .firm import LitigationFirm

STATE_ENV = "MEDCHRON_LITIGATION_STATE_DIR"
DATA_ENV = "MEDCHRON_DATA_DIR"
DEFAULT_DATA_DIR = "/opt/data/medchron"
EMAIL_EXTS = (".msg", ".eml")


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


def is_candidate(f: dict[str, Any], firm: LitigationFirm, today: dt.date) -> bool:
    """Would a read open this file by its name? Court and discovery papers and
    process-server records always; an email only when it is recent and its
    name points at the case (service, answer, settlement, a hearing)."""
    if f.get("deleted"):
        return False
    name = full_name(f)
    if any(s in name.lower() for s in firm.process_servers):
        return True
    named = any(p.search(name) for p in [*firm.court_rx, *firm.discovery_rx])
    if not is_email(f):
        return named
    if not (named or any(p.search(name) for p in firm.settlement_rx)):
        return False
    try:
        when = dt.date.fromisoformat(file_date(f))
    except ValueError:
        return True  # an undated email is read rather than assumed old
    return (today - when).days <= 31 * int(firm.get("email_months"))


def candidates(files: list[dict[str, Any]], firm: LitigationFirm, today: dt.date) -> list[str]:
    """Every file a read is handed up front: the name-filtered set plus the N
    newest emails regardless of name."""
    named = [str(f["id"]) for f in files if is_candidate(f, firm, today)]
    return list(dict.fromkeys([*named, *newest_emails(files, int(firm.get("email_recent_n")))]))


def _missing_groups(prior: dict[str, Any]) -> list[str]:
    have = set(prior.get("fields_read") or [])
    if not have:  # a seed without the marker: infer from the fields it carries
        have = {g for g, keys in vocab.GROUP_FIELDS.items() if all(k in prior for k in keys)}
    return [g for g in vocab.GROUPS if g not in have]


def plan_matter(
    files: list[dict[str, Any]],
    prior: dict[str, Any] | None,
    prior_manifest: dict[str, str],
    firm: LitigationFirm,
    today: dt.date,
) -> dict[str, Any]:
    cands = candidates(files, firm, today)
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
