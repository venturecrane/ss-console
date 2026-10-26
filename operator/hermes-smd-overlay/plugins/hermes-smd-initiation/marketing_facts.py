"""The ``marketing`` section of ``operator_seat_facts`` (ADR 0098, ADR 0099).

WHY IT EXISTS. A marketing seat's skills need three things no file tool can
give them on that seat. The record-store path fence
(``hermes-smd-record-store/path_fence.py``) lets a file tool read only skill
roots and specs, so ``read_file`` of the staged registers
(``/var/lib/smd-config/marketing/marketing-firm.yaml``) is refused, and so is
the sealed reader's research log. And a scheduled run that researched through
``web_research`` may email only the seat's exact ``scope.admins``, which
nothing else on the seat names. This section carries all three, read by code
from the authored sources, so the fence stays as narrow as it is and no
second copy of the config has to be authored anywhere.

WHAT IT CARRIES, and nothing more:

* ``admins``: the exact ``scope.admins`` addresses (``CustomerConfig.admins``,
  which drops domain grants and fails closed to ``[]``). The trust gate checks
  every recipient against the same list in code; this is the model's copy,
  never an authority.
* from the registers, parsed and validated by the advertising gate's own
  loader (so the skills and the gate can never read the file differently):
  the firm's names, office cities, site urls and phones; the roster's names
  and titles (with the lawyer flag and languages the drafting rules need); the
  disclosure sentences; each channel's ``identity_in_template`` flag; and, for
  the drafting rules that read them, ``fee_terms`` and the review-reply offline
  contact. Results, testimonials, awards, certifications and the blocklist
  stay with the gate: a draft never needs to quote them from here.
* ``research_questions``: the sealed reader's logged questions (question text
  and date only, never counts or anything fetched) for this calendar month and
  the one before, oldest first, at most the last 50. The previous month is
  included because the monthly analytics note runs a few days into a month and
  reports the month just ended.

FAIL-CLOSED. Registers missing, unreadable or malformed: ``available: false``
with the reason, and no register fields at all (never a partial or guessed
set). ``admins`` and ``research_questions`` are still reported, since neither
comes from the registers. Only a seat whose ``seat.role`` is ``marketing``
gets this section; every other seat's envelope is unchanged.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ROLE = "marketing"
SECTION = "marketing"
MAX_QUESTIONS = 50

#: Mirrors ``shared.sealed_reader`` (DEFAULT_LOG_PATH, LOG_PATH_ENV); pinned by
#: test. Literals so reading the facts never imports the reader's fetch stack.
DEFAULT_RESEARCH_LOG = "/opt/data/research/research-log.jsonl"
RESEARCH_LOG_ENV = "SMD_RESEARCH_LOG"

RegistersLoader = Callable[[], tuple[dict | None, str | None]]


def is_marketing_seat(cfg: Any) -> bool:
    raw = getattr(cfg, "raw", None)
    seat = raw.get("seat") if isinstance(raw, dict) else None
    return isinstance(seat, dict) and seat.get("role") == ROLE


def _admins(cfg: Any) -> list[str]:
    try:
        raw = cfg.admins
    except Exception:  # noqa: BLE001 - an unreadable list is no admins
        return []
    if not isinstance(raw, (list, tuple)):
        return []
    return [a for a in raw if isinstance(a, str) and "@" in a and not a.startswith("@")]


def _default_registers_loader() -> tuple[dict | None, str | None]:
    """``(normalized registers, None)`` or ``(None, reason)`` via the gate's own loader."""
    from shared import advertising_gate

    try:
        checker = advertising_gate._load_checker()
    except Exception as exc:  # noqa: BLE001 - no loader is unavailable, never a guess
        return (
            None,
            f"the advertising check is not installed on this seat ({exc.__class__.__name__})",
        )
    raw, error = checker.load_registers(advertising_gate.registers_path())
    if error:
        return None, error
    try:
        return checker.normalize_registers(raw), None
    except Exception as exc:  # noqa: BLE001 - RegistersError and anything else fail closed
        return None, f"the registers are malformed ({exc})"


def _register_fields(reg: dict) -> dict[str, Any]:
    firm = reg.get("firm") or {}
    return {
        "firm": {
            k: list(firm.get(k) or []) for k in ("names", "office_cities", "site_urls", "phones")
        },
        "roster": [
            {
                "name": row.get("name"),
                "title": row.get("title"),
                "lawyer": bool(row.get("lawyer")),
                "languages": list(row.get("languages") or []),
            }
            for row in reg.get("roster") or []
        ],
        "disclosures": dict(reg.get("disclosures") or {}),
        "channels": {
            ch: {"identity_in_template": bool((flags or {}).get("identity_in_template"))}
            for ch, flags in (reg.get("channels") or {}).items()
        },
        "fee_terms": dict(reg.get("fee_terms") or {}),
        "review_reply_contact": dict(reg.get("offline_contact") or {}),
    }


def _previous_month_key(dt: datetime) -> str:
    year, month = (dt.year, dt.month - 1) if dt.month > 1 else (dt.year - 1, 12)
    return f"{year:04d}-{month:02d}"


def research_questions(path: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    """This month's and last month's logged questions: ``{read, months, items[, reason]}``."""
    log = Path(path or os.environ.get(RESEARCH_LOG_ENV) or DEFAULT_RESEARCH_LOG)
    now = now or datetime.now(timezone.utc)
    months = sorted({now.strftime("%Y-%m"), _previous_month_key(now)})
    if not log.exists():
        return {"read": True, "months": months, "items": []}
    try:
        lines = log.read_text(encoding="utf-8").splitlines()
    except Exception as exc:  # noqa: BLE001 - reported, never guessed
        return {"read": False, "months": months, "items": [], "reason": exc.__class__.__name__}
    items: list[dict[str, str]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        ts, question = row.get("ts"), row.get("question")
        if not isinstance(ts, str) or not isinstance(question, str) or len(ts) < 10:
            continue
        if ts[:7] not in months:
            continue
        items.append({"date": ts[:10], "question": question})
    return {"read": True, "months": months, "items": items[-MAX_QUESTIONS:]}


def build(
    cfg: Any,
    *,
    registers_loader: RegistersLoader | None = None,
    research_log: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The section for a marketing seat. Never raises."""
    admins = _admins(cfg)
    out: dict[str, Any] = {"admins": admins}
    try:
        registers, reason = (registers_loader or _default_registers_loader)()
    except Exception as exc:  # noqa: BLE001
        registers, reason = None, f"the registers could not be read ({exc.__class__.__name__})"
    if registers is None:
        out.update(available=False, reason=reason or "the registers could not be read")
    else:
        try:
            out.update(_register_fields(registers))
            out["available"] = True
        except Exception as exc:  # noqa: BLE001 - a malformed shape is unavailable, never partial
            logger.warning("operator_seat_facts: marketing registers unusable (%s)", exc)
            out = {"admins": admins, "available": False, "reason": "the registers are malformed"}
    try:
        out["research_questions"] = research_questions(research_log, now)
    except Exception as exc:  # noqa: BLE001
        out["research_questions"] = {
            "read": False,
            "months": [],
            "items": [],
            "reason": exc.__class__.__name__,
        }
    return out
