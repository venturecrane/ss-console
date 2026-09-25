"""Readiness rows for the deadline digest's plain-word replies.

The digest numbers its items and invites a reply ("got it on 1"). Two authored
states break that silently:

* a recipient who is not on ``scope.inbound_allow_from`` replies, the seat
  refuses to read the reply, and the item keeps firing;
* a digest sent under ``confirm`` or ``draft_for_review`` goes out with no
  ``fired`` rows behind it, so every number in it resolves to nothing.

Both rows are blocking only on a seat that authors the escalator's cron,
because only that routine asks for a reply. Elsewhere the recipient row is
reported as INFO, so an ungranted address is still visible.

The case-manager routines (``task-list-keeper``, ``date-prep-brief``; spec
``docs/specs/operator/case-manager-deadline-work.md``) add rows only on a seat
whose cron arms one of them, so a seat that arms neither reads as before:

* an armed routine whose ``case_manager`` job is not authored does nothing on
  every tick, forever, and says so only in a heartbeat basis nobody reads;
* the date-prep brief goes to the matter's own staff and nowhere else, so it
  needs ``escalation.case_alert_routing.mode: matter_staff``;
* a proposal or brief held for approval or drafted for review goes out with
  no raise rows behind its numbers, exactly the digest's failure.

Split out of ``seat-readiness.py`` (module-size ceiling). Returns row dicts in
that script's ``Row`` field order; the script adds them to its report. The
grant test is the escalator's own ``routing._granted``, loaded by path, so
readiness and the digest decide "granted" with one function.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ESCALATOR = "deadline-miss-escalator"
#: Postures under which the digest is delivered with no ``fired`` rows (held
#: for approval, or a draft a person sends).
UNROWED_POSTURES = ("confirm", "draft_for_review")


def _escalator_routing(repo_root: Path):
    spec = importlib.util.spec_from_file_location(
        "readiness_escalator_routing", repo_root / "operator" / "skills" / ESCALATOR / "routing.py"
    )
    if spec is None or spec.loader is None:
        raise SystemExit("FATAL: cannot load the deadline-miss-escalator routing module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _scheduled_personas(cfg: dict) -> list[dict]:
    """Personas whose cron authors the escalator with a non-blank schedule."""
    return [
        p
        for p in (cfg.get("personas") or [])
        if isinstance(p, dict)
        and any(
            isinstance(c, dict) and c.get("skill") == ESCALATOR and str(c.get("schedule") or "").strip()
            for c in (p.get("cron") or [])
        )
    ]


#: Each case-manager routine and the ``case_manager`` sub-blocks that give it work.
CASEWORK_ROUTINES: dict[str, tuple[str, ...]] = {
    "task-list-keeper": ("own_tasks", "task_cleanup", "quiet"),
    "date-prep-brief": ("date_prep",),
}


def _armed(cfg: dict, skill: str) -> list[dict]:
    """Personas whose cron arms ``skill`` with a non-blank schedule."""
    return [
        p
        for p in (cfg.get("personas") or [])
        if isinstance(p, dict)
        and any(
            isinstance(c, dict) and c.get("skill") == skill and str(c.get("schedule") or "").strip()
            for c in (p.get("cron") or [])
        )
    ]


def _row(check: str, failed: str | None, passed: str, falsifier: str) -> dict:
    return {
        "section": "deadline replies",
        "check": check,
        "status": "FAIL" if failed else "PASS",
        "detail": failed or passed,
        "falsifier": falsifier,
        "blocker": True,
    }


def _unrowed(personas: list[dict]) -> list[str]:
    return sorted(
        {
            f"{p.get('name', '?')}: {posture}"
            for p in personas
            if (posture := ((p.get("entitlements") or {}).get("exposure") or {}).get("external_send_internal"))
            in UNROWED_POSTURES
        }
    )


def casework_rows(cfg: dict) -> list[dict]:
    """Rows for the case-manager routines this seat's cron arms. None armed, no rows."""
    armed = {skill: personas for skill in CASEWORK_ROUTINES if (personas := _armed(cfg, skill))}
    if not armed:
        return []
    block = cfg.get("case_manager")
    block = block if isinstance(block, dict) else {}
    idle = [s for s in armed if not any(isinstance(block.get(job), dict) for job in CASEWORK_ROUTINES[s])]
    out = [
        _row(
            "every armed case-manager routine has a job authored",
            f"armed with no case_manager job: {', '.join(idle)}" if idle else None,
            "each armed routine has its case_manager job authored",
            "a routine armed on cron whose every tick suppresses because its job is off, reported as live",
        )
    ]
    if "date-prep-brief" in armed:
        esc = cfg.get("escalation")
        routing = esc.get("case_alert_routing") if isinstance(esc, dict) else None
        mode = routing.get("mode") if isinstance(routing, dict) else None
        out.append(
            _row(
                "the date-prep brief reaches the matter's own staff",
                None if mode == "matter_staff" else f"escalation.case_alert_routing.mode is {mode or 'unauthored'}",
                "escalation.case_alert_routing.mode is matter_staff",
                "a brief with no matter-staff routing counted as deliverable",
            )
        )
    unrowed = _unrowed([p for personas in armed.values() for p in personas])
    out.append(
        _row(
            "case-manager messages send with rows behind their numbers",
            f"external_send_internal is {'; '.join(unrowed)}, so a proposal or brief goes out with no raise rows"
            if unrowed
            else None,
            "external_send_internal on the case-manager persona is neither confirm nor draft_for_review",
            "a proposal held or drafted for review, reported as answerable",
        )
    )
    return out


def rows(cfg: dict, repo_root: Path) -> list[dict]:
    scheduled = _scheduled_personas(cfg)
    esc = cfg.get("escalation")
    esc = esc if isinstance(esc, dict) else {}
    routing_block = esc.get("case_alert_routing")
    routing_block = routing_block if isinstance(routing_block, dict) else {}
    recipients = list(esc.get("red_flag_recipients") or []) + list(routing_block.get("fallback_recipients") or [])
    grants = list((cfg.get("scope") or {}).get("inbound_allow_from") or [])
    granted = _escalator_routing(repo_root)._granted
    ungranted = sorted({str(r) for r in recipients if not granted(r, grants)})
    out = [
        {
            "section": "deadline replies",
            "check": "every deadline recipient may reply",
            "status": ("FAIL" if ungranted else "PASS") if scheduled else "INFO",
            "detail": (
                f"not on scope.inbound_allow_from: {', '.join(ungranted)}" if ungranted else "every recipient granted"
            )
            + ("" if scheduled else " (no deadline digest authored; not blocking)"),
            "falsifier": "a red_flag or fallback recipient whose reply the seat would refuse, counted as granted",
            "blocker": bool(scheduled),
        }
    ]
    if not scheduled:
        return out + casework_rows(cfg)
    unrowed = [
        f"{p.get('name', '?')}: {posture}"
        for p in scheduled
        if (posture := ((p.get("entitlements") or {}).get("exposure") or {}).get("external_send_internal"))
        in UNROWED_POSTURES
    ]
    out.append(
        {
            "section": "deadline replies",
            "check": "deadline digest sends with rows behind its numbers",
            "status": "FAIL" if unrowed else "PASS",
            "detail": f"external_send_internal is {'; '.join(unrowed)}, so the digest goes out with no fired rows"
            if unrowed
            else "external_send_internal on the digest persona is neither confirm nor draft_for_review",
            "falsifier": "a digest held or drafted for review, reported as answerable",
            "blocker": True,
        }
    )
    return out + casework_rows(cfg)
