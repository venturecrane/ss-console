#!/usr/bin/env python3
"""Email team@smd.services about the state of the WORK on every live seat.

WHY (Captain, 2026-10-13). Machine health is paged within minutes; the work was
not watched at all. A job stuck in "running" looked like progress forever, the
seat's NO REPLY card switched off once any job existed and had never fired in
production, the daily reconcilers wrote to GitHub issues nobody read, and an
idle dead Smokeball token stayed silent for 42 hours. This run reads every live
seat and emails the work that needs a person, in plain language.

CONSOLE-SIDE, like the reconcilers it folds in (a seat cannot audit its own
silence): the seat's ledger via the ADR 0043 runtime-read seam, the console's
records via D1, Graph sign-in via SMD's read credential. Rules for what counts
live in ``operator/contracts/work-health.yaml``; the item logic in
``bin/lib/work_health*.py``.

A FAILED READ IS AN ITEM, never an empty result: a seat that could not be read
appears in the email as such, so it can never produce an all-clear.

THE INTERVAL is the workflow's cron line (``.github/workflows/work-health.yml``).
``--since`` (the previous successful run's start) and ``--state-in`` (its item
keys) make "new" vs "still open" exact without this script knowing the period.

EXIT: 0 evaluated (sent, or nothing to send); 2 the email could not be sent;
3 HOLD, nothing could be evaluated (no credentials).

Usage:
    infisical run --env=prod --path=/ss -- python3 operator/bin/work-health.py --dry-run
    ... --since 2026-10-13T15:23:00Z --state-in prev.json --state-out next.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml

from bin.lib import console_d1, msgraph_channel, seam_pull
from bin.lib import work_health as wh
from bin.lib import work_health_auth as auth
from bin.lib import work_health_mail as mail
from bin.lib import work_health_sources as src

EXIT_OK = 0
EXIT_SEND_FAILED = 2
EXIT_HOLD = 3

CUSTOMERS = Path(__file__).resolve().parents[1] / "customers"
RESEND_URL = "https://api.resend.com/emails"
DEFAULT_TO = "team@smd.services"
DEFAULT_FROM = "SMD Services Ops <team@smd.services>"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid_floor(ts: datetime) -> str:
    """The smallest ULID at ``ts``: audit_export pages ``id > cursor`` in ULID
    (= time) order, so this reads only the window instead of the whole ledger."""
    ms = int(ts.timestamp() * 1000)
    out = ""
    for _ in range(10):
        out = _CROCKFORD[ms % 32] + out
        ms //= 32
    return out + "0" * 16


def read_ledger(client, since: datetime) -> list[dict]:
    """The window's rows. An empty window is checked against the ledger's
    first page: a seat with rows somewhere is quiet, a seat with none at all
    (the seam serves an empty page on a database error) is UNREADABLE."""
    rows: list[dict] = []
    cursor: Optional[str] = ulid_floor(since)
    while cursor:
        page = client.read_page("audit_export", cursor=cursor)
        rows.extend(e for e in page["entries"] if isinstance(e, dict))
        cursor = page.get("cursor")
    if not rows and not client.read_page("audit_export")["entries"]:
        raise RuntimeError("the seat's ledger read back empty")
    return rows


def customer_yaml(slug: str) -> dict:
    try:
        data = yaml.safe_load((CUSTOMERS / slug / "customer.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def seat_product(config: dict) -> Optional[str]:
    """``seat.product`` from the seat's customer.yaml: operator or hosted-agent."""
    seat = config.get("seat") if isinstance(config, dict) else None
    product = seat.get("product") if isinstance(seat, dict) else None
    return str(product) if product else None


def seat_shape(config: dict) -> tuple[bool, Optional[str]]:
    """(has_smokeball, the hourly check's authored schedule or None) from git."""
    connectors = config.get("connectors") or {}
    has_smokeball = any(
        isinstance(c, dict) and str(c.get("backend") or "").endswith("smokeball") for c in connectors.values()
    )
    schedule = None
    for persona in config.get("personas") or []:
        for row in (persona or {}).get("cron") or []:
            if isinstance(row, dict) and row.get("skill") == auth.AUTH_PROBE_SKILL and row.get("pre_run"):
                schedule = str(row.get("schedule") or "")
    return has_smokeball, schedule


def probe_not_scheduled_why(
    has_smokeball: bool, authored: Optional[str], deployed: Optional[set[tuple[str, str]]]
) -> Optional[str]:
    """None when the hourly check is scheduled on the Machine, else why not."""
    if not has_smokeball:
        return None
    if authored is None:
        return "no connector-auth-check row with pre_run.py is authored in its customer.yaml"
    if deployed is None:
        return "the seat's provisioned schedule could not be read"
    if (auth.AUTH_PROBE_SKILL, authored) not in deployed:
        return "it is authored but the seat has not been rebuilt to run it yet"
    return None


class Console:
    """The four console reads, done once per run. A failure is kept, not raised:
    every seat then carries an 'unreadable' item for the part it could not see."""

    def __init__(self, d1) -> None:
        self.error: Optional[str] = None
        self.status: dict[str, dict] = {}
        self.alerts: dict[str, list[dict]] = {}
        self.shortfalls: dict[str, list[dict]] = {}
        self.sentry: dict[str, list[dict]] = {}
        self.personas: dict[str, Optional[str]] = {}
        self.lane_holds: dict[str, list[dict]] = {}
        try:
            self._load(d1)
        except Exception as exc:  # noqa: BLE001 - reported as an item on every seat
            self.error = f"{type(exc).__name__}: {str(exc)[:200]}"

    def _load(self, d1) -> None:
        since = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
        week = (datetime.now(timezone.utc) - timedelta(days=9)).strftime("%Y-%m-%d")
        for row in d1.execute(
            "SELECT customer_slug, last_heartbeat_ts, process_uptime_seconds, connectors_json FROM fleet_status"
        ):
            self.status[str(row["customer_slug"])] = row
        for row in d1.execute(
            "SELECT customer_slug, job_lane, lane_key, event_at FROM operator_request_cards "  # noqa: S608 - since is a strftime date, not input
            f"WHERE kind = 'lane_unproven' AND job_lane IS NOT NULL AND event_at >= '{week}'"
        ):
            self.lane_holds.setdefault(str(row["customer_slug"]), []).append(row)
        for row in d1.execute("SELECT customer_slug, personas_json FROM customer_configs"):
            self.personas[str(row["customer_slug"])] = row.get("personas_json")
        for row in d1.execute(
            "SELECT customer_slug, condition, opened_at FROM fleet_alert_state WHERE status = 'open'"
        ):
            self.alerts.setdefault(str(row["customer_slug"]), []).append(row)
        for row in d1.execute(
            "SELECT customer_slug, ts, class, tool, code FROM operator_shortfalls "  # noqa: S608 - since is a strftime date, not input
            f"WHERE ts >= '{since}'"
        ):
            self.shortfalls.setdefault(str(row["customer_slug"]), []).append(row)
        for row in d1.execute(
            "SELECT customer_slug, detected_at, alert_date, driver, summary FROM cost_anomaly_alerts "  # noqa: S608 - since is a strftime date, not input
            f"WHERE source = 'sentry' AND detected_at >= '{since}'"
        ):
            self.sentry.setdefault(str(row["customer_slug"]), []).append(row)


def graph_check(slug: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """(mailbox, error, gap). SMD's own read credential, a token mint plus one
    GET of the mailbox's inbox folder id: no message is read."""
    seat = next((s for s in msgraph_channel.msgraph_seats(str(CUSTOMERS)) if s.slug == slug), None)
    if seat is None:
        return None, None, None
    secret = os.environ.get(seat.secret_env)
    if not secret:
        return (
            seat.mailbox,
            None,
            f"Mailbox sign-in for {seat.mailbox} not checked: SMD holds no read secret ({seat.secret_env}).",
        )
    try:
        token = msgraph_channel.graph_token(seat, secret)
        msgraph_channel._graph_get(
            f"{msgraph_channel.GRAPH_API_BASE}/users/{seat.mailbox}/mailFolders/inbox?$select=id", token
        )
    except msgraph_channel.ReconcileError as exc:
        return seat.mailbox, str(exc)[:120], None
    return seat.mailbox, None, None


def _seat_ledger(slug: str, cfg: wh.Config, now: datetime) -> tuple[Optional[list[dict]], Optional[wh.Item]]:
    """The seat's ledger window, or the 'unreadable' item that replaces it."""
    client = seam_pull.seam_client_from_env(slug)
    try:
        if client is None:
            raise RuntimeError("no runtime-read credential in this environment")
        return read_ledger(client, now - timedelta(hours=cfg.ledger_lookback_hours)), None
    except Exception as exc:  # noqa: BLE001 - an unreadable seat is an item, never all-clear
        return None, wh.item(
            slug,
            "unreadable",
            "ledger",
            now,
            f"Could not read this seat's work ledger ({type(exc).__name__}: {str(exc)[:160]}).",
            "Nothing about this seat's requests or jobs is known until the read works; check the Machine and the seam.",
        )


def _graph_items(slug: str, now: datetime) -> list[wh.Item]:
    mailbox, error, unchecked = graph_check(slug)
    items = src.graph_probe_item(slug, mailbox or "", error, now)
    if unchecked:
        items.append(
            wh.item(
                slug,
                "auth_unchecked",
                "graph:probe-not-running",
                now,
                f"ACTION: {unchecked}",
                "Add the seat's read secret to the work-health workflow's env and Actions secrets.",
            )
        )
    return items


def _operator_items(
    slug: str, cfg: wh.Config, console: Console, now: datetime, rows: list[dict]
) -> tuple[list[wh.Item], int]:
    """The Operator-only checks over a readable ledger: (items, resolved count)."""
    config = customer_yaml(slug)
    deployed = src.deployed_cron(console.personas.get(slug))
    has_smokeball, authored = seat_shape(config)
    why = probe_not_scheduled_why(has_smokeball, authored, deployed)
    closed = wh.load_closed()
    items = wh.request_items(slug, rows, cfg, now, closed)
    items += wh.job_items(slug, rows, cfg, now, closed, console.lane_holds.get(slug))
    items += wh.delivery_proof_items(slug, rows, cfg, now)
    if has_smokeball:
        items += auth.smokeball_auth_items(slug, rows, cfg, now, probe_scheduled=why is None)
        items += auth.probe_not_scheduled_items(slug, now, why=why)
    shortfalls, resolved_shortfalls = src.shortfall_items(slug, console.shortfalls.get(slug, []), cfg, now, rows)
    silent, resolved_silent = src.silent_run_items(slug, rows, cfg, now)
    items += shortfalls + silent
    if config:
        status = console.status.get(slug) or {}
        boot = {k: status.get(k) for k in ("last_heartbeat_ts", "process_uptime_seconds")}
        items += src.missed_slot_items(slug, rows, cfg, now, boot, config, deployed)
    return items + _graph_items(slug, now), resolved_shortfalls + resolved_silent


def evaluate_seat(slug: str, cfg: wh.Config, console: Console, now: datetime, rows: Optional[list[dict]]):
    """(items, gaps, resolved): resolved counts problems in the window that
    later work answered (history: one trailing count, never a line each)."""
    status = console.status.get(slug)
    if status is not None and not status.get("last_heartbeat_ts") and rows is None:
        # Provisioning-gray, the fleet pager's rule: a seat that has never
        # reported is being built, not broken. Named, never an item.
        return [], ["not live yet: this seat has never reported a heartbeat, so it was not evaluated."], 0
    items = src.heartbeat_items(slug, status, cfg, now)
    items += src.connector_items(slug, status, now)
    items += src.alert_items(slug, console.alerts.get(slug, []), now)
    items += src.sentry_items(slug, console.sentry.get(slug, []), cfg, now)
    product = seat_product(customer_yaml(slug))
    if product != "operator":
        # A hosted agent (seat.product: hosted-agent; scott, brokery) has no
        # request cards, job lanes or Smokeball: grading it against Operator
        # expectations would invent findings. Machine checks only, said so.
        gap = f"{product or 'unknown product'}: machine checks only (gateway, connections, alerts, Sentry); "
        return items, [gap + "no Operator request, job or schedule checks apply."], 0
    if console.error:
        items.append(
            wh.item(
                slug,
                "unreadable",
                "console",
                now,
                f"Could not read SMD's own records ({console.error}).",
                "Fix the D1 read; heartbeat, alerts, refusals and Sentry are unchecked until then.",
            )
        )
    if rows is None:
        rows, unreadable = _seat_ledger(slug, cfg, now)
        if rows is None:
            shortfalls, _ = src.shortfall_items(slug, console.shortfalls.get(slug, []), cfg, now, None)
            return [*items, *([unreadable] if unreadable else []), *shortfalls], [], 0
    operator_items, resolved = _operator_items(slug, cfg, console, now, rows)
    return items + operator_items, [], resolved


def live_seats(console: Console, wanted: list[str]) -> list[str]:
    if wanted:
        return wanted
    authored = {d.name for d in CUSTOMERS.iterdir() if d.is_dir() and not d.name.startswith(("_", "."))}
    if console.status:
        return sorted(console.status)
    return sorted(authored)


def send(subject: str, text: str, run_key: str) -> Optional[str]:
    key = os.environ.get("RESEND_API_KEY")
    if not key:
        return "RESEND_API_KEY is not set"
    body = json.dumps(
        {
            "from": os.environ.get("ALERT_FROM_EMAIL") or DEFAULT_FROM,
            "to": [os.environ.get("ALERT_TO_EMAIL") or DEFAULT_TO],
            "subject": subject,
            "text": text,
        }
    ).encode()
    req = urllib.request.Request(  # noqa: S310 - RESEND_URL is an https constant
        RESEND_URL,
        data=body,
        method="POST",
        # A User-Agent is required: Resend sits behind Cloudflare, which answers
        # urllib's default agent with 403 "error code: 1010" (found 2026-10-13).
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Idempotency-Key": run_key,
            "User-Agent": "smd-work-health/1.0",
        },
    )
    try:
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected — the Request's URL is the RESEND_URL https module constant; nothing user-supplied reaches it.
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - the request URL is the RESEND_URL https constant, never input
            payload = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return f"resend HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return f"resend unreachable: {exc}"
    print(f"sent: resend id {payload.get('id')}")
    return None


def send_pages(pages: list[wh.Item], now: datetime) -> Optional[str]:
    """One ACTION email per paging item, EVERY run while it stands: a dead
    sign-in stops all of a seat's work, so it is not left to the digest's
    one-line "still open" repeat. Returns the first send error, if any."""
    first_error = None
    for it in pages:
        text = (
            f"Seat: {it.seat}\n\n{it.what}\n\nTo do: {it.todo}\n\n"
            f"This repeats every hour until the sign-in works again (work-health, {now.strftime('%Y-%m-%d %H:%MZ')}).\n"
        )
        error = send(it.page or "", text, f"work-health-page:{mail.state_key(it.key)}:{now.strftime('%Y%m%dT%H')}")
        if error:
            print(f"PAGE SEND FAILED: {error}", file=sys.stderr)
            first_error = first_error or error
    return first_error


def _ts(value: Optional[str]) -> Optional[datetime]:
    return wh.parse_ts(value) if value else None


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Email team@ about the state of the work on every live seat.")
    ap.add_argument("--since", help="previous successful run start (ISO); new = after this")
    ap.add_argument("--state-in", help="previous run's state file ({key: first_seen})")
    ap.add_argument("--state-out", help="where to write this run's state")
    ap.add_argument("--slug", action="append", default=[], help="evaluate only this seat (repeatable)")
    ap.add_argument("--rows", action="append", default=[], help="offline: <slug>=<audit rows json>")
    ap.add_argument("--now", help="evaluate as of this instant (ISO); default: now")
    ap.add_argument("--dry-run", action="store_true", help="do not send")
    ap.add_argument(
        "--print-body",
        action="store_true",
        help="print the email text (LOCAL ONLY: the CI log of this public repo must never carry it)",
    )
    ap.add_argument(
        "--pages-only", action="store_true", help="send ACTION pages but not the digest (deliberate proofs)"
    )
    args = ap.parse_args(argv)

    now = _ts(args.now) or datetime.now(timezone.utc)
    since = _ts(args.since)
    previous = None
    if args.state_in and Path(args.state_in).exists():
        try:
            previous = json.loads(Path(args.state_in).read_text(encoding="utf-8"))
        except ValueError:
            previous = None
    offline = dict(spec.split("=", 1) for spec in args.rows)
    if not offline and not os.environ.get("OPERATOR_RUNTIME_READ_SECRET"):
        print("HOLD: no runtime-read credential; nothing can be evaluated", file=sys.stderr)
        return EXIT_HOLD

    cfg = wh.load_config()
    console = Console(console_d1.ConsoleD1()) if not offline else Console(_NoD1())
    seats = live_seats(console, args.slug or list(offline))
    items: list[wh.Item] = []
    gaps: dict[str, list[str]] = {}
    resolved: dict[str, int] = {}
    for slug in seats:
        rows = json.loads(Path(offline[slug]).read_text(encoding="utf-8")) if slug in offline else None
        seat_items, seat_gaps, seat_resolved = evaluate_seat(slug, cfg, console, now, rows)
        items += seat_items
        if seat_resolved:
            resolved[slug] = seat_resolved
        if seat_gaps:
            gaps[slug] = seat_gaps

    plan = mail.decide(
        items=items,
        seats=seats,
        gaps=gaps,
        previous=previous,
        since=since,
        now=now,
        all_clear_hour_utc=cfg.all_clear_hour_utc,
        resolved=resolved,
    )
    if args.state_out:
        Path(args.state_out).write_text(json.dumps(plan.state, indent=1, sort_keys=True), encoding="utf-8")
    # Counts only: this line lands in a PUBLIC repository's Actions log, and the
    # email text names matters. The text goes to team@ and nowhere else.
    kinds: dict[str, int] = {}
    for it in items:
        kinds[it.kind] = kinds.get(it.kind, 0) + 1
    print(f"evaluated {len(seats)} seat(s): {len(items)} item(s), {len(plan.new)} new; send={plan.send}; {kinds}")
    pages = [it for it in items if it.page]
    page_error = None if args.dry_run else send_pages(pages, now)
    if pages:
        print(f"pages: {len(pages)} ACTION email(s){' (dry run)' if args.dry_run else ''}")
    if not plan.send or args.pages_only:
        return EXIT_SEND_FAILED if page_error else EXIT_OK
    if args.print_body:
        print(f"Subject: {plan.subject}\n\n{plan.text}")
    if args.dry_run:
        return EXIT_OK
    error = send(plan.subject, plan.text, f"work-health:{now.strftime('%Y%m%dT%H%M')}:{int(time.time()) // 3600}")
    if error:
        print(f"SEND FAILED: {error}", file=sys.stderr)
        return EXIT_SEND_FAILED
    return EXIT_SEND_FAILED if page_error else EXIT_OK


class _NoD1:
    def execute(self, sql: str) -> list[dict]:
        return []


if __name__ == "__main__":
    raise SystemExit(main())
