"""msgraph-read-app-cannot-read-staff-probe.py: every verdict arm, and no secret out.

The other half of ADR 0089 amendment 5a. The approve buttons are signed links,
and the broker keeps no Sent Items copy of the email carrying them, so the only
mailbox holding a link is the approver's. This probe asks Microsoft whether the
agent's read app can open that mailbox; if it can, the Operator can lift its own
approve link and press its own button. Driven against a fake /proc root, a fake
mint and a fake Graph.
"""

from __future__ import annotations

import email.utils
import http.server
import importlib.util
import os
import threading
import time
import urllib.error
from collections.abc import Iterator
from pathlib import Path

import pytest

PROBE = Path(__file__).resolve().parents[2] / "templates" / "msgraph-read-app-cannot-read-staff-probe.py"


def _load():
    spec = importlib.util.spec_from_file_location("msgraph_read_app_cannot_read_staff_probe", PROBE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


probe = _load()
SECRET = "read-app-secret-value-0123456789"
UID = os.getuid()
STAFF = "paralegal@firm.example"

WITH_STAFF = f"scope:\n  staff_send_as:\n    - address: {STAFF}\n      name: Pat\n"
WITHOUT_STAFF = "scope:\n  admins:\n    - chris@firm.example\n"


def _seat(tmp_path: Path, yaml_text: str, *, env: bool = True) -> tuple[str, str]:
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(yaml_text)
    proc = tmp_path / "proc"
    (proc / "123").mkdir(parents=True)
    body = b"PATH=/bin\x00"
    if env:
        body += (
            b"MSGRAPH_TENANT_ID=tid\x00MSGRAPH_CLIENT_ID=cid-read\x00MSGRAPH_CLIENT_SECRET=" + SECRET.encode() + b"\x00"
        )
    (proc / "123" / "environ").write_bytes(body)
    return str(yaml_path), str(proc)


def _user() -> str:
    import pwd

    return pwd.getpwuid(UID).pw_name


def _run(tmp_path: Path, yaml_text: str, status: int, *, env: bool = True):
    yaml_path, proc = _seat(tmp_path, yaml_text, env=env)
    asked: list[str] = []

    def ask(_token: str, mailbox: str) -> int:
        asked.append(mailbox)
        return status

    rc = probe.run(yaml_path, _user(), proc, mint=lambda *_: "token", ask=ask)
    return rc, asked


def test_a_scoped_read_app_passes(tmp_path: Path, capsys) -> None:
    rc, asked = _run(tmp_path, WITH_STAFF, 403)
    out = capsys.readouterr().out
    assert (rc, asked) == (0, [STAFF])
    assert "refused" in out and "403" in out


def test_an_address_graph_does_not_know_is_unproven_not_a_pass(tmp_path: Path, capsys) -> None:
    # CHANGED 2026-11-12 (was test_a_missing_mailbox_also_passes). Graph answers
    # 404 for an alias, a typo, an unlicensed or mailbox-less user, even where
    # the app could read the real mailbox, so a 404 proves nothing about the
    # policy. It is inconclusive (exit 3, a warning), never a pass and never a
    # breach, and the line names the address.
    rc, _asked = _run(tmp_path, WITH_STAFF, 404)
    out = capsys.readouterr().out
    assert rc == probe.INCONCLUSIVE_RC
    assert f"cannot prove the policy refuses {STAFF}: Graph does not know this address (HTTP 404)" in out
    assert "pass:" not in out and "can read" not in out


def test_an_unscoped_read_app_fails_and_names_the_fix(tmp_path: Path, capsys) -> None:
    rc, _asked = _run(tmp_path, WITH_STAFF, 200)
    out = capsys.readouterr().out
    assert rc == 1
    assert "can read" in out and "ApplicationAccessPolicy" in out


SECOND = "associate@firm.example"
TWO_APPROVERS = WITH_STAFF + f"    - address: {SECOND}\n      name: Sam\n"


def _run_per_mailbox(tmp_path: Path, yaml_text: str, statuses: dict[str, int]):
    yaml_path, proc = _seat(tmp_path, yaml_text)
    asked: list[str] = []

    def ask(_token: str, mailbox: str) -> int:
        asked.append(mailbox)
        return statuses[mailbox]

    rc = probe.run(yaml_path, _user(), proc, mint=lambda *_: "token", ask=ask)
    return rc, asked


def test_every_approver_is_asked_and_all_refused_passes(tmp_path: Path, capsys) -> None:
    # CHANGED 2026-11-12: SECOND answered 404 here, which passed; only 403 is a
    # refusal now (see test_an_address_graph_does_not_know_is_unproven_not_a_pass).
    rc, asked = _run_per_mailbox(tmp_path, TWO_APPROVERS, {STAFF: 403, SECOND: 403})
    out = capsys.readouterr().out
    assert (rc, asked) == (0, [STAFF, SECOND])
    assert "every approver" in out and STAFF in out and SECOND in out


def test_one_refused_and_one_unknown_address_is_inconclusive(tmp_path: Path, capsys) -> None:
    rc, asked = _run_per_mailbox(tmp_path, TWO_APPROVERS, {STAFF: 403, SECOND: 404})
    out = capsys.readouterr().out
    assert (rc, asked) == (probe.INCONCLUSIVE_RC, [STAFF, SECOND])
    assert f"cannot prove the policy refuses {SECOND}" in out and f"refused: {STAFF} HTTP 403" in out


def test_a_breach_beside_an_unknown_address_still_fails(tmp_path: Path, capsys) -> None:
    rc, _asked = _run_per_mailbox(tmp_path, TWO_APPROVERS, {STAFF: 404, SECOND: 200})
    assert rc == 1 and f"can read {SECOND}'s mailbox" in capsys.readouterr().out


def test_a_readable_second_approver_fails_though_the_first_is_refused(tmp_path: Path, capsys) -> None:
    # The shape the first cut could not see: approver one is out of the read
    # app's scope group, approver two is in it (say, added alongside a named
    # staff mailbox). Asking only the first would pass this seat.
    rc, asked = _run_per_mailbox(tmp_path, TWO_APPROVERS, {STAFF: 403, SECOND: 200})
    out = capsys.readouterr().out
    assert (rc, asked) == (1, [STAFF, SECOND])
    assert f"can read {SECOND}'s mailbox" in out
    # The failure states the authored policy: named staff mailboxes may be in
    # the scope group, approvers may not.
    assert "staff_mailbox_reads" in out and "never a person on scope.staff_send_as" in out


def test_the_first_readable_approver_stops_the_probe(tmp_path: Path, capsys) -> None:
    rc, asked = _run_per_mailbox(tmp_path, TWO_APPROVERS, {STAFF: 200, SECOND: 403})
    assert (rc, asked) == (1, [STAFF])
    assert f"can read {STAFF}'s mailbox" in capsys.readouterr().out


def test_a_seat_without_staff_send_as_passes_vacuously(tmp_path: Path, capsys) -> None:
    rc, asked = _run(tmp_path, WITHOUT_STAFF, 200, env=False)
    assert (rc, asked) == (0, [])
    assert "vacuous" in capsys.readouterr().out


def test_no_credential_to_ask_with_fails_closed(tmp_path: Path, capsys) -> None:
    rc, asked = _run(tmp_path, WITH_STAFF, 403, env=False)
    assert (rc, asked) == (1, [])
    assert "cannot ask" in capsys.readouterr().out


def test_a_failed_mint_fails_closed(tmp_path: Path, capsys) -> None:
    yaml_path, proc = _seat(tmp_path, WITH_STAFF)

    def boom(*_args):
        raise OSError("tenant unreachable")

    rc = probe.run(yaml_path, _user(), proc, mint=boom, ask=lambda *_: 403)
    assert rc == 1 and "could not mint" in capsys.readouterr().out


def test_the_secret_never_reaches_the_output(tmp_path: Path, capsys) -> None:
    for status in (200, 403):
        seat = tmp_path / f"s{status}"
        seat.mkdir()
        _run(seat, WITH_STAFF, status)
        assert SECRET not in capsys.readouterr().out


# ---- 2026-11-12: three answers, not two -------------------------------------
#
# Microsoft Graph returned 502/503 or timed out on the mailbox endpoint for the
# client tenant and the lab tenant from about 18:10Z to at least 23:28Z on
# 2026-11-12, while token minting kept working. The probe read every one of
# those as "the read app can read the approver's inbox", and the twin seat
# rolled back every release. An answer Microsoft did not give is UNKNOWN: never
# a pass, never a breach, exit INCONCLUSIVE_RC (3). ALLOWED still exits 1.

Reply = probe.Reply


@pytest.mark.parametrize(
    ("status", "verdict"),
    [
        (403, probe.DENIED),
        # Graph does not know the address as asked: proves nothing.
        (404, probe.NOT_FOUND),
        (200, probe.ALLOWED),
        (204, probe.ALLOWED),
        # Neither a refusal nor weather: failed before 2026-11-12, still does.
        (400, probe.ALLOWED),
        (401, probe.ALLOWED),
        (302, probe.ALLOWED),
        (429, probe.UNKNOWN),
        (500, probe.UNKNOWN),
        (502, probe.UNKNOWN),
        (503, probe.UNKNOWN),
        (504, probe.UNKNOWN),
        (None, probe.UNKNOWN),
    ],
)
def test_every_answer_is_exactly_one_of_four(status: int | None, verdict: str) -> None:
    assert probe.classify(Reply(status)) == verdict


class _Clock:
    """A fake monotonic clock that sleeping advances."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _run_script(tmp_path: Path, yaml_text: str, script: dict, mint=None):
    """Drive run() with a scripted answer list per mailbox; the last answer repeats."""
    yaml_path, proc = _seat(tmp_path, yaml_text)
    asked: list[str] = []
    clock = _Clock()

    def ask(_token: str, mailbox: str):
        asked.append(mailbox)
        answers = script[mailbox]
        return answers.pop(0) if len(answers) > 1 else answers[0]

    rc = probe.run(yaml_path, _user(), proc, mint=mint or (lambda *_: "token"), ask=ask, sleep=clock.sleep, clock=clock)
    return rc, asked, clock


def test_a_503_is_inconclusive_never_a_breach_and_never_a_pass(tmp_path: Path, capsys) -> None:
    rc, asked, clock = _run_script(tmp_path, WITH_STAFF, {STAFF: [Reply(503)]})
    out = capsys.readouterr().out
    assert rc == probe.INCONCLUSIVE_RC == 3
    assert asked == [STAFF] * probe.MAX_ATTEMPTS
    assert out.startswith("INCONCLUSIVE:") and "HTTP 503" in out
    assert f"after {probe.MAX_ATTEMPTS} attempt(s)" in out
    assert "can read" not in out and "pass:" not in out
    # Exponential backoff between attempts when Microsoft names no Retry-After.
    assert clock.slept == [2.0, 4.0]


def test_a_429_waits_the_retry_after_microsoft_names(tmp_path: Path, capsys) -> None:
    rc, asked, clock = _run_script(tmp_path, WITH_STAFF, {STAFF: [Reply(429, 7.0), Reply(403)]})
    assert (rc, asked, clock.slept) == (0, [STAFF, STAFF], [7.0])
    assert "pass" in capsys.readouterr().out


def test_a_retry_after_past_the_cap_is_capped(tmp_path: Path) -> None:
    rc, _asked, clock = _run_script(tmp_path, WITH_STAFF, {STAFF: [Reply(429, 600.0), Reply(403)]})
    assert rc == 0 and clock.slept == [probe.RETRY_AFTER_CAP_S]


def test_a_wait_that_would_overrun_the_budget_is_not_taken(tmp_path: Path, capsys) -> None:
    # Three approvers each answering 429 with a 20 s Retry-After would need 120 s
    # of waiting; the budget (inside the laptop smoke's 90 s ssh) stops it short.
    third = "clerk@firm.example"
    three = TWO_APPROVERS + f"    - address: {third}\n      name: Lee\n"
    slow = Reply(429, 20.0)
    rc, _asked, clock = _run_script(tmp_path, three, {STAFF: [slow], SECOND: [slow], third: [slow]})
    assert rc == probe.INCONCLUSIVE_RC
    assert sum(clock.slept) <= probe.BUDGET_S
    assert "HTTP 429" in capsys.readouterr().out


def test_an_outage_that_clears_on_a_retry_passes(tmp_path: Path) -> None:
    rc, asked, _clock = _run_script(tmp_path, WITH_STAFF, {STAFF: [Reply(503), Reply(502), Reply(403)]})
    assert (rc, asked) == (0, [STAFF] * 3)


def test_planted_defect_allowed_after_an_outage_still_fails(tmp_path: Path, capsys) -> None:
    # FALSIFIER for the loosening this change must not make: Microsoft is down
    # for the first approver and says YES for the second. That is a breach, and
    # it exits 1 exactly as before; the outage on the other approver does not
    # soften it.
    rc, asked, _clock = _run_script(tmp_path, TWO_APPROVERS, {STAFF: [Reply(503)], SECOND: [Reply(200)]})
    out = capsys.readouterr().out
    assert rc == 1
    # Every approver is asked once before any retry, so the breach is found
    # in the first round and the outage spends none of its budget.
    assert asked == [STAFF, SECOND]
    assert f"can read {SECOND}'s mailbox (HTTP 200)" in out


def test_three_approvers_all_timing_out_finish_inside_the_budget(tmp_path: Path, capsys) -> None:
    # Review finding (2026-11-12): checking only the next WAIT against the
    # deadline let a timeout-style outage with 2+ approvers run 81-96 s, past
    # the laptop smoke's 90 s ssh limit, which records a failure and rolls the
    # release back. Each request now must fit, wall clock included.
    third = "clerk@firm.example"
    three = TWO_APPROVERS + f"    - address: {third}\n      name: Lee\n"
    yaml_path, proc = _seat(tmp_path, three)
    clock = _Clock()
    start = clock()
    asked: list[str] = []

    def hang(_token: str, mailbox: str):
        asked.append(mailbox)
        clock.now += probe.REQUEST_TIMEOUT_S + probe.WALL_SLACK_S  # the worst a request can take
        return Reply(None, None, "WallClockTimeout")

    rc = probe.run(yaml_path, _user(), proc, mint=lambda *_: "token", ask=hang, sleep=clock.sleep, clock=clock)
    out = capsys.readouterr().out
    assert rc == probe.INCONCLUSIVE_RC
    assert clock() - start <= probe.BUDGET_S
    assert asked[:3] == [STAFF, SECOND, third]  # every approver asked once first
    for mailbox in (STAFF, SECOND, third):
        assert f"Microsoft did not answer for {mailbox}" in out


def test_an_approver_the_budget_never_reached_is_unknown_never_a_pass(tmp_path: Path, capsys) -> None:
    yaml_path, proc = _seat(tmp_path, TWO_APPROVERS)
    clock = _Clock()

    def slow_first(_token: str, mailbox: str):
        clock.now += probe.BUDGET_S  # the first answer arrives with the budget spent
        return Reply(403)

    rc = probe.run(yaml_path, _user(), proc, mint=lambda *_: "token", ask=slow_first, sleep=clock.sleep, clock=clock)
    out = capsys.readouterr().out
    assert rc == probe.INCONCLUSIVE_RC
    assert f"Microsoft did not answer for {SECOND}: no answer ({probe.NOT_ASKED})" in out


def test_a_hung_dns_lookup_is_bounded(monkeypatch) -> None:
    # urllib's timeout does not cover getaddrinfo; the wall clock does.
    import socket

    def stuck(*_args, **_kwargs):
        time.sleep(3)
        raise OSError("never")

    monkeypatch.setattr(socket, "getaddrinfo", stuck)
    began = time.monotonic()
    reply = probe._urllib_ask("token", STAFF, base="http://graph.invalid", timeout=0.2)
    assert time.monotonic() - began < 2.5
    assert (reply.status, reply.error) == (None, "WallClockTimeout")
    assert probe.classify(reply) == probe.UNKNOWN
    with pytest.raises(probe.Unanswered):
        probe._urllib_mint("tid", "cid", SECRET, base="http://login.invalid", timeout=0.2)


def test_bounded_returns_the_value_and_reraises_the_error() -> None:
    assert probe.bounded(lambda: 7, 1.0) == 7
    with pytest.raises(KeyError):
        probe.bounded(lambda: {}["x"], 1.0)
    with pytest.raises(probe.WallClockTimeout):
        probe.bounded(lambda: time.sleep(2), 0.1)


def test_an_answer_that_turns_from_weather_to_yes_fails(tmp_path: Path, capsys) -> None:
    rc, _asked, _clock = _run_script(tmp_path, WITH_STAFF, {STAFF: [Reply(503), Reply(200)]})
    assert rc == 1 and "can read" in capsys.readouterr().out


def test_one_unknown_and_one_refused_is_inconclusive_and_names_both(tmp_path: Path, capsys) -> None:
    script = {STAFF: [Reply(403)], SECOND: [Reply(None, None, "TimeoutError")]}
    rc, _asked, _clock = _run_script(tmp_path, TWO_APPROVERS, script)
    out = capsys.readouterr().out
    assert rc == probe.INCONCLUSIVE_RC
    assert f"{SECOND}: no answer (TimeoutError)" in out and f"refused: {STAFF} HTTP 403" in out


def test_a_token_endpoint_that_does_not_answer_is_inconclusive(tmp_path: Path, capsys) -> None:
    def down(*_args):
        raise probe.Unanswered(Reply(503))

    rc, asked, clock = _run_script(tmp_path, WITH_STAFF, {STAFF: [Reply(200)]}, mint=down)
    out = capsys.readouterr().out
    assert (rc, asked) == (probe.INCONCLUSIVE_RC, [])
    assert "token endpoint did not answer (HTTP 503" in out
    assert len(clock.slept) == probe.MAX_ATTEMPTS - 1


def test_a_token_endpoint_that_recovers_goes_on_to_ask(tmp_path: Path) -> None:
    calls: list[int] = []

    def flaky(*_args):
        calls.append(1)
        if len(calls) == 1:
            raise probe.Unanswered(Reply(None, None, "URLError"))
        return "token"

    rc, asked, _clock = _run_script(tmp_path, WITH_STAFF, {STAFF: [Reply(403)]}, mint=flaky)
    assert (rc, asked, len(calls)) == (0, [STAFF], 2)


def test_the_secret_never_reaches_an_inconclusive_output(tmp_path: Path, capsys) -> None:
    _run_script(tmp_path, WITH_STAFF, {STAFF: [Reply(503)]})
    assert SECRET not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("value", "want"),
    [("7", 7.0), ("0", 0.0), ("-3", 0.0), ("soon", None), (None, None), ("", None)],
)
def test_retry_after_seconds_reads_delta_seconds(value: str | None, want: float | None) -> None:
    assert probe.retry_after_seconds(value) == want


def test_retry_after_seconds_reads_an_http_date() -> None:
    got = probe.retry_after_seconds(email.utils.formatdate(time.time() + 30, usegmt=True))
    assert got is not None and 25 <= got <= 31


# ---- the real urllib code, against a local server ----------------------------


class _Graph(http.server.BaseHTTPRequestHandler):
    """Answers each request from the server's script: (status, headers, body),
    "hang" (answers later than the client waits) or "drop" (closes, no reply).
    The last step repeats."""

    def _answer(self) -> None:
        server = self.server
        server.paths.append(self.path)  # type: ignore[attr-defined]
        script = server.script  # type: ignore[attr-defined]
        step = script.pop(0) if len(script) > 1 else script[0]
        if step == "hang":
            time.sleep(1.5)
            step = (403, {}, b"")
        if step == "drop":
            self.close_connection = True
            return
        status, headers, body = step
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _answer
    do_POST = _answer

    def log_message(self, *_args: object) -> None:
        return None


@pytest.fixture
def graph() -> Iterator[http.server.ThreadingHTTPServer]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Graph)
    server.daemon_threads = True
    server.script = [(403, {}, b"")]  # type: ignore[attr-defined]
    server.paths = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _base(server: http.server.ThreadingHTTPServer) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}"


GARBAGE = b"<html>upstream connect error or disconnect/reset before headers {"
DENIED_BODY = b'{"error":{"code":"ErrorAccessDenied","message":"Access to OData is disabled."}}'


@pytest.mark.parametrize(
    ("step", "status", "verdict"),
    [
        ((503, {"Retry-After": "7"}, GARBAGE), 503, probe.UNKNOWN),
        ((502, {"Content-Type": "application/json"}, b'{"error": {"code": "Bad'), 502, probe.UNKNOWN),
        ((429, {"Retry-After": "3"}, b'{"error":{"code":"TooManyRequests"}}'), 429, probe.UNKNOWN),
        ((403, {}, DENIED_BODY), 403, probe.DENIED),
        ((404, {}, b'{"error":{"code":"ErrorInvalidUser"}}'), 404, probe.NOT_FOUND),
        # Microsoft said YES. A body that does not parse does not take that back.
        ((200, {"Content-Type": "application/json"}, b'{"id": "AAMk'), 200, probe.ALLOWED),
        ("hang", None, probe.UNKNOWN),
        ("drop", None, probe.UNKNOWN),
    ],
)
def test_the_real_ask_reads_each_answer(graph, step, status, verdict) -> None:
    graph.script = [step]
    reply = probe._urllib_ask("token", STAFF, base=_base(graph), timeout=0.4)
    assert reply.status == status
    assert probe.classify(reply) == verdict
    if status in (503, 429):
        assert reply.retry_after in (7.0, 3.0)
    if status is None:
        assert reply.error  # names the exception class, never a value
    assert graph.paths[-1].endswith("/mailFolders/inbox?$select=id")


def test_token_ok_then_502_503_timeout_is_inconclusive_not_a_breach(tmp_path: Path, graph, capsys) -> None:
    # The 2026-11-12 pattern exactly: the mint answers, the mailbox endpoint
    # answers 502, then 503, then not at all. Real urllib, real classification.
    graph.script = [(502, {}, GARBAGE), (503, {"Retry-After": "1"}, GARBAGE), "hang"]
    yaml_path, proc = _seat(tmp_path, WITH_STAFF)
    clock = _Clock()

    def ask(token: str, mailbox: str):
        return probe._urllib_ask(token, mailbox, base=_base(graph), timeout=0.4)

    rc = probe.run(yaml_path, _user(), proc, mint=lambda *_: "token", ask=ask, sleep=clock.sleep, clock=clock)
    out = capsys.readouterr().out
    assert rc == probe.INCONCLUSIVE_RC
    assert "INCONCLUSIVE" in out and "no answer" in out and "can read" not in out
    assert clock.slept == [2.0, 1.0]  # backoff, then the Retry-After Microsoft named


def test_the_real_mint_sorts_weather_from_refusal(graph) -> None:
    graph.script = [(503, {}, GARBAGE)]
    with pytest.raises(probe.Unanswered) as weather:
        probe._urllib_mint("tid", "cid", SECRET, base=_base(graph), timeout=0.4)
    assert weather.value.reply.status == 503
    graph.script = ["hang"]
    with pytest.raises(probe.Unanswered):
        probe._urllib_mint("tid", "cid", SECRET, base=_base(graph), timeout=0.4)
    # A refusal is a defect, not weather: it propagates and run() fails closed.
    graph.script = [(400, {}, b'{"error":"invalid_client"}')]
    with pytest.raises(urllib.error.HTTPError):
        probe._urllib_mint("tid", "cid", SECRET, base=_base(graph), timeout=0.4)
    graph.script = [(200, {}, b'{"access_token": "tok"}')]
    assert probe._urllib_mint("tid", "cid", SECRET, base=_base(graph), timeout=0.4) == "tok"
