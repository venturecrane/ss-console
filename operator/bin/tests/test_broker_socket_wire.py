"""The one broker client (shared/broker_socket.py) sends UTF-8, and the broker
decodes every request to the value it decoded before.

Wave 2, B1 moved every broker client onto one encoder. Before it, 24 clients
sent ``json.dumps(payload)`` with the default ASCII escapes and 3 sent UTF-8.
The broker reads one line and ``json.loads`` its bytes, so what reaches it is
the DECODED payload; this file holds that equal, old encoder against new, over
non-ASCII payloads, and runs the new client over a real Unix socket into a
server that decodes the way the broker does.

The old encoders are written out here as the clients wrote them (the three
shapes in operator/contracts/seat-config-callers.json's rows), so a change to
the module cannot move the reference it is checked against.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "hermes-smd-overlay" / "shared" / "broker_socket.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("broker_socket_under_test", SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bs = _load()

#: The three ways the ASCII clients framed a request, and the keywords that reproduce each in UTF-8.
OLD_ASCII: dict[str, tuple[Callable[[Any], bytes], dict[str, Any]]] = {
    "default": (lambda p: json.dumps(p).encode("utf-8") + b"\n", {}),
    "sorted-compact": (
        lambda p: json.dumps(p, sort_keys=True, separators=(",", ":")).encode() + b"\n",
        {"sort_keys": True, "separators": (",", ":")},
    ),
    "compact": (
        lambda p: json.dumps(p, separators=(",", ":")).encode("utf-8") + b"\n",
        {"separators": (",", ":")},
    ),
}


def _old_utf8(p: Any) -> bytes:
    """How the three clients that always sent UTF-8 framed a request."""
    return json.dumps(p, ensure_ascii=False).encode("utf-8") + b"\n"


NON_ASCII: list[Any] = [
    {"verb": "note", "text": "café naïve 日本 ±"},
    {
        "action": "audit_append",
        "row": {"metadata": "\u201ccurly\u201d \u2013 en dash \u2014 em dash, \u2018quotes\u2019"},
    },
    {
        "text": "emoji \N{SLIGHTLY SMILING FACE}, astral \N{MUSICAL SYMBOL G CLEF}, RTL \N{HEBREW LETTER SHIN}\N{HEBREW LETTER LAMED}, combining e\N{COMBINING ACUTE ACCENT}"
    },
    {
        "text": "line\N{LINE SEPARATOR}sep\N{PARAGRAPH SEPARATOR}para, nbsp\N{NO-BREAK SPACE}, "
        "bom\N{ZERO WIDTH NO-BREAK SPACE}"
    },
    {"ключ": ["значение", 1, 2.5, None, True, {"深い": {"キー": "値"}}]},
    {"mixed": ["ascii", "ünïcödé", "", " ", "\\backslash\\", '"quoted"']},
    ["a top-level list", "with ñ"],
    "a bare string ø",
]
ASCII_ONLY: list[Any] = [
    {"n": 1, "verb": "ping"},
    {"action": "health"},
    {"row": {"a": [1, 2, {"b": None}], "c": "x\ny"}},
    {"text": "controls \x00\x01\x1f and a newline\nand a tab\t inside a string"},
]
_HIGH, _LOW, _ESCAPED_BYTE = chr(0xD83D), chr(0xDE00), chr(0xDC80)
LONE_SURROGATE: list[Any] = [
    {"text": f"half a pair {_HIGH} alone"},
    {"text": f"a pair written as two code points {_HIGH}{_LOW}"},
    {f"key{_ESCAPED_BYTE}": "surrogateescape byte"},
]


@pytest.mark.parametrize("shape", sorted(OLD_ASCII))
@pytest.mark.parametrize("payload", NON_ASCII, ids=range(len(NON_ASCII)))
def test_a_non_ascii_request_decodes_to_the_value_it_decoded_to_before(shape: str, payload: Any) -> None:
    old_encode, kwargs = OLD_ASCII[shape]
    old, new = old_encode(payload), bs.encode(payload, **kwargs)
    assert new != old, "the payload carries non-ASCII text, so the UTF-8 frame must differ in bytes"
    assert json.loads(new) == json.loads(old) == json.loads(json.dumps(payload))
    # One frame per line: the only newline byte is the terminator (UTF-8 never encodes one inside text).
    assert new.endswith(b"\n") and new.count(b"\n") == 1
    new.decode("utf-8")  # well-formed UTF-8
    assert len(new) < len(old), "UTF-8 is never longer than its \\uXXXX escapes"


@pytest.mark.parametrize("shape", sorted(OLD_ASCII))
@pytest.mark.parametrize("payload", ASCII_ONLY, ids=range(len(ASCII_ONLY)))
def test_an_ascii_request_is_byte_for_byte_what_it_was(shape: str, payload: Any) -> None:
    old_encode, kwargs = OLD_ASCII[shape]
    assert bs.encode(payload, **kwargs) == old_encode(payload)


@pytest.mark.parametrize("payload", LONE_SURROGATE, ids=range(len(LONE_SURROGATE)))
def test_a_string_with_no_utf8_form_goes_out_exactly_as_before(payload: Any) -> None:
    for shape, (old_encode, kwargs) in OLD_ASCII.items():
        assert bs.encode(payload, **kwargs) == old_encode(payload), shape
    # A client that always sent UTF-8 raised on it, and with lone_surrogates="raise" still does.
    with pytest.raises(UnicodeEncodeError):
        _old_utf8(payload)
    with pytest.raises(UnicodeEncodeError):
        bs.encode(payload, lone_surrogates="raise")


@pytest.mark.parametrize("payload", NON_ASCII + ASCII_ONLY, ids=range(len(NON_ASCII + ASCII_ONLY)))
def test_the_clients_that_always_sent_utf8_send_the_same_bytes(payload: Any) -> None:
    assert bs.encode(payload, lone_surrogates="raise") == _old_utf8(payload)


def test_an_unknown_surrogate_policy_is_refused() -> None:
    with pytest.raises(ValueError, match="lone_surrogates"):
        bs.encode({"a": 1}, lone_surrogates="ignore")


# ------------------------------------------------------------ over a real socket


class _DecodingBroker:
    """A Unix-socket server that reads a request the way the broker does
    (readline, then json.loads of the bytes) and answers with what it decoded."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.decoded: list[Any] = []
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(path)
        self._sock.listen(4)
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> _DecodingBroker:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._sock.close()
        self._thread.join(timeout=2)

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            with conn, conn.makefile("rb") as rfile:
                raw = rfile.readline()
                value = json.loads(raw)
                self.decoded.append(value)
                conn.sendall(json.dumps({"ok": True, "echo": value}).encode("utf-8") + b"\n")


@pytest.fixture
def sock_path() -> Any:
    # AF_UNIX paths are short: a fresh dir under /tmp, not pytest's long tmp_path.
    with tempfile.TemporaryDirectory(prefix="bsw", dir="/tmp") as d:
        yield os.path.join(d, "b.sock")


@pytest.mark.parametrize("payload", NON_ASCII[:6], ids=range(6))
def test_the_client_round_trips_through_a_broker_that_decodes_bytes(sock_path: str, payload: Any) -> None:
    with _DecodingBroker(sock_path) as broker:
        reply = bs.request(sock_path, payload, timeout=5.0)
        framed = bs.send_frame(sock_path, bs.encode(payload, sort_keys=True, separators=(",", ":")), timeout=5.0)
    assert broker.decoded == [payload, payload]
    assert json.loads(reply)["echo"] == payload
    assert json.loads(framed)["echo"] == payload
    assert isinstance(reply, bytearray)


def test_connect_failures_are_the_socket_s_own(sock_path: str) -> None:
    with pytest.raises(FileNotFoundError):
        bs.request(sock_path, {"action": "health"}, timeout=1.0)
    bound = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    bound.bind(sock_path)  # a socket file nobody listens on
    try:
        with pytest.raises(ConnectionRefusedError):
            bs.connect(sock_path, timeout=1.0)
    finally:
        bound.close()
