#!/usr/bin/env python3
"""Boot-smoke proof that the agent's Graph credential cannot read a rostered
staff member's mailbox, on a seat that may send AS staff (ADR 0089).

WHY. The approval email carries approve buttons, and a button is a signed
one-decision link (amendment 5a). The link is a key, so the question is who can
hold it. The broker sends that email with no Sent Items copy, precisely so the
only mailbox holding the link is the staff member's own. That control is worth
exactly as much as the answer to this question: can the agent read THAT mailbox?
An app-only Graph registration reads every mailbox in the tenant unless the
tenant scopes it with an ApplicationAccessPolicy. Where the policy is missing,
the Operator can read its approver's inbox, lift its own approve link, and press
its own button. No plugin fence can prevent that; only the tenant can.

WHAT THIS DOES. Runs as ROOT under boot-smoke. Reads the agent-uid gateway's
environ for the msgraph read credential, mints an app-only token, and asks Graph
for the INBOX METADATA (id only, never a message, never a body) of EVERY
address in ``scope.staff_send_as``, in authored order. Each approver must answer
403: the policy is scoping this app away from that inbox. An approver answering
ALLOWED is the failure, and it names the mailbox. Every approver, not the first:
the scope group may lawfully hold named staff mailboxes (``staff_mailbox_reads``),
so one refused approver says nothing about the next.

WHAT MAKES IT ABLE TO FAIL. Remove the ApplicationAccessPolicy for the read app
in the tenant, or add any approver to the read app's scope group, and this
exits 1. A seat that authors staff_send_as and carries no msgraph credential
exits 1, because "could not ask" must never read as "cannot read". A token mint
Microsoft refused exits 1, for the same reason.

VACUOUS PASS, STATED. A seat authoring no staff_send_as passes with a line
saying so: it mints no approve links, because it proposes no staff sends.

FOUR ANSWERS, NOT TWO (2026-11-12). Each approver's answer is classified by
``classify``, in code, into exactly one of:

  DENIED     403. The policy scopes the read app away. Pass.
  NOT_FOUND  404. Graph does not know the address as asked: an alias, a typo,
             an unlicensed or mailbox-less user. The app may still read the real
             mailbox, so this proves nothing. Never a pass: inconclusive.
  ALLOWED    2xx, or any other answer that is neither a refusal nor weather.
             Exit 1, exactly as before this change.
  UNKNOWN    5xx, 429, a timeout (DNS included), a dropped connection, a
             response with no readable status line, or an approver not asked
             because the time budget ran out. Never a pass: inconclusive.

Before this, every answer that was not 403/404 read as "can read", so a
Microsoft 502/503 (2026-11-12, about 18:10Z to at least 23:28Z) was reported as
a confirmed breach and the twin seat rolled back every release behind it; and a
404 passed, which proves nothing. Every approver is asked once, then the
UNKNOWN ones are asked again, up to MAX_ATTEMPTS, honouring Retry-After, and
never starting a request that could not finish inside BUDGET_S (which sits
inside the laptop smoke's 90 s ssh budget). An ALLOWED answer exits 1 at once,
whatever any other approver said. Otherwise any NOT_FOUND or UNKNOWN prints an
``INCONCLUSIVE:`` line naming each address and status and exits
``INCONCLUSIVE_RC`` (3). The self-test and the laptop smoke read that as a
WARNING, never a pass, and the self-test turns a persistent one into a failure
that pages (selftest.sh, judge_inconclusive).

The token mint is held to the same line: a mint Microsoft did not answer is
UNKNOWN; a mint it refused (a 4xx, or no token in the reply) still exits 1.

Usage:  msgraph-read-app-cannot-read-staff-probe.py [customer-yaml] [agent-username]
"""

from __future__ import annotations

import email.utils
import http.client
import json
import os
import pwd
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any, NamedTuple

_DEFAULT_YAML = "/var/lib/smd-config/customer.yaml"
_DEFAULT_USER = "hermes"
_VARS = ("MSGRAPH_TENANT_ID", "MSGRAPH_CLIENT_ID", "MSGRAPH_CLIENT_SECRET")
_GRAPH = "https://graph.microsoft.com"
_LOGIN = "https://login.microsoftonline.com"

# The exit code that means "not proven either way": neither a pass nor a
# breach. selftest.sh and boot-smoke-test.sh read it as a warning.
INCONCLUSIVE_RC = 3

DENIED = "denied"
NOT_FOUND = "not_found"
ALLOWED = "allowed"
UNKNOWN = "unknown"

MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 2.0
RETRY_AFTER_CAP_S = 20.0
# The whole probe, mint included, ends inside BUDGET_S: a request is started
# only when its hard wall clock (REQUEST_TIMEOUT_S + WALL_SLACK_S, which also
# bounds DNS) fits before the deadline.
BUDGET_S = 60.0
REQUEST_TIMEOUT_S = 15.0
WALL_SLACK_S = 2.0
NOT_ASKED = "not asked, the probe's time budget was spent"

# What "no answer" looks like from urllib: a refused or dropped connection, a
# timeout, a status line that is not HTTP. An HTTPError is an answer and is
# classified by its status instead.
_NO_ANSWER = (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException)


class Reply(NamedTuple):
    """One answer from Microsoft. ``status`` is None when no HTTP answer came back."""

    status: int | None
    retry_after: float | None = None
    error: str = ""

    def says(self) -> str:
        if self.status is not None:
            return f"HTTP {self.status}"
        return f"no answer ({self.error or 'unknown error'})"


class Unanswered(Exception):
    """The token endpoint did not answer (weather), as opposed to refusing."""

    def __init__(self, reply: Reply) -> None:
        super().__init__(reply.says())
        self.reply = reply


class WallClockTimeout(TimeoutError):
    """A request (DNS included) outran its hard wall clock."""


Mint = Callable[[str, str, str], str]
Ask = Callable[[str, str], "Reply | int"]
Sleep = Callable[[float], None]
Clock = Callable[[], float]
Outcome = tuple[str, Reply, int]


def classify(reply: Reply) -> str:
    """DENIED, NOT_FOUND, ALLOWED or UNKNOWN for one answer. The only place this is decided."""
    status = reply.status
    if status is None or status == 429 or 500 <= status <= 599:
        return UNKNOWN
    if status == 403:
        return DENIED
    if status == 404:
        return NOT_FOUND
    # 2xx is Microsoft saying yes. Anything else that is neither a refusal nor
    # weather failed before 2026-11-12 and still does: tighten, never loosen.
    return ALLOWED


def retry_after_seconds(value: str | None) -> float | None:
    """Retry-After in seconds (delta-seconds or an HTTP date), or None when absent or unreadable."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    return max(0.0, when.timestamp() - time.time())


def _http_error_reply(exc: urllib.error.HTTPError) -> Reply:
    headers = exc.headers
    return Reply(int(exc.code), retry_after_seconds(headers.get("Retry-After") if headers is not None else None))


def _wait_for(attempt: int, reply: Reply) -> float:
    if reply.retry_after is not None:
        return min(reply.retry_after, RETRY_AFTER_CAP_S)
    return BACKOFF_BASE_S * (2 ** (attempt - 1))


def _fits(clock: Clock, deadline: float, wait: float) -> bool:
    """Would a request started after ``wait`` seconds finish before the deadline?"""
    return clock() + wait + REQUEST_TIMEOUT_S + WALL_SLACK_S <= deadline


def bounded(fn: Callable[[], Any], seconds: float) -> Any:
    """``fn()`` under a hard wall clock. urllib's timeout does not cover DNS
    (getaddrinfo has none), so the call runs on a daemon thread and a call that
    outlives ``seconds`` raises WallClockTimeout; the thread is abandoned."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
            box["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise WallClockTimeout(f"no answer in {seconds:.0f}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def staff_addresses(yaml_text: str) -> list[str]:
    """Every address in ``scope.staff_send_as``. Parsed, not grepped."""
    import yaml

    data = yaml.safe_load(yaml_text) or {}
    scope = data.get("scope") if isinstance(data, dict) else None
    entries = scope.get("staff_send_as") if isinstance(scope, dict) else None
    if not isinstance(entries, list):
        return []
    return [str(e["address"]).strip() for e in entries if isinstance(e, dict) and str(e.get("address") or "").strip()]


def _environ(proc_root: str, pid: str) -> dict[str, str] | None:
    try:
        with open(os.path.join(proc_root, pid, "environ"), "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    env: dict[str, str] = {}
    for entry in raw.split(b"\x00"):
        if entry:
            name, _, value = entry.partition(b"=")
            env[name.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return env


def read_agent_credential(proc_root: str, uid: int) -> dict[str, str] | None:
    """The msgraph read credential from the first agent-uid process carrying all three vars."""
    for pid in sorted(os.listdir(proc_root)):
        if not pid.isdigit():
            continue
        try:
            if os.stat(os.path.join(proc_root, pid)).st_uid != uid:
                continue
        except OSError:
            continue
        env = _environ(proc_root, pid) or {}
        if all(env.get(v, "").strip() for v in _VARS):
            return {v: env[v].strip() for v in _VARS}
    return None


def _mint_once(tenant: str, client_id: str, secret: str, base: str, timeout: float) -> str:
    data = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": secret,
            "scope": "https://graph.microsoft.com/.default",
        }
    ).encode()
    url = f"{base}/{urllib.parse.quote(tenant, safe='')}/oauth2/v2.0/token"
    request = urllib.request.Request(url, data=data, method="POST")  # noqa: S310 - fixed https login host; tenant id is url-quoted
    try:
        # Fixed https login host; the tenant id is url-quoted.
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https login host; tenant id is url-quoted
            return str(json.loads(response.read().decode("utf-8")).get("access_token") or "")
    except urllib.error.HTTPError as exc:
        reply = _http_error_reply(exc)
        if classify(reply) == UNKNOWN:
            raise Unanswered(reply) from exc
        raise
    except _NO_ANSWER as exc:
        raise Unanswered(Reply(None, None, type(exc).__name__)) from exc


def _urllib_mint(
    tenant: str, client_id: str, secret: str, base: str = _LOGIN, timeout: float = REQUEST_TIMEOUT_S
) -> str:
    """An app-only token. Raises Unanswered for weather; any other failure propagates."""
    try:
        return bounded(lambda: _mint_once(tenant, client_id, secret, base, timeout), timeout + WALL_SLACK_S)
    except WallClockTimeout as exc:
        raise Unanswered(Reply(None, None, type(exc).__name__)) from exc


def _ask_once(token: str, mailbox: str, base: str, timeout: float) -> Reply:
    url = base + "/v1.0/users/" + urllib.parse.quote(mailbox, safe="") + "/mailFolders/inbox?$select=id"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})  # noqa: S310 - fixed https graph host; mailbox is url-quoted
    try:
        # Fixed https graph host; the mailbox address is url-quoted.
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https graph host; mailbox is url-quoted
            return Reply(int(response.status))
    except urllib.error.HTTPError as exc:
        return _http_error_reply(exc)
    except _NO_ANSWER as exc:
        return Reply(None, None, type(exc).__name__)


def _urllib_ask(token: str, mailbox: str, base: str = _GRAPH, timeout: float = REQUEST_TIMEOUT_S) -> Reply:
    """Microsoft's answer to an inbox METADATA read. Never a message, never a body.

    The status line decides; the body is never read. ``base`` and ``timeout``
    are seams for the tests, which point this at a local server.
    """
    try:
        return bounded(lambda: _ask_once(token, mailbox, base, timeout), timeout + WALL_SLACK_S)
    except WallClockTimeout as exc:
        return Reply(None, None, type(exc).__name__)


def _as_reply(answer: Reply | int) -> Reply:
    return answer if isinstance(answer, Reply) else Reply(int(answer))


def survey(
    ask: Ask, token: str, staff: list[str], deadline: float, sleep: Sleep = time.sleep, clock: Clock = time.monotonic
) -> dict[str, Outcome]:
    """(verdict, last reply, attempts) per approver.

    Every approver is asked once before any is asked again, so one approver in
    an outage cannot spend the budget another approver's breach needs. Only
    UNKNOWN is asked again. Returns at the first ALLOWED. An approver the
    budget never reached stays UNKNOWN (NOT_ASKED), never a pass.
    """
    results: dict[str, Outcome] = {m: (UNKNOWN, Reply(None, None, NOT_ASKED), 0) for m in staff}
    pending = list(staff)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if attempt > 1:
            wait = max(_wait_for(attempt - 1, results[m][1]) for m in pending)
            if not _fits(clock, deadline, wait):
                break
            sleep(wait)
        still: list[str] = []
        for mailbox in pending:
            if not _fits(clock, deadline, 0.0):
                still.append(mailbox)
                continue
            reply = _as_reply(ask(token, mailbox))
            results[mailbox] = (classify(reply), reply, attempt)
            if results[mailbox][0] == ALLOWED:
                return results
            if results[mailbox][0] == UNKNOWN:
                still.append(mailbox)
        pending = still
        if not pending:
            break
    return results


def _mint_settled(
    mint: Mint, credential: dict[str, str], deadline: float, sleep: Sleep, clock: Clock
) -> tuple[str, Reply | None, int]:
    """(token, None, attempts) once minted; ("", reply, attempts) when it settled UNKNOWN.

    Any exception other than Unanswered propagates: a refused mint is a failure.
    """
    attempt = 1
    while True:
        try:
            token = mint(
                credential["MSGRAPH_TENANT_ID"],
                credential["MSGRAPH_CLIENT_ID"],
                credential["MSGRAPH_CLIENT_SECRET"],
            )
            return token, None, attempt
        except Unanswered as exc:
            reply = exc.reply
        wait = _wait_for(attempt, reply)
        if attempt >= MAX_ATTEMPTS or not _fits(clock, deadline, wait):
            return "", reply, attempt
        sleep(wait)
        attempt += 1


def _breach_line(mailbox: str, reply: Reply) -> str:
    return (
        f"FAIL: the read app can read {mailbox}'s mailbox ({reply.says()}), and "
        f"{mailbox} approves drafts sent in their name. The read app's "
        "ApplicationAccessPolicy scope group may hold the Operator's own mailbox and "
        "the staff mailboxes the firm authored under staff_mailbox_reads, never a "
        "person on scope.staff_send_as: their inbox holds the approve links, and "
        "the agent could lift its own link and press it. Take this approver out of "
        "the scope group."
    )


def _inconclusive_line(results: dict[str, Outcome]) -> str:
    parts: list[str] = []
    for mailbox, (verdict, reply, tries) in results.items():
        if verdict == NOT_FOUND:
            parts.append(
                f"cannot prove the policy refuses {mailbox}: Graph does not know this address ({reply.says()}); "
                "ask by the user principal name"
            )
        elif verdict == UNKNOWN:
            parts.append(f"Microsoft did not answer for {mailbox}: {reply.says()} after {tries} attempt(s)")
    refused = [f"{m} {r.says()}" for m, (v, r, _t) in results.items() if v == DENIED]
    also = f"; refused: {'; '.join(refused)}" if refused else ""
    return f"INCONCLUSIVE: {'; '.join(parts)}{also}. Not a pass and not a breach: this approver's inbox is unproven."


def run(
    yaml_path: str,
    user: str,
    proc_root: str = "/proc",
    mint: Mint = _urllib_mint,
    ask: Ask = _urllib_ask,
    sleep: Sleep = time.sleep,
    clock: Clock = time.monotonic,
) -> int:
    try:
        yaml_text = open(yaml_path, encoding="utf-8").read()
    except OSError as exc:
        print(f"FAIL: cannot read {yaml_path}: {exc}")
        return 1
    staff = staff_addresses(yaml_text)
    if not staff:
        print("vacuous: seat authors no scope.staff_send_as; it mints no approve links")
        return 0
    try:
        uid = pwd.getpwnam(user).pw_uid
    except KeyError:
        print(f"FAIL: no such user {user}")
        return 1
    credential = read_agent_credential(proc_root, uid)
    if credential is None:
        print("FAIL: seat authors staff_send_as but no agent process carries the msgraph read credential; cannot ask")
        return 1
    deadline = clock() + BUDGET_S
    try:
        token, unanswered, tries = _mint_settled(mint, credential, deadline, sleep, clock)
    except Exception as exc:  # noqa: BLE001 - could not ask is a failure, not a pass
        print(f"FAIL: could not mint an app-only token to ask: {type(exc).__name__}")
        return 1
    if unanswered is not None:
        print(
            f"INCONCLUSIVE: Microsoft's token endpoint did not answer ({unanswered.says()} after {tries} "
            "attempt(s)), so no approver's mailbox could be asked. Not a pass: every approver's inbox is unproven."
        )
        return INCONCLUSIVE_RC
    if not token:
        print("FAIL: the tenant returned no token; cannot ask")
        return 1
    results = survey(ask, token, staff, deadline, sleep, clock)
    for mailbox, (verdict, reply, _tries) in results.items():
        if verdict == ALLOWED:
            print(_breach_line(mailbox, reply))
            return 1
    if any(verdict != DENIED for verdict, _r, _t in results.values()):
        print(_inconclusive_line(results))
        return INCONCLUSIVE_RC
    refused = "; ".join(f"{m} {r.says()}" for m, (_v, r, _t) in results.items())
    print(f"pass: the read app is refused every approver's mailbox ({refused})")
    return 0


def main(argv: list[str]) -> int:
    yaml_path = argv[1] if len(argv) > 1 else _DEFAULT_YAML
    user = argv[2] if len(argv) > 2 else _DEFAULT_USER
    return run(yaml_path, user)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
