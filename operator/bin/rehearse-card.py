#!/usr/bin/env python3
"""rehearse-card.py — say the stand-up script to a seat, as the firm would.

WHY THIS EXISTS (2026-08-12). `initiation-card.yaml` is the script the firm is
told to speak after connect, and every command carries an `expected` and a
`falsifier`. Both seats read `rehearsal: pending` on all 18 commands — the card's
own rule is that a pending command is not spoken at the firm, so on paper nothing
was sayable. The first rehearsal was run by hand out of a scratchpad, which is
how a one-off stays a one-off.

WHAT IT DOES. Sends each unlocked command VERBATIM from an address the seat's own
config names as an admin, waits for the reply, and writes a transcript.

WHAT IT DOES NOT DO — and this is deliberate. **It does not judge.** The first
hand rehearsal was scored by the same agent that wrote the messages, and that
judgment was wrong at least once: a "failure" turned out to be a mis-designed
test, caught by accident. An automated grader here would industrialise exactly
that error. So the transcript pairs each reply with the command's own `expected`
and `falsifier` and stops, leaving a human or a second reader to decide. The
tool's job is to make the evidence cheap and complete, not to reach a verdict.

SILENCE IS NEVER A PASS. A command that draws no reply is written as NO REPLY
with its timeout, because "the seat said nothing" and "the seat answered well"
must never look alike in the record.

WHY A SECOND TRANSPORT (2026-08-20). The paying client's seat runs its Email
connector on Microsoft Graph (ADR 0078: the operator lives on the client's own
mail system), so it has no AgentMail inbox and an AgentMail-only harness could
not reach it at all. The tool that proves a seat is client-ready could not speak
to the only production client. Channel coupling in the instrument is the same
defect class ADR 0078 names in the product, so the transport is now selected
from config rather than assumed.

USAGE
    AgentMail seat (the seat owns an AgentMail inbox):
        infisical run --env=prod --path=/ss -- \\
            operator/bin/rehearse-card.py pilot-smokeball --as <admin@address>

    Microsoft Graph seat (the seat mailbox lives in the client's own tenant):
        infisical run --env=prod --path=/ss -- \\
            operator/bin/rehearse-card.py ashton-price --as <admin@address>

    --out FILE      transcript path (default card-transcript-<slug>.txt)
    --only N        run just command N (1-based, as printed by --list)
    --list          print the commands and exit; sends nothing
    --timeout SECS  per-command wait (default 420)

TRANSPORT is chosen from the seat's own `connectors.Email.adapter`, never from a
flag. `msgraph` sends through Resend from the admin address and reads the reply
out of the SEAT mailbox's Sent Items over app-only Graph, because the admin
address is a human mailbox this tool holds no credential for. Any other adapter
keeps the AgentMail path exactly as it was.

ENVIRONMENT
    AgentMail seats:  AGENTMAIL_API_KEY
    msgraph seats:    RESEND_API_KEY and MSGRAPH_CLIENT_SECRET__<SLUG>
                      (slug upper-cased, hyphens as underscores)

Exit codes: 0 = every attempted command drew a reply; 1 = at least one did not;
2 = the seat/card/config could not be read, or the sender is not an authored
admin (fail closed — rehearsing as someone the seat does not trust measures the
wrong thing).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CUSTOMERS = REPO_ROOT / "operator" / "customers"
API_BASE = "https://api.agentmail.to/v0"

RESEND_URL = "https://api.resend.com/emails"
TOKEN_HOST = "https://login.microsoftonline.com"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"

# Cloudflare sits in front of api.resend.com and answers urllib's default
# User-Agent with `403 error code: 1010` (observed live 2026-08-20), so every
# outbound request this module makes names itself.
USER_AGENT = "smd-rehearse-card/1.0"

# The seat mailbox's own outbound folder, newest first. The space in $orderby is
# written %20 because urllib refuses a literal space in a URL.
SENT_ITEMS_QUERY = "?$top=10&$orderby=sentDateTime%20desc&$select=id,subject,sentDateTime,toRecipients,body"

# Ask Graph for a plain-text body so the transcript holds words rather than HTML.
TEXT_BODY_PREFER = {"Prefer": 'outlook.body-content-type="text"'}

INFISICAL_RECIPE = "run under `infisical run --env=prod --path=/ss`"

# The subject stem both transports match a reply on. Long enough to be unique per
# command, short enough to survive a mail client's own subject rewriting.
SUBJECT_STEM = 38


def _die(msg: str, code: int = 2) -> None:
    print(f"FATAL: {msg}", file=sys.stderr)
    raise SystemExit(code)


def load(slug: str) -> tuple[dict, dict]:
    card_path = CUSTOMERS / slug / "initiation-card.yaml"
    cfg_path = CUSTOMERS / slug / "customer.yaml"
    if not card_path.is_file():
        _die(f"no initiation-card.yaml at {card_path}")
    if not cfg_path.is_file():
        _die(f"no customer.yaml at {cfg_path}")
    return (
        yaml.safe_load(card_path.read_text()) or {},
        yaml.safe_load(cfg_path.read_text()) or {},
    )


def email_connector(cfg: dict) -> dict:
    """The seat's authored Email connector, or a loud exit."""
    conns = cfg.get("connectors") or {}
    email = conns.get("Email") if isinstance(conns, dict) else None
    if not isinstance(email, dict) or not email.get("enabled"):
        _die(
            "this seat authors no enabled Email connector, so there is no channel "
            "to speak the card on — nothing to rehearse"
        )
    return email


def email_adapter(cfg: dict) -> str:
    """Which transport can reach this seat, read from the seat's own config.

    The seat, not a flag, decides. A flag would let a rehearsal be run over a
    channel the seat does not actually serve clients on, which is the failure
    this harness exists to rule out.
    """
    return str(email_connector(cfg).get("adapter", "")).strip().lower()


def seat_inbox(cfg: dict) -> str:
    """The seat's own inbox, from its authored Email connector.

    On a `msgraph` seat the address is authored outright (the mailbox lives in
    the client's tenant, ADR 0078). On every other seat it is still derived from
    the AgentMail webhook host, unchanged.
    """
    email = email_connector(cfg)
    if str(email.get("adapter", "")).strip().lower() == "msgraph":
        mailbox = str((email.get("msgraph_auth") or {}).get("mailbox", "")).strip()
        if not mailbox:
            _die(
                "this seat's Email connector is msgraph but authors no "
                "msgraph_auth.mailbox, so there is no address to speak to"
            )
        return mailbox
    url = str(email.get("webhook_url", ""))
    m = re.search(r"hermes-([a-z0-9-]+)\.fly\.dev", url)
    if not m:
        _die("could not derive the seat inbox from the Email connector's webhook_url")
    return f"{m.group(1)}@agentmail.to"


def unlocked_commands(card: dict) -> list[dict]:
    """Every command the firm could actually be told to say.

    Locked stages are skipped: the card gates them on a real-world event (the
    principal's own drafting test), so rehearsing them would assert a readiness
    the firm has not granted.
    """
    out: list[dict] = []
    for stage in card.get("stages") or []:
        if stage.get("locked"):
            continue
        for cmd in stage.get("commands") or []:
            out.append({"stage": stage.get("id", "?"), **cmd})
    return out


def api(method: str, path: str, key: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API_BASE + path, data=data, method=method, headers=headers)
    try:
        # The URL is always the constant API_BASE (https://api.agentmail.to/v0)
        # concatenated with a path this module builds; no caller supplies a
        # scheme or host, so the file:// concern the rule guards does not arise.
        # Same suppression and reasoning as operator/bin/mint-agentmail-keys.py.
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read().decode()[:400]}


def strip_quote_trail(body: str) -> str:
    """Drop the quoted original so the record holds the Operator's own words."""
    for marker in ("\nOn Mon,", "\nOn Tue,", "\nOn Wed,", "\nOn Thu,", "\nOn Fri,", "\nOn Sat,", "\nOn Sun,", "\n> "):
        if marker in body:
            body = body.split(marker)[0]
    return body.strip()


def ask(sender: str, seat: str, subject: str, text: str, key: str, timeout: int) -> str | None:
    st, _ = api(
        "POST",
        f"/inboxes/{urllib.parse.quote(sender)}/messages/send",
        key,
        {"to": [seat], "subject": subject, "text": text},
    )
    if st != 200:
        return None
    deadline = time.time() + timeout
    while time.time() < deadline:
        s2, res = api("GET", f"/inboxes/{urllib.parse.quote(sender)}/messages?limit=12", key)
        if s2 == 200:
            for m in res.get("messages") or []:
                if seat in str(m.get("from", "")) and subject[:SUBJECT_STEM] in str(m.get("subject", "")):
                    s3, full = api(
                        "GET",
                        f"/inboxes/{urllib.parse.quote(sender)}/messages/{urllib.parse.quote(m['message_id'])}",
                        key,
                    )
                    return strip_quote_trail(full.get("text") or "")
        time.sleep(10)
    return None


# ---------------------------------------------------------------------------
# Microsoft Graph transport (ADR 0078 seats).
#
# Asymmetric on purpose. OUT: the admin address on scope.admins is a human
# mailbox in some tenant this tool has no credential for, so the ask ships
# through Resend from a domain we control. BACK: the reply is read from the SEAT
# mailbox's Sent Items with the seat's own app-only Graph credential, because
# that folder is the one place the Operator's answer is observable to us.
# ---------------------------------------------------------------------------


def slug_env_suffix(slug: str) -> str:
    """`ashton-price` -> `ASHTON_PRICE`.

    Mirrors provision-customer.sh (`tr '[:lower:]-' '[:upper:]_' | tr -cd
    'A-Z0-9_'`) so this tool reaches for the same variable the provisioner
    staged. A private derivation here would fail on exactly the seat it is
    meant to reach.
    """
    return re.sub(r"[^A-Z0-9_]", "", slug.upper().replace("-", "_"))


def msgraph_secret_env_names(slug: str, email: dict) -> tuple[str, str]:
    """(per-seat variable, global fallback) holding the Graph client secret.

    The NAME comes from `msgraph_auth.secret_ref` with its `fly-secret:` prefix
    stripped, same as the provisioner; the value is never in customer.yaml
    (ADR 0010, client custody).
    """
    ref = str((email.get("msgraph_auth") or {}).get("secret_ref", "")).strip()
    base = ref[len("fly-secret:") :] if ref.startswith("fly-secret:") else ""
    base = base or "MSGRAPH_CLIENT_SECRET"
    return f"{base}__{slug_env_suffix(slug)}", base


def _open(req: urllib.request.Request, timeout: int = 45) -> tuple[int, str]:
    """One HTTP round trip, returning (status, body).

    Errors are returned rather than raised so a caller decides what a non-2xx
    means. No request header is ever echoed, so a bearer cannot reach a log.

    A 2xx body is returned WHOLE. The first live run against the paying seat
    crashed because this cap was 400 characters on every path: a token grant
    is about 1,500 characters and its access_token string opens near character
    78, so the truncated JSON was unterminated and the harness died before the
    first poll. The mocked tests never saw a wire-sized body. Only the error
    body is capped, since that is the one a caller may echo.
    """
    try:
        # Every URL here is one of this module's own constants concatenated with
        # a path this module builds; no caller supplies a scheme or host, so the
        # file:// concern the rule guards does not arise. Same suppression and
        # reasoning as `api` above.
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]


def graph_token(tenant_id: str, client_id: str, client_secret: str) -> tuple[str, int]:
    """Mint an app-only bearer. Returns (token, seconds until it expires)."""
    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": GRAPH_SCOPE,
            "grant_type": "client_credentials",
        }
    ).encode()
    req = urllib.request.Request(
        f"{TOKEN_HOST}/{urllib.parse.quote(tenant_id)}/oauth2/v2.0/token",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    status, raw = _open(req)
    if status < 200 or status >= 300:
        # Status only. The response body is not echoed, because a rejected grant
        # is exactly the moment a credential is most likely to end up in a log.
        _die(
            f"the Microsoft identity platform refused the client-credentials grant "
            f"(HTTP {status}). Check msgraph_auth.tenant_id and client_id in "
            f"customer.yaml, and that the vaulted client secret has not expired."
        )
    payload = json.loads(raw or "{}") or {}
    token = str(payload.get("access_token") or "")
    ttl = int(payload.get("expires_in") or 3600)
    if not token:
        _die("the token response carried no access_token")
    return token, ttl


class GraphToken:
    """Holds the app-only bearer and re-mints it on expiry.

    A full card is 17 commands at up to 420s each, which outlasts a Graph
    token's hour. The value is never printed, logged, or written to the
    transcript.
    """

    def __init__(self, tenant_id: str, client_id: str, client_secret: str) -> None:
        self._tenant = tenant_id
        self._client = client_id
        self._secret = client_secret
        self._token = ""
        self._expires_at = 0.0

    def value(self) -> str:
        if not self._token or time.time() >= self._expires_at:
            self._token, ttl = graph_token(self._tenant, self._client, self._secret)
            self._expires_at = time.time() + max(ttl - 300, 60)
        return self._token


def graph_get(path: str, token: str, headers: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        GRAPH_BASE + path,
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            **(headers or {}),
        },
    )
    status, raw = _open(req)
    if status != 200:
        return status, {"raw": raw}
    try:
        return status, json.loads(raw or "{}")
    except json.JSONDecodeError:
        return status, {}


def resend_send(sender: str, seat: str, subject: str, text: str, key: str) -> tuple[int, str]:
    """Ship the ask from the admin address through Resend.

    The sender's domain must be Resend-verified with sending enabled, or Resend
    refuses; smd.services is. A non-2xx is returned, never swallowed, so the
    caller can record a refusal rather than invent silence.
    """
    req = urllib.request.Request(
        RESEND_URL,
        data=json.dumps({"from": sender, "to": [seat], "subject": subject, "text": text}).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    return _open(req)


def parse_stamp(raw: object) -> datetime | None:
    """Graph's ISO-8601 timestamp as an aware datetime, or None if unreadable.

    Parsed rather than string-compared: Graph emits up to seven fractional
    digits, and `...06.0000000Z` sorts BELOW `...06Z` lexically, so a text
    compare would call a later message earlier inside the same second.
    """
    s = str(raw or "").strip()
    if not s:
        return None
    s = s[:-1] + "+00:00" if s.endswith("Z") else s
    trimmed = re.match(r"^(.*\.\d{6})\d*([+-]\d{2}:\d{2})$", s)
    if trimmed:
        s = trimmed.group(1) + trimmed.group(2)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def recipient_addresses(msg: dict) -> set[str]:
    out = set()
    for r in msg.get("toRecipients") or []:
        addr = ((r or {}).get("emailAddress") or {}).get("address")
        if addr:
            out.add(str(addr).strip().lower())
    return out


def msgraph_reply_matches(msg: dict, sender: str, subject: str, floor: datetime) -> bool:
    """True only for the answer to THIS ask.

    Three conjuncts, each closing a different way the record could be wrong. The
    recipient, because a reply to somebody else is not our reply. The subject
    stem, the same rule the AgentMail path uses. And the send time, because a
    prior rehearsal's answer sits in that folder forever: reading one as this
    ask's reply is a silent false positive, and a harness that can report a
    stale answer as a fresh one measures nothing.
    """
    if sender.strip().lower() not in recipient_addresses(msg):
        return False
    if subject[:SUBJECT_STEM] not in str(msg.get("subject", "")):
        return False
    stamp = parse_stamp(msg.get("sentDateTime"))
    return stamp is not None and stamp > floor


def ask_msgraph(
    sender: str,
    seat: str,
    subject: str,
    text: str,
    resend_key: str,
    token: GraphToken,
    timeout: int,
) -> str | None:
    floor = datetime.now(timezone.utc)
    status, body = resend_send(sender, seat, subject, text, resend_key)
    if status < 200 or status >= 300:
        print(f"    SEND REFUSED by Resend (HTTP {status}): {body}")
        return None
    path = f"/users/{urllib.parse.quote(seat)}/mailFolders/SentItems/messages{SENT_ITEMS_QUERY}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        st, res = graph_get(path, token.value(), TEXT_BODY_PREFER)
        if st == 200:
            for m in res.get("value") or []:
                if msgraph_reply_matches(m, sender, subject, floor):
                    return strip_quote_trail(str((m.get("body") or {}).get("content") or ""))
        time.sleep(10)
    return None


def agentmail_asker(sender: str, seat: str, timeout: int):
    key = os.environ.get("AGENTMAIL_API_KEY")
    if not key:
        _die("AGENTMAIL_API_KEY unset — run under `infisical run --env=prod --path=/ss`")

    def asker(subject: str, text: str) -> str | None:
        return ask(sender, seat, subject, text, key, timeout)

    return asker


def msgraph_asker(slug: str, cfg: dict, sender: str, seat: str, timeout: int):
    """Bind the Graph transport, or exit naming the one thing that is missing."""
    resend_key = os.environ.get("RESEND_API_KEY")
    if not resend_key:
        _die(
            f"RESEND_API_KEY unset. This seat's Email connector is msgraph, so the "
            f"ask is sent from {sender} through Resend rather than an AgentMail "
            f"inbox. To fix: {INFISICAL_RECIPE}."
        )
    email = email_connector(cfg)
    auth = email.get("msgraph_auth") or {}
    per_seat, fallback = msgraph_secret_env_names(slug, email)
    secret = os.environ.get(per_seat) or os.environ.get(fallback)
    if not secret:
        _die(
            f"{per_seat} unset (and no {fallback} fallback). It holds this seat's "
            f"Graph client secret, which is how the reply is read back out of "
            f"{seat}'s Sent Items. To fix: {INFISICAL_RECIPE}."
        )
    tenant = str(auth.get("tenant_id") or "").strip()
    client = str(auth.get("client_id") or "").strip()
    if not tenant or not client:
        _die(
            "msgraph_auth is missing tenant_id or client_id, so no app-only token "
            "can be minted and the reply could never be read"
        )
    token = GraphToken(tenant, client, secret)

    def asker(subject: str, text: str) -> str | None:
        return ask_msgraph(sender, seat, subject, text, resend_key, token, timeout)

    return asker


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("slug")
    ap.add_argument("--as", dest="sender", help="address to speak as; must be on scope.admins")
    ap.add_argument("--out", default=None)
    ap.add_argument("--only", type=int, default=None)
    ap.add_argument("--list", action="store_true", dest="just_list")
    ap.add_argument("--timeout", type=int, default=420)
    args = ap.parse_args()

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", args.slug):
        _die(f"invalid slug {args.slug!r}")

    card, cfg = load(args.slug)
    commands = unlocked_commands(card)

    if args.just_list:
        for i, c in enumerate(commands, 1):
            say = " ".join(str(c.get("say", "")).split())
            flag = " [admin]" if c.get("admin_only") else ""
            print(f"{i:>2}. [{c['stage']}] {c.get('backed_by')}{flag}\n    {say}")
        print(f"\n{len(commands)} unlocked command(s). Nothing sent.")
        return 0

    if not args.sender:
        _die("--as is required (the address to rehearse as)")

    # Fail closed on an unauthored sender: the card's admin_only commands resolve
    # authority from scope.admins, so rehearsing as anyone else silently measures
    # the refusal path and would read as a product defect.
    admins = {str(a).strip().lower() for a in ((cfg.get("scope") or {}).get("admins") or [])}
    if args.sender.strip().lower() not in admins:
        _die(
            f"{args.sender} is not on this seat's scope.admins, so admin-only commands "
            f"would be refused for the sender rather than answered. Authored admins: "
            f"{', '.join(sorted(admins)) or '(none)'}"
        )

    adapter = email_adapter(cfg)
    seat = seat_inbox(cfg)
    if adapter == "msgraph":
        ask_one = msgraph_asker(args.slug, cfg, args.sender, seat, args.timeout)
    else:
        ask_one = agentmail_asker(args.sender, seat, args.timeout)

    chosen = [commands[args.only - 1]] if args.only else commands
    if args.only and not (1 <= args.only <= len(commands)):
        _die(f"--only must be 1..{len(commands)}")

    out_path = Path(args.out) if args.out else Path(f"card-transcript-{args.slug}.txt")
    silent = 0
    with out_path.open("w") as fh:
        fh.write(f"REHEARSAL TRANSCRIPT — {args.slug} — spoken as {args.sender}\n")
        fh.write(f"Seat: {seat} (transport: {adapter or 'agentmail'})\n")
        fh.write(
            "\nThis file is EVIDENCE, not a verdict. Each reply is paired with the "
            "card's own expected and falsifier so a reader who did not run the "
            "rehearsal can judge it. Nothing here has been graded.\n"
        )
        for i, c in enumerate(chosen, 1):
            n = args.only or i
            say = " ".join(str(c.get("say", "")).split())
            subject = f"Card {n} - {c.get('backed_by', 'command')}"
            print(f"[{n}/{len(commands)}] {say[:70]}")
            reply = ask_one(subject, say)
            fh.write(f"\n\n{'=' * 72}\n### Command {n} [{c['stage']}] backed_by: {c.get('backed_by')}\n")
            fh.write(f"SAID: {say}\n\nEXPECTED: {' '.join(str(c.get('expected', '')).split())}\n")
            fh.write(f"\nFALSIFIER: {' '.join(str(c.get('falsifier', '')).split())}\n\n")
            if reply is None:
                silent += 1
                fh.write(f"*** NO REPLY within {args.timeout}s ***\n")
                print("    NO REPLY")
            else:
                fh.write(f"REPLY:\n{reply}\n")
            fh.flush()

    print(f"\nTranscript: {out_path}")
    print(f"{len(chosen) - silent}/{len(chosen)} answered; {silent} silent.")
    print("Nothing has been graded — read each reply against its expected and falsifier.")
    return 1 if silent else 0


if __name__ == "__main__":
    sys.exit(main())
