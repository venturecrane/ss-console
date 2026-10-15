"""Every interactive model call has a hard wall-clock deadline (deadline.py).

The defect: two runner processes each sat about an hour in one connection to
the model API with the client configured `timeout=600.0`. That timeout is
httpx's per-read timer, so any byte resets it; a server that sends headers and
then only pings (or keepalive whitespace) holds the call forever.

These tests point the REAL SDK client, with that same 600s timeout, at a local
server that stalls in each way, and require the doorway to give up within a
small deadline. Each test is guarded by its own join limit, so code without the
deadline FAILS here ("hung") instead of hanging CI.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

import anthropic
import pytest

from medchron import ledger as ledger_mod, llm

GUARD_S = 20.0  # a test that has not returned by now has hung
MSGS = [{"role": "user", "content": "synthetic prompt"}]


def _chunk(data: bytes) -> bytes:
    return hex(len(data))[2:].encode() + b"\r\n" + data + b"\r\n"


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return _chunk(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())


PING = _sse("ping", {"type": "ping"})


def _healthy_events() -> list[bytes]:
    msg = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "m",
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": 3, "output_tokens": 1},
    }
    return [
        _sse("message_start", {"type": "message_start", "message": msg}),
        _sse(
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        ),
        _sse(
            "content_block_delta",
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ok"}},
        ),
        _sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 2},
            },
        ),
        _sse("message_stop", {"type": "message_stop"}),
    ]


class Stalling:
    """A local HTTP server: read the request, send 200 headers, then `body`
    (a function of the connection writing whatever it likes)."""

    def __init__(self, ctype: str, body: Callable[[socket.socket, threading.Event], None]) -> None:
        self.ctype, self.body = ctype, body
        self.stop = threading.Event()
        self.connections = 0
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        threading.Thread(target=self._serve, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.sock.getsockname()[1]}"

    def _serve(self) -> None:
        while not self.stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            buf = b""
            while b"\r\n\r\n" not in buf:
                buf += conn.recv(65536)
            head = f"HTTP/1.1 200 OK\r\ncontent-type: {self.ctype}\r\ntransfer-encoding: chunked\r\n\r\n"
            conn.sendall(head.encode())
            self.body(conn, self.stop)
        except OSError:
            pass  # the client hung up, which is the point
        finally:
            conn.close()

    def close(self) -> None:
        self.stop.set()
        self.sock.close()


def silent(conn: socket.socket, stop: threading.Event) -> None:
    stop.wait(GUARD_S * 3)


def pings_forever(conn: socket.socket, stop: threading.Event) -> None:
    while not stop.wait(0.2):
        conn.sendall(PING)


def whitespace_forever(conn: socket.socket, stop: threading.Event) -> None:
    while not stop.wait(0.2):
        conn.sendall(_chunk(b" "))


def pings_then_answer(conn: socket.socket, stop: threading.Event) -> None:
    for _ in range(3):
        conn.sendall(PING)
        time.sleep(0.2)
    for ev in _healthy_events():
        conn.sendall(ev)
        conn.sendall(PING)
        time.sleep(0.2)
    conn.sendall(b"0\r\n\r\n")


@pytest.fixture
def serve() -> Iterator[Callable[..., Stalling]]:
    made: list[Stalling] = []

    def make(ctype: str, body: Callable[[socket.socket, threading.Event], None]) -> Stalling:
        made.append(Stalling(ctype, body))
        return made[-1]

    yield make
    for s in made:
        s.close()


def doorway(tmp_path: Path, url: str, logs: list[str]) -> llm.Doorway:
    # The production client shape (driver._sdk_client): 600s, no SDK retries.
    client = anthropic.Anthropic(api_key="test-key", base_url=url, timeout=600.0, max_retries=0)
    return llm.Doorway(ledger=ledger_mod.Ledger(tmp_path / "ledger.jsonl"), client=client, log=logs.append)


def guarded(fn: Callable[[], Any]) -> tuple[BaseException | None, Any, float]:
    """Run fn on a daemon thread; fail the test if it has not finished by
    GUARD_S. Returns (error, result, seconds)."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            box["error"] = exc

    t0 = time.monotonic()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(GUARD_S)
    if t.is_alive():
        pytest.fail(f"model call hung: still blocked after {GUARD_S:.0f}s; nothing bounded it")
    return box.get("error"), box.get("result"), time.monotonic() - t0


def _call(dw: llm.Doorway, stream: bool) -> Callable[[], Any]:
    return lambda: dw.call("compose", model="m", messages=MSGS, max_tokens=100, stream=stream, attempts=2, backoff=0.0)


@pytest.mark.parametrize(
    ("ctype", "body", "stream"),
    [
        ("text/event-stream", pings_forever, True),
        ("application/json", whitespace_forever, False),
        ("text/event-stream", silent, True),
        ("application/json", silent, False),
    ],
    ids=["stream-pings-forever", "create-keepalive-forever", "stream-silent", "create-silent"],
)
def test_a_stalled_call_is_aborted_at_its_deadline_retried_then_fails(
    tmp_path: Path, serve: Callable[..., Stalling], ctype: str, body: Any, stream: bool
) -> None:
    srv = serve(ctype, body)
    logs: list[str] = []
    dw = doorway(tmp_path, srv.url, logs)
    dw.deadline = lambda max_tokens: 1.0
    dw.stall_s = None
    err, _, took = guarded(_call(dw, stream))
    assert type(err).__name__ == "CallDeadlineError", repr(err)
    assert "compose" in str(err) and "deadline" in str(err) and "2 attempts" in str(err)
    assert took < 6.0, f"two 1s attempts took {took:.1f}s"
    assert srv.connections == 2, "the deadline is retried like a transport failure"
    assert len(logs) == 2 and all("deadline" in line for line in logs)
    assert not (tmp_path / "ledger.jsonl").exists(), "no reply, no ledger row"


def test_a_stream_of_only_pings_trips_the_stall_window_before_the_deadline(
    tmp_path: Path, serve: Callable[..., Stalling]
) -> None:
    srv = serve("text/event-stream", pings_forever)
    logs: list[str] = []
    dw = doorway(tmp_path, srv.url, logs)
    dw.deadline = lambda max_tokens: 60.0
    dw.stall_s = 1.0
    err, _, took = guarded(_call(dw, True))
    assert type(err).__name__ == "CallDeadlineError", repr(err)
    assert "stalled" in str(err)
    assert took < 6.0


def test_a_slow_but_live_stream_is_not_cut_by_the_stall_window(tmp_path: Path, serve: Callable[..., Stalling]) -> None:
    """Pings never count as progress, but real events do: a stream whose
    content arrives in gaps shorter than the window completes."""
    srv = serve("text/event-stream", pings_then_answer)
    dw = doorway(tmp_path, srv.url, [])
    dw.deadline = lambda max_tokens: 15.0
    dw.stall_s = 1.0
    err, res, _ = guarded(_call(dw, True))
    assert err is None, repr(err)
    assert res.text == "ok" and res.stop_reason == "end_turn"
    assert (tmp_path / "ledger.jsonl").is_file()


def test_the_deadline_scales_with_the_call_between_a_floor_and_a_ceiling() -> None:
    from medchron import deadline as d

    assert d.deadline_for(0) == d.DEADLINE_FLOOR_S
    assert d.deadline_for(2_000) == d.DEADLINE_FLOOR_S + 2_000 / d.DEADLINE_TOKENS_PER_S
    assert d.deadline_for(16_000) > d.deadline_for(2_000)
    assert d.deadline_for(128_000) == d.DEADLINE_CEILING_S
    # every call the runner makes is bounded, and well under the hour the defect sat
    assert d.DEADLINE_CEILING_S < 3600 and d.STALL_S <= 600
    dw = llm.Doorway(ledger=ledger_mod.Ledger(Path("unused")))
    assert dw.deadline is d.deadline_for and dw.stall_s == d.STALL_S
