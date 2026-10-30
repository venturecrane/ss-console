"""`fleet status`: what each seat runs, from D1, in one table (or --json).

Per seat: ring, hold, image (and the release it is), overlay ref, skills
version, config digest, jobs (running / queued / wakes pending), heartbeat age,
the last rollout's state, DRIFT against the newest release that completed
its last ring, and whether the identifier-gate rollback lever is set. NULL means
unknown and is shown as "?", never as 0.

The lever (`SMD_IDENTIFIER_GATE_MODE`) turns off all identifier blocking on a
seat. ADR 0093 keeps it only as an incident-time, fleet-wide rollback, so a seat
carrying it is named here and in boot smoke. Read from Fly secret NAMES only;
values never leave Fly.
"""

from __future__ import annotations

import json
from typing import Any

from . import ids
from .d1 import FleetStore
from .rings import Ring


def _age(ts: str | None) -> str:
    moment = ids.parse_iso(ts)
    if moment is None:
        return "?"
    secs = int((ids.utc_now() - moment).total_seconds())
    return f"{secs}s" if secs < 120 else f"{secs // 60}m" if secs < 7200 else f"{secs // 3600}h"


def _lever(value: bool | None) -> str:
    return "?" if value is None else "SET" if value else "-"


def _q(value: Any) -> str:
    return "?" if value is None else str(value)


LEVER = "SMD_IDENTIFIER_GATE_MODE"


def lever_flags(fly: Any, seats: list[str]) -> dict[str, bool | None]:
    """Per seat: is the identifier-gate lever a Fly secret (deployed or staged)?

    None when the secret list cannot be read: unknown is never reported as unset.
    """
    out: dict[str, bool | None] = {}
    for seat in seats:
        try:
            deployed, staged = fly.secret_names(f"hermes-{seat}")
        except Exception:  # noqa: BLE001 - one unreadable seat must not hide the others
            out[seat] = None
            continue
        out[seat] = LEVER in deployed or LEVER in staged
    return out


def seat_rows(
    store: FleetStore, rings: list[Ring], levers: dict[str, bool | None] | None = None
) -> list[dict[str, Any]]:
    status = store.fleet_status()
    holds = store.holds()
    last = store.last_rollouts()
    latest_img = store.latest_completed("image") or {}
    latest_skl = store.latest_completed("skills") or {}
    rows = []
    for ring in rings:
        for seat in ring.seats:
            st = status.get(seat) or {}
            image = st.get("image_ref")
            drift = []
            if (
                latest_img
                and image
                and not ids.image_matches(image, latest_img["image_ref"], latest_img.get("image_digest"))
            ):
                drift.append("image")
            if latest_skl and st.get("skills_version") and st.get("skills_version") != latest_skl.get("skills_version"):
                drift.append("skills")
            rows.append({
                "seat": seat, "ring": ring.number, "hold": (holds.get(seat) or {}).get("reason"),
                "image_ref": image, "overlay": st.get("version"), "skills_version": st.get("skills_version"),
                "config_digest": st.get("config_digest"), "jobs_running": st.get("jobs_running"),
                "jobs_queued": st.get("jobs_queued"), "wakes_pending": st.get("wakes_pending"),
                "heartbeat_ts": st.get("last_heartbeat_ts"), "heartbeat_status": st.get("heartbeat_status"),
                "last_rollout": (last.get(seat) or {}).get("state"), "drift": drift,
                "identifier_gate_lever": (levers or {}).get(seat),
            })  # fmt: skip
    return rows


def render_table(rows: list[dict[str, Any]]) -> str:
    head = [
        "seat",
        "ring",
        "hold",
        "image",
        "overlay",
        "skills",
        "config",
        "jobs r/q/w",
        "beat",
        "last rollout",
        "drift",
        "id-lever",
    ]
    lines = [head]
    for r in rows:
        image = (r["image_ref"] or "?").rsplit("/", 1)[-1]
        lines.append([
            r["seat"], str(r["ring"]), "HELD" if r["hold"] else "", image[:40], (r["overlay"] or "?")[:8],
            _q(r["skills_version"]), (r["config_digest"] or "?")[:10],
            f"{_q(r['jobs_running'])}/{_q(r['jobs_queued'])}/{_q(r['wakes_pending'])}",
            f"{_age(r['heartbeat_ts'])} {r['heartbeat_status'] or ''}".strip(), _q(r["last_rollout"]),
            ",".join(r["drift"]) or "-", _lever(r.get("identifier_gate_lever")),
        ])  # fmt: skip
    widths = [max(len(row[i]) for row in lines) for i in range(len(head))]
    return "\n".join("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in lines)


def render_holds(holds: dict[str, dict[str, Any]]) -> str:
    """Each hold with who set it, when, and why, so nobody waits on a hold no one can name."""
    if not holds:
        return "\nholds: none"
    out = ["", "holds:"]
    for seat in sorted(holds):
        h = holds[seat]
        out.append(f"  {seat}: held {h.get('held_at') or '?'} by {h.get('held_by') or '?'}")
        out.append(f"    {h.get('reason') or '(no reason recorded)'}")
    return "\n".join(out)


def render_releases(rows: list[dict[str, Any]], as_json: bool) -> str:
    if as_json:
        return json.dumps(rows, indent=1, default=str)
    out = []
    for r in rows:
        what = r.get("image_ref") if r.get("kind") == "image" else f"skills {r.get('skills_version')}"
        done = r.get("completed_last_ring_at") or "not completed"
        out.append(f"{r['release_id']}  {r.get('kind')}  {what}  created {r.get('created_at')}  completed {done}")
    return "\n".join(out) or "(no releases)"
