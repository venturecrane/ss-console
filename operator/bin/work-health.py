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

from bin.lib import console_d1, msgraph_channel, seam_pull, transient_http
from bin.lib import work_health as wh
from bin.lib import work_health_auth as auth
from bin.lib import work_health_boot as boot
from bin.lib import work_health_casefile as casefile_wh
from bin.lib import work_health_keeper as keeper_wh
from bin.lib import work_health_mail as mail
from bin.lib import work_health_owed as owed_wh
from bin.lib import work_health_page_store as page_store_wh
from bin.lib import work_health_proof as proof_wh
from bin.lib import work_health_runs as runs_wh
from bin.lib import work_health_sources as src
from bin.lib import work_health_waiting as waiting
from bin.lib import seat_config

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
        data = seat_config.load(CUSTOMERS / slug / "customer.yaml", encoding="utf-8", on_missing="raise")
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def seat_product(config: dict) -> Optional[str]:
    """``seat.product`` from the seat's customer.yaml: operator or hosted-agent."""
    seat = config.get("seat") if isinstance(config, dict) else None
    product = seat.get("product") if isinstance(seat, dict) else None
    return str(product) if product else None


def seat_kind(config: dict) -> Optional[str]:
    """``seat.kind`` from the seat's customer.yaml (customer, proving, preprod,
    internal, sandbox)."""
    seat = config.get("seat") if isinstance(config, dict) else None
    kind = seat.get("kind") if isinstance(seat, dict) else None
    return str(kind) if kind else None


def footnoted_seats(seats: list[str]) -> frozenset[str]:
    """Seats SMD runs for itself, whose problems are a footnote in the email.
    An unknown kind is NOT a test seat: a seat is hidden only when its own
    customer.yaml says it is not a client."""
    return frozenset(s for s in seats if seat_kind(customer_yaml(s)) not in (None, "customer"))


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
        self.schedule_changed_at: dict[str, Optional[str]] = {}
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
        for row in d1.execute(
            "SELECT customer_slug, personas_json, synced_at, schedule_changed_at FROM customer_configs"
        ):
            slug = str(row["customer_slug"])
            self.personas[slug] = row.get("personas_json")
            # When the SCHEDULE last changed (migration 0130), not when the config
            # was last published: synced_at moves on every publish, and grading
            # against it switched A&P's grading off after #3332's unrelated merge.
            # A row not re-projected since 0130 falls back to synced_at, the
            # conservative answer (grading waits for a rebuild, never invents misses).
            self.schedule_changed_at[slug] = row.get("schedule_changed_at") or row.get("synced_at")
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


class MachineWatch:
    """The seat's Machine health while a failed read waits (work_health_boot).

    Fly's machine list where the fly CLI is (smd-ops); elsewhere (the GitHub
    Actions backstop) the seam answering a first page is the health check."""

    def __init__(self, slug: str, client) -> None:
        self.slug = slug
        self.client = client
        self.state = boot.MachineState(None)

    def passing(self) -> Optional[bool]:
        machines, _why = keeper_wh.read_machines(self.slug, CUSTOMERS.parents[1])
        if machines is not None:
            self.state = boot.machine_state(machines)
            return self.state.passing
        try:
            self.client.read_page("audit_export")
        except Exception:  # noqa: BLE001 - any failure is "not answering yet"
            return False
        return True


def _unreadable(slug: str, now: datetime, detail: str) -> wh.Item:
    return wh.item(
        slug,
        "unreadable",
        "ledger",
        now,
        f"Could not read this seat's work ledger ({detail}).",
        "Nothing about this seat's requests or jobs is known until the read works; check the Machine and the seam.",
    )


def _seat_ledger(
    slug: str,
    cfg: wh.Config,
    now: datetime,
    *,
    sleep=time.sleep,
    clock=time.monotonic,
    watch: Optional[MachineWatch] = None,
) -> tuple[Optional[list[dict]], Optional[wh.Item]]:
    """The seat's ledger window, or the 'unreadable' item that replaces it.

    A timeout or a connection error is a Machine that may be restarting: wait
    for its health check (up to work_health_boot.BOOT_WAIT), then read once
    more, and list the seat only if that read fails too."""
    client = seam_pull.seam_client_from_env(slug)
    since = now - timedelta(hours=cfg.ledger_lookback_hours)
    if client is None:
        return None, _unreadable(slug, now, "RuntimeError: no runtime-read credential in this environment")
    try:
        return read_ledger(client, since), None
    except Exception as exc:  # noqa: BLE001 - an unreadable seat is an item, never all-clear
        if not transient_http.is_transient(exc):
            return None, _unreadable(slug, now, f"{type(exc).__name__}: {str(exc)[:160]}")
    watch = watch or MachineWatch(slug, client)
    waited = boot.wait_for_health(watch.passing, sleep=sleep, clock=clock)
    try:
        return read_ledger(client, since), None
    except Exception as exc:  # noqa: BLE001 - an unreadable seat is an item, never all-clear
        later = now + timedelta(seconds=waited)
        return None, _unreadable(slug, now, boot.unreadable_detail(exc, waited, watch.state, later))


def _graph_items(slug: str, now: datetime, status: Optional[dict] = None) -> list[wh.Item]:
    mailbox, error, unchecked = graph_check(slug)
    items = src.graph_probe_item(slug, mailbox or "", error, now, src.seat_mail_connection_ok(status))
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
    # Owed work with no lookback (the broker's open read); None falls back to the window.
    open_owed = owed_wh.read_open_owed(slug)
    items = wh.request_items(slug, rows, cfg, now, closed)
    items += src.could_not_items(slug, rows, cfg, now, closed, owed_wh.owed_refs(open_owed))
    items += wh.job_items(slug, rows, cfg, now, closed, console.lane_holds.get(slug))
    owed_jobs = owed_wh.owed_jobs_kwargs(open_owed, proof_wh.delivery_proof_items)
    items += proof_wh.delivery_proof_items(slug, rows, cfg, now, closed, **owed_jobs)
    items += owed_wh.smd_owed_items(slug, rows, cfg, now, closed, open_owed)
    # One job, one line: a hold that is about a job already listed is said on
    # that job's line, not again as a silent run (triage, 2026-11-04).
    listed_jobs = frozenset(it.job for it in items if it.job)
    items += casefile_wh.casefile_schedule_items(slug, rows, now)
    items += keeper_wh.keeper_items(slug, rows, now, config)
    items += page_store_wh.page_store_items(slug, rows, cfg, now)
    if has_smokeball:
        items += auth.smokeball_auth_items(slug, rows, cfg, now, probe_scheduled=why is None)
        items += auth.probe_not_scheduled_items(slug, now, why=why)
    shortfalls, resolved_shortfalls = src.shortfall_items(slug, console.shortfalls.get(slug, []), cfg, now, rows)
    silent, resolved_silent = src.silent_run_items(slug, rows, cfg, now, listed_jobs)
    items += shortfalls + silent
    status = console.status.get(slug) or {}
    if config:
        booted = {k: status.get(k) for k in ("last_heartbeat_ts", "process_uptime_seconds")}
        adopted = src.schedule_adopted_at(console.schedule_changed_at.get(slug), booted)
        items += src.missed_slot_items(slug, rows, cfg, now, booted, config, deployed, adopted)
    return items + _graph_items(slug, now, status), resolved_shortfalls + resolved_silent


def evaluate_seat(
    slug: str,
    cfg: wh.Config,
    console: Console,
    now: datetime,
    rows: Optional[list[dict]],
    seen: Optional[dict[str, list[dict]]] = None,
):
    """(items, gaps, resolved): resolved counts problems in the window that
    later work answered (history: one trailing count, never a line each)."""
    status = console.status.get(slug)
    if status is not None and not status.get("last_heartbeat_ts") and rows is None:
        # Provisioning-gray, the fleet pager's rule: a seat that has never
        # reported is being built, not broken. Named, never an item.
        return [], ["not live yet: this seat has never reported a heartbeat, so it was not evaluated."], 0
    items = src.heartbeat_items(slug, status, cfg, now)
    items += src.fold_connector_lines(
        src.connector_items(slug, status, now) + src.alert_items(slug, console.alerts.get(slug, []), now)
    )
    items += src.sentry_items(slug, console.sentry.get(slug, []), cfg, now)
    # ADR 0100 W6: one product. Every seat gets the same checks; each check
    # grades only what the seat authors (Smokeball sign-in only with that
    # connector, the mailbox probe only with an msgraph mailbox, missed slots
    # only for authored schedules, jobs only where jobs ran). Until 2026-10-31
    # a seat.product of hosted-agent got machine checks only.
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
            # The unreadable line already says nothing about the seat is known.
            # Its refusals are left off: without the ledger they cannot be
            # checked against later work, and answered ones came back as NEW
            # (triage, 2026-11-05). The next readable run grades them.
            return [*items, *([unreadable] if unreadable else [])], [], 0
    if seen is not None:
        seen[slug] = rows
    operator_items, resolved = _operator_items(slug, cfg, console, now, rows)
    return items + operator_items, [], resolved


def live_seats(console: Console, wanted: list[str]) -> list[str]:
    if wanted:
        return wanted
    authored = {d.name for d in CUSTOMERS.iterdir() if d.is_dir() and not d.name.startswith(("_", "."))}
    if console.status:
        return sorted(console.status)
    return sorted(authored)


def send(subject: str, text: str, run_key: str, to: Optional[str] = None) -> Optional[str]:
    key = os.environ.get("RESEND_API_KEY")
    if not key:
        return "RESEND_API_KEY is not set"
    body = json.dumps(
        {
            "from": os.environ.get("ALERT_FROM_EMAIL") or DEFAULT_FROM,
            "to": [to or os.environ.get("ALERT_TO_EMAIL") or DEFAULT_TO],
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


def page_text(it: wh.Item, page: wh.Page, now: datetime) -> str:
    """What happened and what to do, then the agent's half below a fold."""
    return (
        f"WHAT HAPPENED: {page.happened}\n\n"
        f"WHAT YOU NEED TO DO: {page.todo}\n\n"
        "You will get this email every hour until it is fixed.\n\n"
        "---\n"
        "DETAILS, for the agent who picks this up\n"
        f"Seat: {it.seat}. Checked {wh.hhmm(now)}.\n"
        f"{it.what}\n"
        f"To do: {it.todo}\n"
    )


def send_pages(pages: list[wh.Item], now: datetime) -> Optional[str]:
    """One email per paging item, EVERY run while it stands: a dead sign-in
    stops all of a seat's work, so it is not left to the digest's one-line "still
    open" repeat. Returns the first send error, if any."""
    first_error = None
    for it in pages:
        if it.page is None:
            continue
        error = send(
            it.page.subject,
            page_text(it, it.page, now),
            f"work-health-page:{mail.state_key(it.key)}:{now.strftime('%Y%m%dT%H')}",
        )
        if error:
            print(f"PAGE SEND FAILED: {error}", file=sys.stderr)
            first_error = first_error or error
    return first_error


def _ts(value: Optional[str]) -> Optional[datetime]:
    return wh.parse_ts(value) if value else None


def _parser() -> argparse.ArgumentParser:
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
        "--pages-only", action="store_true", help="send the urgent pages but not the digest (deliberate proofs)"
    )
    ap.add_argument(
        "--machines", action="store_true", help="also count each seat's started Fly machines (needs a Fly credential)"
    )
    ap.add_argument("--runs", action="store_true", help="print how many chronology runs needed nobody; send nothing")
    ap.add_argument(
        "--runs-days",
        type=int,
        default=runs_wh.DEFAULT_DAYS,
        help="--runs window in days (reads every audit row in it, 50 per seam request)",
    )
    return ap


def runs_report(seats: list[str], offline: dict[str, str], now: datetime, days: int) -> int:
    """``--runs``: each seat's chronology runs over its own window, and all seats."""
    per_seat: dict[str, dict] = {}
    for slug in seats:
        if slug in offline:
            rows = json.loads(Path(offline[slug]).read_text(encoding="utf-8"))
        else:
            client = seam_pull.seam_client_from_env(slug)
            if client is None:
                print(f"{slug}: not read (no runtime-read credential)")
                continue
            try:
                rows = read_ledger(client, runs_wh.read_from(now - timedelta(days=days)))
            except Exception as exc:  # noqa: BLE001 - one unreadable seat is said, the rest still report
                print(f"{slug}: not read ({type(exc).__name__})")
                continue
        per_seat[slug] = runs_wh.summarize(rows, since=now - timedelta(days=days))
    print(runs_wh.report(per_seat, days))
    return EXIT_OK


def _machine_check(slug: str, now: datetime, items: list[wh.Item], gaps: dict[str, list[str]]) -> None:
    """One machine per seat (bin/lib/work_health_keeper.py); unreadable is a gap."""
    machines, why = keeper_wh.read_machines(slug, CUSTOMERS.parents[1])
    if machines is None:
        gaps.setdefault(slug, []).append(f"machine count not checked: {why}.")
        return
    items += keeper_wh.machine_items(slug, machines, now)


def _runs_footer(seen: dict[str, list[dict]], hours: int, now: datetime) -> str:
    """The digest's one line about chronology runs, when any seat ran one: the
    runs submitted after the read's start plus the same-matter look-back, so
    every graded run has its whole history in the rows read."""
    since = now - timedelta(hours=hours) + runs_wh.SAME_MATTER_WINDOW
    per_seat = {slug: runs_wh.summarize(rows, since=since) for slug, rows in seen.items()}
    total = runs_wh.combine({s: c for s, c in per_seat.items() if c["runs"]})
    if not total.get("runs"):
        return ""
    days = (now - since).total_seconds() / 86400
    return "\n\n" + runs_wh.line(f"Chronology runs submitted in the last {days:g} day(s)", total) + "\n"


def _evaluate_all(
    cfg: wh.Config,
    console: Console,
    seats: list[str],
    offline: dict[str, str],
    now: datetime,
    machines: bool = False,
    seen: Optional[dict[str, list[dict]]] = None,
):
    """(items, gaps, resolved) over every seat; ``seen`` collects each read ledger."""
    items: list[wh.Item] = []
    gaps: dict[str, list[str]] = {}
    resolved: dict[str, int] = {}
    for slug in seats:
        rows = json.loads(Path(offline[slug]).read_text(encoding="utf-8")) if slug in offline else None
        seat_items, seat_gaps, seat_resolved = evaluate_seat(slug, cfg, console, now, rows, seen)
        items += seat_items
        if seat_resolved:
            resolved[slug] = seat_resolved
        if seat_gaps:
            gaps[slug] = seat_gaps
        if machines:
            _machine_check(slug, now, items, gaps)
    return items, gaps, resolved


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
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

    if args.runs and (args.slug or offline):
        return runs_report(args.slug or list(offline), offline, now, args.runs_days)
    cfg = wh.load_config()
    console = Console(console_d1.ConsoleD1()) if not offline else Console(_NoD1())
    seats = live_seats(console, args.slug or list(offline))
    if args.runs:
        return runs_report(seats, offline, now, args.runs_days)
    seen: dict[str, list[dict]] = {}
    items, gaps, resolved = _evaluate_all(cfg, console, seats, offline, now, args.machines, seen)
    items += wh.missed_run_items(since, cfg, now)
    plan = mail.decide(
        items=items,
        seats=seats,
        gaps=gaps,
        previous=previous,
        since=since,
        now=now,
        all_clear_hour_utc=cfg.all_clear_hour_utc,
        resolved=resolved,
        test_seats=footnoted_seats(seats),
    )
    # A firm person still waiting two hours after the reply: the Captain hears
    # of it once per item (2026-11-25). Sent before the state is written, so a
    # reminder is recorded only once it went.
    test_seats = footnoted_seats(seats)
    reminded, remind_error = waiting.run(items, previous, now, test_seats, args.dry_run, send)
    if args.state_out:
        state = {**plan.state, waiting.REMINDED: reminded}
        Path(args.state_out).write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    # Counts only: this line lands in a PUBLIC repository's Actions log, and the
    # email text names matters. The text goes to team@ and nowhere else.
    kinds: dict[str, int] = {}
    for it in items:
        kinds[it.kind] = kinds.get(it.kind, 0) + 1
    print(f"evaluated {len(seats)} seat(s): {len(items)} item(s), {len(plan.new)} new; send={plan.send}; {kinds}")
    # A test seat's problems stay on the triage agent's list and are not emailed
    # (workers/fleet-alerts/src/mail-policy.ts is the same rule for alerts).
    pages = [it for it in items if it.page and it.seat not in test_seats]
    page_error = (None if args.dry_run else send_pages(pages, now)) or remind_error
    if pages:
        print(f"pages: {len(pages)} email(s){' (dry run)' if args.dry_run else ''}")
    if not plan.send or args.pages_only:
        return EXIT_SEND_FAILED if page_error else EXIT_OK
    plan.text += _runs_footer(seen, cfg.ledger_lookback_hours, now)
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
