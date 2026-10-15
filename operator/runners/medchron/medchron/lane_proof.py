"""Which kinds of job are PROVEN on this seat, and the hold an unproven one
waits in.

THE RULE (the Captain, 2026-10-13). No new kind of job goes live on a real firm
request until it has passed a full dry run on a real matter of that client, and
SMD hears about the request immediately so the proof happens within hours, not
a day. Evidence: about 45 of A&P's 52 defects from 09-01 to 10-13 happened in a
lane's first one to three live uses (ss-console PR "lane-proof gate").

A LANE KEY names a kind of job: the skill that queues it, plus the document
class for the drafting lane, whose classes are different jobs (the first
discovery set, 2026-10-12, failed in ways the mediation brief never had):
``medical-chronology-maintainer``, ``demand-letter-drafter``,
``document-drafter.discovery_set``, ``litigation-status``,
``negotiation-watch``. The overlay's request cards read the same keys.

THE PROVEN SET is two files in the medchron tree, both root-owned on the
volume, so neither the agent uid nor the broker can prove a lane:

* ``lane-proofs.yaml``: the AUTHORED seed and revocations, reviewed in git
  (``operator/customers/<slug>/lane-proofs.yaml``), staged to the vault by the
  provisioner and fetched by the entrypoint every boot;
* ``lane-proofs.runtime.json``: events appended on the seat by
  ``medchron lane-proof accept`` (a passed proof run plus SMD's verify id) and
  ``revoke``, so a proof takes effect without a reprovision, which would kill
  a running job.

A key is proven when the authored file does not revoke it and either its last
runtime event is ``proven`` or it has no runtime event and the authored file
proves it. An unreadable file proves nothing: the lane holds and SMD is paged,
which is loud and recoverable; the opposite failure would deliver unproven work.

THE HOLD. ``Daemon.claim_next`` asks :func:`admits` before claiming each
envelope. An unproven one is skipped (the lane keeps claiming proven jobs
behind it) and stays ``submitted``/``queued``: not ``held``, which on most
ledgers can only move to ``failed``. The ledger row gets one same-state note,
``lane_unproven: <key>``, which is what the overlay's request cards page SMD
on. When the lane is proven, the note is cleared BEFORE the job is claimed (a
reason left on a delivered row once told a requester false things,
2026-10-08), and the job runs exactly as it would have.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("medchron.daemon")

AUTHORED_NAME = "lane-proofs.yaml"
RUNTIME_NAME = "lane-proofs.runtime.json"
HOLDS_DIR = "lane-holds"
HOLD_PREFIX = "lane_unproven: "

#: The skill that queues each daemon lane's jobs (the lane's ``LANE``).
SKILLS = {
    "medchron": "medical-chronology-maintainer",
    "demand": "demand-letter-drafter",
    "drafting": "document-drafter",
    "litigation": "litigation-status",
    "negotiation": "negotiation-watch",
}
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}(?:\.[a-z0-9_]{1,40})?$")
_CLASS_RE = re.compile(r"^[a-z_]{1,40}$")
UPDATE_CLASS = "update_delivered"
DEADLINES_CLASS = "deadlines"
VERIFY_RE = re.compile(r"^vfy_[0-9A-HJKMNP-TV-Z]{26}$")


def lane_key(lane: str, envelope: dict[str, Any]) -> str:
    """The kind of job an envelope asks for. A drafting envelope with no
    readable class is ``document-drafter.unknown``: never proven, so held."""
    skill = SKILLS.get(lane)
    if skill is None:
        return f"unknown-lane.{re.sub(r'[^a-z_]', '_', lane.lower())[:40] or 'none'}"
    if lane == "drafting":
        cls = str(envelope.get("document_class") or "")
        return f"{skill}.{cls if _CLASS_RE.match(cls) else 'unknown'}"
    if lane == "litigation" and envelope.get("report") == "deadlines":
        # 2026-10-15: the deadlines report is a different job from the status
        # list (no model, two workbooks, another folder), so it waits for its
        # own proof run on the seat, never riding the status list's.
        return f"{skill}.{DEADLINES_CLASS}"
    if lane == "medchron" and isinstance(envelope.get("update"), dict):
        # 2026-10-13: an update INTO the delivered chronology is its own kind of
        # job (it reads the delivered document and deletes the one it replaces),
        # so it waits for its own proof run on the seat, never riding the run's.
        return f"{skill}.{UPDATE_CLASS}"
    return skill


@dataclass
class Proofs:
    """The effective proven set; ``problems`` says what could not be read."""

    proven: dict[str, dict[str, Any]] = field(default_factory=dict)
    revoked: dict[str, str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def is_proven(self, key: str) -> bool:
        return key in self.proven and key not in self.revoked


def _authored(path: Path, out: Proofs) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        out.problems.append(f"{path.name} absent")
        return {}
    try:
        import yaml

        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        lanes = doc.get("lanes") or {}
        revoked = doc.get("revoked") or []
        if not isinstance(lanes, dict) or not isinstance(revoked, list):
            raise ValueError("lanes must be a mapping and revoked a list")
    except Exception as exc:  # noqa: BLE001 - an unreadable seed proves nothing, said once per read
        out.problems.append(f"{path.name} unreadable: {type(exc).__name__}")
        return {}
    for item in revoked:
        key = str((item or {}).get("key") or "") if isinstance(item, dict) else ""
        if KEY_RE.match(key):
            out.revoked[key] = str(item.get("why") or "")
    return {k: dict(v or {}, source="authored") for k, v in lanes.items() if KEY_RE.match(str(k))}


def read_events(run_dir: Path) -> list[dict[str, Any]]:
    path = Path(run_dir) / RUNTIME_NAME
    if not path.is_file():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    events = doc.get("events") if isinstance(doc, dict) else None
    if not isinstance(events, list):
        raise ValueError(f"{RUNTIME_NAME} carries no events list")
    return [e for e in events if isinstance(e, dict)]


def load(run_dir: Path) -> Proofs:
    out = Proofs()
    proven = _authored(Path(run_dir) / AUTHORED_NAME, out)
    try:
        events = read_events(Path(run_dir))
    except Exception as exc:  # noqa: BLE001 - see the module note: unreadable proves nothing
        out.problems.append(f"{RUNTIME_NAME} unreadable: {type(exc).__name__}")
        return out
    for e in events:
        key = str(e.get("key") or "")
        if not KEY_RE.match(key):
            continue
        if e.get("event") == "proven":
            proven[key] = {**e, "source": "runtime"}
        elif e.get("event") == "revoked":
            proven.pop(key, None)
    out.proven = proven
    return out


def append_event(run_dir: Path, event: dict[str, Any]) -> dict[str, Any]:
    """Append one runtime event, atomically, root-owned 0644. Root only: the
    file's directory is the medchron tree, which no other uid can write."""
    run_dir = Path(run_dir)
    events = read_events(run_dir)
    row = {**event, "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    events.append(row)
    tmp = run_dir / f".{RUNTIME_NAME}.tmp"
    tmp.write_text(json.dumps({"events": events}, indent=1) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o644)
    tmp.replace(run_dir / RUNTIME_NAME)
    return row


# ---------------------------------------------------------------------------
# The hold at claim
# ---------------------------------------------------------------------------


def _hold_path(d: Any, job_id: str) -> Path:
    return Path(d.run_dir) / HOLDS_DIR / f"{d.LANE}-{job_id}.json"


def holds(run_dir: Path) -> list[dict[str, Any]]:
    """Every job currently waiting on its lane's proof, oldest first."""
    folder = Path(run_dir) / HOLDS_DIR
    if not folder.is_dir():
        return []
    out = []
    for p in sorted(folder.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda h: float(h.get("held_at") or 0))


def admits(d: Any, job_id: str, env_path: Path, row: dict[str, Any]) -> bool:
    """True when the daemon may claim this envelope now. Records the hold once,
    and clears it once, through the lane's own broker ``record``."""
    try:
        env = json.loads(env_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        env = {}
    key = lane_key(d.LANE, env if isinstance(env, dict) else {})
    proofs = load(Path(d.run_dir))
    marker = _hold_path(d, job_id)
    state = str(row.get("state") or "")
    if proofs.is_proven(key):
        if marker.is_file():
            try:
                d.broker.record(job_id, state, {"reason": ""})
            except Exception as exc:  # noqa: BLE001 - the note must be gone before the job runs; next tick retries
                logger.warning("could not clear the lane hold on %s, not claiming yet: %s", job_id, exc)
                return False
            marker.unlink(missing_ok=True)
            logger.info("%s job %s released: lane %s is proven", d.LANE, job_id, key)
        return True
    if not marker.is_file():
        why = "; ".join(proofs.problems) or ("revoked" if key in proofs.revoked else "never proven on this seat")
        try:
            d.broker.record(job_id, state, {"reason": f"{HOLD_PREFIX}{key}"})
        except Exception as exc:  # noqa: BLE001 - retried next tick; the job is not claimed either way
            logger.warning("could not note the lane hold on %s: %s", job_id, exc)
            return False
        marker.parent.mkdir(mode=0o700, exist_ok=True)
        marker.write_text(
            json.dumps({"lane": d.LANE, "job_id": job_id, "key": key, "why": why, "held_at": d.clock()}),
            encoding="utf-8",
        )
        logger.warning("%s job %s HELD: lane %s is not proven on this seat (%s)", d.LANE, job_id, key, why)
    return False


def validate(path: Path) -> list[str]:
    """Problems with an authored ``lane-proofs.yaml``; empty when it is good.
    The provisioner refuses to stage a file with any: a typo in a key would
    otherwise hold a lane the author meant to prove, silently."""
    import yaml

    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        return [f"unreadable: {type(exc).__name__}: {exc}"]
    if not isinstance(doc, dict):
        return ["the file is not a mapping"]
    problems = [f"unknown top-level key {k!r}" for k in doc if k not in ("schema", "lanes", "revoked")]
    if doc.get("schema") != 1:
        problems.append("schema must be 1")
    lanes, revoked = doc.get("lanes"), doc.get("revoked")
    if not isinstance(lanes, dict):
        return [*problems, "lanes must be a mapping (use {} for none)"]
    for key, entry in lanes.items():
        if not KEY_RE.match(str(key)):
            problems.append(f"lane key {key!r} is not a lane key")
        if not isinstance(entry, dict) or not str(entry.get("evidence") or "").strip():
            problems.append(f"{key}: every proven lane names its evidence")
    if not isinstance(revoked, list):
        return [*problems, "revoked must be a list (use [] for none)"]
    for item in revoked:
        if not isinstance(item, dict) or not KEY_RE.match(str(item.get("key") or "")) or not item.get("why"):
            problems.append(f"revoked entry {item!r} needs a lane key and a why")
    return problems


__all__ = [
    "AUTHORED_NAME",
    "HOLD_PREFIX",
    "KEY_RE",
    "Proofs",
    "RUNTIME_NAME",
    "SKILLS",
    "VERIFY_RE",
    "admits",
    "append_event",
    "holds",
    "lane_key",
    "load",
    "read_events",
    "validate",
]


if __name__ == "__main__":  # the provisioner's check: python -m medchron.lane_proof <file>
    import sys

    found = validate(Path(sys.argv[1]))
    for p in found:
        print(f"{sys.argv[1]}: {p}", file=sys.stderr)
    sys.exit(1 if found else 0)
