#!/usr/bin/env python3
"""seat-readiness.py — is this seat actually ready, checked against what the code
requires rather than what someone remembered.

WHY THIS EXISTS (2026-08-12). A&P was reported READY to connect Smokeball. It was
not: the connector reads three secrets and the seat carried two. The readiness
note that cleared it said "`SMOKEBALL_PROD_CLIENT_ID/SECRET` in Infisical /ss (key
names verified this session)" — it enumerated two of three, found both, and
declared ready. **It could not fail on the third, because the third was never in
the list.** Every check here is derived from a machine source for exactly that
reason: a hand-maintained readiness list rots into false coverage the moment the
thing it forgot is the thing that breaks.

The credential check reads each connector's own `manifest.toml`
(`[[connector.required_secrets]]`) and asks whether the SEAT carries every
`runtime_env` the connector declares. That is deliberately the seat and not the
vault: the SDK's SecretSpec docstring is explicit that the vault->runtime remap
belongs to the overlay registry and must never be duplicated into the manifest
("so the manifest never becomes a second, contradictory wiring spec"), so a
vault-side check here would have to re-encode the mapping and could drift from
`provision-customer.sh`. The Machine's own env is the layer that matters anyway
(Law 9) and it needs no mapping to read.

UNKNOWN IS NEVER PASS. A stopped Machine cannot answer the currency question, so
that row reports UNKNOWN and the run is not green. A readiness tool whose unknown
rows read as ready is the same defect this file exists to kill.

NO SECRET VALUE IS EVER READ OR PRINTED. `flyctl secrets list` returns names and
digests only; nothing here dereferences a value.

USAGE
    operator/bin/seat-readiness.py <slug> [--json] [--no-seat]

    --no-seat  skip every check that needs Fly (for CI / offline); those rows
               report UNKNOWN rather than passing.

Exit codes: 0 = every blocker row passed; 1 = a blocker row FAILED or is UNKNOWN;
2 = the seat/config could not be read at all.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CUSTOMERS = REPO_ROOT / "operator" / "customers"
CONNECTORS = REPO_ROOT / "operator" / "connectors"

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"
INFO = "INFO"


@dataclass
class Row:
    """One readiness fact.

    `falsifier` is not decoration: a row that cannot state what would have made it
    FAIL is a row that proves nothing, and writing it forces the question.
    """

    section: str
    check: str
    status: str
    detail: str
    falsifier: str
    blocker: bool = True


@dataclass
class Report:
    slug: str
    rows: list[Row] = field(default_factory=list)

    def add(self, *a, **kw) -> None:
        self.rows.append(Row(*a, **kw))

    @property
    def blocking(self) -> list[Row]:
        return [r for r in self.rows if r.blocker and r.status in (FAIL, UNKNOWN)]


def _run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as exc:  # noqa: BLE001 — a probe failure is UNKNOWN, never PASS
        return 127, str(exc)


def load_customer(slug: str) -> dict:
    path = CUSTOMERS / slug / "customer.yaml"
    if not path.is_file():
        print(f"FATAL: no customer.yaml at {path}", file=sys.stderr)
        raise SystemExit(2)
    return yaml.safe_load(path.read_text()) or {}


def seat_secret_names(app: str) -> set[str] | None:
    """Fly secret NAMES for the app. None means we could not ask — never an
    empty set, because 'no secrets' and 'could not look' must not be the same
    answer to a readiness question."""
    code, out = _run(["flyctl", "secrets", "list", "--app", app, "--json"])
    if code != 0:
        return None
    try:
        return {row["name"] for row in json.loads(out) if isinstance(row, dict) and "name" in row}
    except Exception:  # noqa: BLE001 - flyctl JSON of an unexpected shape reads as UNKNOWN, which is the readiness report's honest answer
        return None


# ---------------------------------------------------------------- credentials
def check_connector_credentials(rep: Report, cfg: dict, staged: set[str] | None) -> None:
    """Every `runtime_env` a connector's manifest declares must exist on the seat.

    This is the check that would have caught SMOKEBALL_API_KEY on 2026-07-29.
    """
    connectors = cfg.get("connectors") or {}
    if not connectors:
        rep.add(
            "credentials",
            "connectors authored",
            INFO,
            "no connectors authored on this seat",
            "a connector present in customer.yaml but not seen here",
            blocker=False,
        )
        return

    for capability, conn in connectors.items():
        if not isinstance(conn, dict) or not conn.get("enabled"):
            continue
        backend = str(conn.get("backend", ""))
        if not backend.startswith("mcp:"):
            continue
        name = backend.split(":", 1)[1]
        manifest_path = CONNECTORS / name / "manifest.toml"
        if not manifest_path.is_file():
            # A vendor MCP with no in-repo manifest declares nothing we can check.
            rep.add(
                "credentials",
                f"{capability} ({backend})",
                INFO,
                "no in-repo manifest — nothing declared to verify",
                "a manifest appearing later that declares secrets nobody checked",
                blocker=False,
            )
            continue

        data = tomllib.loads(manifest_path.read_text())
        conn_tbl = data.get("connector", data)
        required = [
            s["runtime_env"]
            for s in (conn_tbl.get("required_secrets") or [])
            if isinstance(s, dict) and s.get("runtime_env")
        ]
        if not required:
            rep.add(
                "credentials",
                f"{capability} ({backend})",
                INFO,
                "manifest declares no required_secrets",
                "the connector reading an env var it never declared",
                blocker=False,
            )
            continue

        if staged is None:
            rep.add(
                "credentials",
                f"{capability}: {len(required)} declared secret(s)",
                UNKNOWN,
                "could not read the seat's secret names",
                "a seat probe that succeeds and shows one missing",
            )
            continue

        missing = [e for e in required if e not in staged]
        env = str(conn.get("environment", "")) or "(unset)"
        if missing:
            rep.add(
                "credentials",
                f"{capability}: declared secrets present on seat",
                FAIL,
                f"environment={env}; MISSING {', '.join(sorted(missing))} "
                f"(declared in operator/connectors/{name}/manifest.toml; staged by "
                f"provision-customer.sh from its per-environment vault names)",
                "all declared runtime_env names present on the seat",
            )
        else:
            rep.add(
                "credentials",
                f"{capability}: declared secrets present on seat",
                PASS,
                f"environment={env}; all {len(required)} present: {', '.join(sorted(required))}",
                "a declared runtime_env absent from the seat's Fly secrets",
            )


# ------------------------------------------------------------------- currency
def check_currency(rep: Report, app: str, no_seat: bool) -> None:
    """The running overlay ref must equal origin/main's pin.

    Same property `bin/connect-smokeball.sh` gates on (#2149) — a token must not
    land on a stale seat. Reported here so it is visible BEFORE connect day
    rather than as a bare non-zero exit from the connect script.
    """
    code, out = _run(
        ["git", "-C", str(REPO_ROOT), "show", "origin/main:operator/contracts/overlay-pairs.json"]
    )
    expected = ""
    if code == 0:
        m = re.search(r'"overlayRef"\s*:\s*"([0-9a-f]{40})"', out)
        expected = m.group(1) if m else ""
    if not expected:
        rep.add(
            "currency",
            "seat runs origin/main's pinned overlay",
            UNKNOWN,
            "could not read overlayRef from origin/main",
            "a readable pin that differs from the seat's",
        )
        return

    if no_seat:
        rep.add(
            "currency",
            "seat runs origin/main's pinned overlay",
            UNKNOWN,
            f"--no-seat; pin is {expected[:12]}",
            "a seat probe showing a different ref",
        )
        return

    code, out = _run(
        [
            "flyctl",
            "ssh",
            "console",
            "-a",
            app,
            "-C",
            "sh -c 'GPID=$(pgrep -f \"hermes.*gateway run\" | head -1); [ -n \"$GPID\" ] || exit 1; "
            'tr "\\0" "\\n" < /proc/$GPID/environ | grep ^SMD_OVERLAY_REF= | cut -d= -f2\'',
        ],
        timeout=120,
    )
    running = "".join(re.findall(r"[0-9a-f]{40}", out))[:40]
    if len(running) != 40:
        rep.add(
            "currency",
            "seat runs origin/main's pinned overlay",
            UNKNOWN,
            f"no gateway to read SMD_OVERLAY_REF from (machine stopped or gateway down); "
            f"pin is {expected[:12]}",
            "a running gateway whose ref differs from the pin",
        )
        return
    if running != expected:
        rep.add(
            "currency",
            "seat runs origin/main's pinned overlay",
            FAIL,
            f"seat runs {running[:12]}, origin/main pins {expected[:12]} — "
            f"connect-smokeball.sh will refuse (#2149). Fix: "
            f"yes s | operator/bin/reprovision.sh <slug>",
            "the two refs matching",
        )
    else:
        rep.add(
            "currency",
            "seat runs origin/main's pinned overlay",
            PASS,
            f"both {expected[:12]}",
            "a seat ref differing from the pin",
        )


def check_machine(rep: Report, app: str, no_seat: bool) -> None:
    if no_seat:
        rep.add("machine", "machine state", UNKNOWN, "--no-seat", "a stopped machine", blocker=False)
        return
    code, out = _run(["flyctl", "machines", "list", "--app", app, "--json"])
    if code != 0:
        rep.add("machine", "machine state", UNKNOWN, "could not list machines", "a readable state")
        return
    try:
        machines = json.loads(out)
        states = [m.get("state", "?") for m in machines] or ["(none)"]
    except Exception:  # noqa: BLE001 - an unparseable machine list reads as UNKNOWN; the readiness report reports, it does not crash
        rep.add("machine", "machine state", UNKNOWN, "unparseable machine list", "a parseable list")
        return
    started = any(s == "started" for s in states)
    rep.add(
        "machine",
        "machine started",
        PASS if started else FAIL,
        f"state(s): {', '.join(states)}",
        "a machine reporting started when it is not (or vice versa)",
        blocker=False,
    )


# ------------------------------------------------------------------- routines
def check_routines(rep: Report, cfg: dict, raw: str) -> None:
    """Scheduled AND webhook initiation, because 'routines are off' is only true
    when both are. Crons are the visible half; a live webhook trigger fires on the
    firm's own activity and is the half that surprises people."""
    active_schedules = re.findall(r"^\s+schedule:\s*'", raw, re.M)
    rep.add(
        "routines",
        "scheduled routines off",
        PASS if not active_schedules else FAIL,
        f"{len(active_schedules)} active schedule line(s)",
        "an uncommented schedule: line counted as off",
        blocker=False,
    )

    enabled_skills: dict[str, dict] = {}
    for persona in cfg.get("personas") or []:
        for s in (persona.get("skills") or []) if isinstance(persona, dict) else []:
            if isinstance(s, dict) and s.get("name"):
                enabled_skills[s["name"]] = s

    triggers = cfg.get("webhook_triggers") or []
    if not triggers:
        rep.add(
            "routines",
            "webhook-initiated routines off",
            PASS,
            "no webhook_triggers authored",
            "an authored trigger not counted here",
            blocker=False,
        )
        return
    live = []
    for t in triggers:
        if not isinstance(t, dict):
            continue
        skill = t.get("skill", "?")
        spec = enabled_skills.get(skill)
        if spec and spec.get("enabled") and (spec.get("initiation") or {}).get("webhook"):
            live.append(f"{t.get('source', '?')}/{t.get('event_type', '?')} -> {skill}")
    rep.add(
        "routines",
        "webhook-initiated routines off",
        PASS if not live else FAIL,
        "; ".join(live) if live else "no trigger reaches an enabled webhook-initiable skill",
        "a trigger whose skill is enabled+webhook-initiable counted as off",
        blocker=False,
    )


# -------------------------------------------------------------------- channel
def check_channel(rep: Report, cfg: dict) -> None:
    """Can a person say anything to this Operator?

    Every initiation-card command is something a human SAYS. If no inbound
    conversational channel is authored, the card is unusable and case alerts
    addressed to humans cannot be delivered — regardless of what else is green.
    """
    conns = cfg.get("connectors") or {}
    email = conns.get("Email") if isinstance(conns, dict) else None
    adapter = (email or {}).get("adapter") if isinstance(email, dict) else None
    enabled = bool((email or {}).get("enabled")) if isinstance(email, dict) else False
    if adapter and enabled:
        rep.add(
            "channel",
            "inbound conversational channel authored",
            PASS,
            f"Email adapter={adapter}",
            "an Email connector authored but disabled, or absent",
        )
    else:
        rep.add(
            "channel",
            "inbound conversational channel authored",
            FAIL,
            "no enabled Email connector — nobody at the firm can address this "
            "Operator, so no initiation-card command can be spoken and "
            "human-addressed alerts cannot be delivered",
            "an enabled Email connector with an adapter",
        )

    esc = cfg.get("escalation") or {}
    recips = list(esc.get("red_flag_recipients") or [])
    if recips:
        rep.add(
            "channel",
            "case alerts deliverable",
            PASS if (adapter and enabled) else FAIL,
            f"{len(recips)} recipient(s) authored; "
            + ("a channel exists" if (adapter and enabled) else "NO channel to deliver on"),
            "recipients authored with no channel, reported as deliverable",
        )


# ---------------------------------------------------------------------- card
def check_initiation_card(rep: Report, slug: str, cfg: dict) -> None:
    path = CUSTOMERS / slug / "initiation-card.yaml"
    if not path.is_file():
        rep.add(
            "card", "initiation card present", INFO, "no initiation-card.yaml", "a card appearing later", blocker=False
        )
        return
    card = yaml.safe_load(path.read_text()) or {}
    enabled_skills = {
        s["name"]
        for persona in (cfg.get("personas") or [])
        for s in (persona.get("skills") or [])
        if isinstance(s, dict) and s.get("name") and s.get("enabled")
    }
    total = green = 0
    unbound: list[str] = []
    for stage in card.get("stages") or []:
        locked = bool(stage.get("locked"))
        for cmd in stage.get("commands") or []:
            total += 1
            if str(cmd.get("rehearsal", "")).strip() == "green":
                green += 1
            backed = str(cmd.get("backed_by", ""))
            if not locked and backed and backed != "core-dialogue" and backed not in enabled_skills:
                unbound.append(backed)
    rep.add(
        "card",
        "initiation-card commands rehearsed",
        PASS if green == total and total else FAIL,
        f"{green}/{total} green — the card's own rule is that a pending command is "
        f"not spoken at the firm",
        "a command marked green without a rehearsal record",
    )
    if unbound:
        rep.add(
            "card",
            "unlocked card commands are backed by bound skills",
            FAIL,
            f"unbound: {', '.join(sorted(set(unbound)))}",
            "every unlocked command's backed_by present and enabled",
        )


# ------------------------------------------------------------------ coverage
def coverage_rows(slug: str, cfg: dict) -> list[dict]:
    """One row per routine the FIRM was promised, generated from routine-grid.yaml.

    IMPLEMENTATION-PLAN §6 wants a coverage checklist whose unproven-row count is
    the honest answer to "are we done?", and warns that a hand-maintained one
    "rots into false coverage". So the ROWS are generated: routine-grid.yaml is
    the compiled letter-07 grid (ADR 0075, every tier traced verbatim to the
    letter), which makes completeness structural — a capability the firm was
    promised cannot be missing from this table, because the table is derived from
    the promise.

    The `proving` column is deliberately NOT invented. Nothing on this machine
    knows whether a routine was demonstrated to the firm, so it reports
    "(none recorded)" and a human pastes the crane_verify id. A blank that looks
    like a pass is the failure this file exists to prevent.
    """
    grid_path = CUSTOMERS / slug / "routine-grid.yaml"
    if not grid_path.is_file():
        return []
    grid = yaml.safe_load(grid_path.read_text()) or {}

    skills: dict[str, dict] = {}
    for persona in cfg.get("personas") or []:
        for s in (persona.get("skills") or []) if isinstance(persona, dict) else []:
            if isinstance(s, dict) and s.get("name"):
                skills[s["name"]] = s

    conns = cfg.get("connectors") or {}
    email = conns.get("Email") if isinstance(conns, dict) else None
    has_channel = bool(isinstance(email, dict) and email.get("adapter") and email.get("enabled"))

    out: list[dict] = []
    for row in grid.get("rows") or []:
        named = list(row.get("skills") or [])
        bound = [n for n in named if n in skills and skills[n].get("enabled")]
        unbound = [n for n in named if n not in bound]
        init = {
            k
            for n in bound
            for k, v in (skills[n].get("initiation") or {}).items()
            if v
        }
        # Can a real person or event actually make this routine run today?
        # `runnable` is an explicit boolean, not something a caller infers from
        # the prose: counting on a string prefix silently missed the
        # "person-invoked only — but NO channel" case, which is exactly the kind
        # of soft undercount that makes a checker read greener than the world.
        if not bound:
            can_run, runnable = "NO — skill not bound/enabled on this seat", False
        elif any((skills[n].get("initiation") or {}).get("scheduled") for n in bound):
            can_run, runnable = "scheduled (off means it will not fire — see the crons row)", True
        elif "webhook" in init:
            can_run, runnable = (
                ("webhook", True)
                if has_channel
                else ("NO — webhook-initiable but this seat has no inbound channel", False)
            )
        elif "manual" in init:
            can_run, runnable = (
                ("person-invoked", True)
                if has_channel
                else ("NO — person-invoked, and no channel exists to invoke it on", False)
            )
        else:
            can_run, runnable = "NO — no initiation authored", False
        out.append(
            {
                "routine": row.get("routine", "?"),
                "letter_section": row.get("letter_section", ""),
                "start_verbatim": row.get("start_verbatim", ""),
                "skills": named,
                "unbound": unbound,
                "can_run_today": can_run,
                "runnable": runnable,
                "proving": "(none recorded)",
            }
        )
    return out


def print_coverage(slug: str, rows: list[dict]) -> None:
    print(f"\nCOVERAGE — {slug} — {len(rows)} routine(s) the firm was promised (letter 07 grid)")
    print("=" * 78)
    for i, r in enumerate(rows, 1):
        print(f"\n{i:>2}. {r['routine']}  [{r['letter_section']}]  start: {r['start_verbatim']}")
        print(f"    skills: {', '.join(r['skills']) or '(none named)'}")
        if r["unbound"]:
            print(f"    UNBOUND on this seat: {', '.join(r['unbound'])}")
        print(f"    can run today: {r['can_run_today']}")
        print(f"    proving event: {r['proving']}")
    unproven = sum(1 for r in rows if r["proving"] == "(none recorded)")
    blocked = sum(1 for r in rows if not r["runnable"])
    print("\n" + "=" * 78)
    print(f"unproven rows: {unproven}/{len(rows)}   cannot-run-today rows: {blocked}/{len(rows)}")
    print("The unproven count is the honest answer to 'are we done?' (plan §6).")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("slug")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--no-seat", action="store_true", help="skip Fly probes; they report UNKNOWN")
    ap.add_argument("--coverage", action="store_true", help="emit the letter-07 coverage table too")
    args = ap.parse_args()

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", args.slug):
        print(f"FATAL: invalid slug {args.slug!r}", file=sys.stderr)
        return 2

    cfg = load_customer(args.slug)
    raw = (CUSTOMERS / args.slug / "customer.yaml").read_text()
    app = f"hermes-{args.slug}"
    rep = Report(args.slug)

    staged = None if args.no_seat else seat_secret_names(app)
    check_connector_credentials(rep, cfg, staged)
    check_currency(rep, app, args.no_seat)
    check_machine(rep, app, args.no_seat)
    check_routines(rep, cfg, raw)
    check_channel(rep, cfg)
    check_initiation_card(rep, args.slug, cfg)

    cov = coverage_rows(args.slug, cfg) if args.coverage else []

    if args.as_json:
        payload = {"slug": args.slug, "rows": [r.__dict__ for r in rep.rows]}
        if args.coverage:
            payload["coverage"] = cov
        print(json.dumps(payload, indent=2))
    else:
        print(f"\nSEAT READINESS — {args.slug}\n" + "=" * 72)
        section = None
        for r in rep.rows:
            if r.section != section:
                section = r.section
                print(f"\n[{section}]")
            print(f"  {r.status:<8} {r.check}")
            print(f"           {r.detail}")
        blocking = rep.blocking
        print("\n" + "=" * 72)
        if blocking:
            print(f"NOT READY — {len(blocking)} blocking row(s):")
            for r in blocking:
                print(f"  {r.status}  {r.check}")
        else:
            print("All blocking rows pass.")
        print("(UNKNOWN is never PASS — an unanswerable check is not a ready one.)")
        if args.coverage:
            print_coverage(args.slug, cov)
    return 1 if rep.blocking else 0


if __name__ == "__main__":
    sys.exit(main())
