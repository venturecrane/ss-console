"""Is each Smokeball seat's sign-in alive, and is anything checking it?

The hourly connector-auth-check pre_run is the ONLY detector of a dead
Smokeball token: the connector-health ledger records agent MCP calls and the
Graph client, never the Python-connector runners (drafting, negotiation watch,
litigation), so A&P's heartbeat carries no Smokeball key at all (PR #3144).

THE OUTCOMES, KEPT APART (2026-10-13 review): a false "sign-in is dead" page
is worse than none, so the dead-token PAGE fires only on an explicit refusal
from Smokeball's token endpoint (a 4xx on the refresh grant: invalid_grant and
friends). An UNSAVED token (the refresh worked, the rotated token never reached
the durable file) also pages, with its own words: sign-in works now and dies
at the seat's next restart, so it must be fixed the same day (ADR 0080
amendment; until 2026-10-17 it read as could-not-run and paged nobody, review
DS1). A check that could not run (venv missing, timeout, import error,
network) is a "could not run" item carrying the error, never "dead". Failures
seen only second-hand (a job's reason naming an auth error) are an item, not a
page, because they cannot tell a refusal from a transport fault.

The pre_run's decision bases (``operator/skills/connector-auth-check/pre_run.py``):
    connector_auth_ok:smokeball
    connector_auth_refused:smokeball:<http_4xx>          -> the page (dead)
    connector_auth_unsaved:smokeball:refresh_token_not_persisted
                                                         -> the page (dies at restart)
    connector_auth_unchecked:smokeball:<ErrorClass>      -> could not run
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

from bin.lib.work_health import _JOB_TYPE, Config, Item, hhmm, item, meta, stamped

AUTH_PROBE_SKILL = "connector-auth-check"
OK, REFUSED, UNSAVED, UNCHECKED, SECONDHAND = "ok", "refused", "unsaved", "unchecked", "secondhand"
_AUTH_WORDS = re.compile(r"auth(entication)?error|invalid_grant|unauthori[sz]ed|\b401\b", re.IGNORECASE)


def _probe_outcome(basis: str) -> Optional[str]:
    if basis.startswith("connector_auth_ok"):
        return OK
    if basis.startswith("connector_auth_refused"):
        return REFUSED
    if basis.startswith("connector_auth_unsaved"):
        return UNSAVED
    if basis.startswith(("connector_auth_unchecked", "connector_auth_failed")):
        # connector_auth_failed: the first build's basis, which did not tell a
        # refusal from a check that could not run. Read as could-not-run.
        return UNCHECKED
    return None


def evidence(rows: list[dict]) -> list[tuple[datetime, str, str]]:
    """(when, outcome, detail) for every row that says whether Smokeball took
    the seat's sign-in, oldest first."""
    out = []
    for ts, row in stamped(rows):
        action = str(row.get("action_type") or "")
        md = meta(row)
        if action == "SUPPRESSED_WAKE" and row.get("skill_name") == AUTH_PROBE_SKILL:
            outcome = _probe_outcome(str(md.get("decision_basis") or ""))
            if outcome:
                detail = str(md.get("error_detail") or md.get("decision_basis") or "")[:160]
                out.append((ts, outcome, detail))
        elif action == "TOOL_CALL_COMPLETED" and str(md.get("tool") or "").startswith("mcp_smokeball_"):
            if md.get("outcome") == "ok":
                out.append((ts, OK, "a Smokeball call"))
            elif _AUTH_WORDS.search(str(md.get("error_type") or "")):
                out.append((ts, SECONDHAND, f"{md.get('tool')} failed ({md.get('error_type')})"))
        elif _JOB_TYPE.match(action) and _AUTH_WORDS.search(str(md.get("reason") or "")):
            out.append((ts, SECONDHAND, f"a {action.split('_JOB_')[0].lower()} job failed on sign-in"))
    return out


def smokeball_auth_items(
    seat: str, rows: list[dict], cfg: Config, now: datetime, *, probe_scheduled: bool
) -> list[Item]:
    """Items from the trailing run of non-ok evidence (an ok clears it)."""
    all_evidence = evidence(rows)
    trail: list[tuple[datetime, str, str]] = []
    for ev in all_evidence:
        trail = [] if ev[1] == OK else [*trail, ev]
    out: list[Item] = []
    refused = [ev for ev in trail if ev[1] == REFUSED]
    unsaved = [ev for ev in trail if ev[1] == UNSAVED]
    if refused:
        since = refused[0][0]
        out.append(
            item(
                seat,
                "auth_failed",
                f"smokeball:refused:{since.isoformat()}",
                since,
                f"Smokeball REFUSED this seat's sign-in at {hhmm(refused[-1][0])} ({refused[-1][2]}), first at "
                f"{hhmm(since)}; every Smokeball read and write fails until it is re-consented.",
                "Re-consent the Smokeball connection for this seat (memory: smokeball token program).",
                page=f"ACTION: {seat} Smokeball sign-in is dead",
            )
        )
    elif unsaved:
        since = unsaved[0][0]
        out.append(
            item(
                seat,
                "auth_failed",
                f"smokeball:unsaved:{since.isoformat()}",
                since,
                f"Smokeball renewed this seat's sign-in but the new token was NOT saved at {hhmm(unsaved[-1][0])}, "
                f"first at {hhmm(since)}; sign-in works now and every Smokeball read and write fails after the "
                "seat's next restart.",
                "Fix it before the seat restarts: check the durable token file is writable, then re-consent the "
                "Smokeball connection for this seat (memory: smokeball token program).",
                page=f"ACTION: {seat} Smokeball sign-in dies at the next restart",
            )
        )
    elif trail and trail[-1][1] == UNCHECKED:
        ts, _o, detail = trail[-1]
        out.append(
            item(
                seat,
                "auth_unchecked",
                "smokeball:check-could-not-run",
                trail[0][0],
                f"The hourly Smokeball sign-in check could not run at {hhmm(ts)} ({detail}); whether sign-in works is unknown.",
                "Fix the check on the seat (connector venv, broker, network); this is not a dead token.",
            )
        )
    elif trail:
        ts, _o, detail = trail[-1]
        out.append(
            item(
                seat,
                "auth_unchecked",
                f"smokeball:secondhand:{trail[0][0].isoformat()}",
                trail[0][0],
                f"Smokeball work failed on sign-in at {hhmm(ts)} ({detail}) and nothing has succeeded since.",
                "Check the seat's Smokeball connection; the hourly check (where it runs) says whether the token is refused.",
            )
        )
    probes = [ts for ts, outcome, _d in all_evidence if outcome in (OK, REFUSED, UNSAVED, UNCHECKED)]
    stale = timedelta(minutes=cfg.auth_probe_stale_minutes)
    if probe_scheduled and (not probes or now - probes[-1] > stale):
        last = f"since {hhmm(probes[-1])}" if probes else "in the window read"
        out.append(
            item(
                seat,
                "auth_unchecked",
                "smokeball:probe-silent",
                (probes[-1] + stale) if probes else now,
                f"ACTION: the hourly Smokeball sign-in check has not reported {last}; a dead token would go unseen.",
                "Check the seat's connector-auth-check schedule; a silent check is a dead detector.",
            )
        )
    return out


def probe_not_scheduled_items(seat: str, now: datetime, *, why: Optional[str]) -> list[Item]:
    """A Smokeball seat whose hourly check is not scheduled on the Machine is an
    ACTION item, never a quiet skip (``why`` None = it is scheduled)."""
    if why is None:
        return []
    return [
        item(
            seat,
            "auth_unchecked",
            "smokeball:probe-not-scheduled",
            now,
            f"ACTION: Smokeball sign-in is NOT checked hourly on this seat: {why}. "
            "A dead token shows only when work fails on it.",
            "Rebuild the seat (or author the row) so the hourly check is scheduled.",
        )
    ]
