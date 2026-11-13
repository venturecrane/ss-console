"""The broker's Unix socket: one newline-framed JSON request, one reply. One client.

Every process that talks to the workspace broker (the gateway and its plugins,
the job runner, the Smokeball connector, the broker's own CLIs, each skill's
pre_run.py, the operator tools) used to open the socket, frame the request and
read the reply with its own copy of the same dozen lines. This module is the one
home for that; each process carries a byte-identical copy, stamped by
operator/bin/gen-skill-shared.py from operator/contracts/
skill-shared-modules.yaml. Edit this file (the source), then run the generator;
never edit a copy.

The frame is UTF-8. A request is ``json.dumps(payload) + "\\n"`` with
``ensure_ascii=False``: an accented letter or a curly quote is its two or three
UTF-8 bytes on the wire, not a six-byte ``\\uXXXX`` escape (the broker's frame
ceiling is the real per-document limit, ss#2247). The broker reads a line and
``json.loads`` the bytes, which decodes UTF-8, and no UTF-8 continuation byte is
0x0A, so the newline framing is unchanged and the decoded request is the same
value. A string holding a lone surrogate has no UTF-8 form; by default that
request goes out escaped, byte for byte as before, so every request the broker
decoded before decodes to the same value now (operator/bin/tests/
test_broker_socket_wire.py). A caller that has always sent UTF-8 and raised on
such a string passes ``lone_surrogates="raise"`` and still does.

What a reply means (ok or refused, a dict or not, which exception) is each
caller's: this module returns the raw reply (a bytearray) and raises only what
the socket raised. Stdlib only.
"""

from __future__ import annotations

import json
import socket
from typing import Any

#: How many bytes one ``recv`` asks for.
RECV_BYTES = 65_536


#: ``lone_surrogates`` for :func:`encode`: what a string with no UTF-8 form does.
#:   escape   the request goes out ASCII-escaped (what every ASCII client sent)
#:   raise    UnicodeEncodeError (what every client that always sent UTF-8 raised)
LONE_SURROGATES = ("escape", "raise")


def encode(
    payload: Any,
    *,
    sort_keys: bool = False,
    separators: tuple[str, str] | None = None,
    lone_surrogates: str = "escape",
) -> bytes:
    """The request frame: the payload as JSON in UTF-8, then a newline.

    ``sort_keys`` and ``separators`` are ``json.dumps``'s; ``lone_surrogates``
    names what a payload with no UTF-8 encoding does (:data:`LONE_SURROGATES`).
    """
    if lone_surrogates not in LONE_SURROGATES:
        raise ValueError(f"lone_surrogates must be one of {LONE_SURROGATES}: {lone_surrogates!r}")
    text = json.dumps(payload, sort_keys=sort_keys, separators=separators, ensure_ascii=False)
    try:
        return text.encode("utf-8") + b"\n"
    except UnicodeEncodeError:
        if lone_surrogates == "raise":
            raise
        escaped = json.dumps(payload, sort_keys=sort_keys, separators=separators)
        return escaped.encode("ascii") + b"\n"


def unix_stream(*, timeout: float | None) -> socket.socket:
    """A new AF_UNIX stream socket with ``timeout`` set, not yet connected."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    return sock


def connect(path: str, *, timeout: float | None) -> socket.socket:
    """An AF_UNIX stream socket connected to ``path``; closed again when the
    connect raises (FileNotFoundError, ConnectionRefusedError, ...), which
    propagates unchanged."""
    sock = unix_stream(timeout=timeout)
    try:
        sock.connect(path)
    except BaseException:
        sock.close()
        raise
    return sock


def read_reply(sock: socket.socket, *, recv_bytes: int = RECV_BYTES) -> bytearray:
    """What the broker sent, until a newline ends it or the broker closes."""
    buf = bytearray()
    while not buf.endswith(b"\n"):
        chunk = sock.recv(recv_bytes)
        if not chunk:
            break
        buf.extend(chunk)
    return buf


def send_frame(
    path: str,
    frame: bytes,
    *,
    timeout: float | None,
    recv_bytes: int = RECV_BYTES,
) -> bytearray:
    """Connect, send an already-encoded ``frame`` (:func:`encode`), and return the reply."""
    with connect(path, timeout=timeout) as sock:
        sock.sendall(frame)
        return read_reply(sock, recv_bytes=recv_bytes)


def request(
    path: str,
    payload: Any,
    *,
    timeout: float | None,
    sort_keys: bool = False,
    separators: tuple[str, str] | None = None,
    lone_surrogates: str = "escape",
    recv_bytes: int = RECV_BYTES,
) -> bytearray:
    """Connect, then encode and send ``payload`` (:func:`encode`), and return the reply."""
    with connect(path, timeout=timeout) as sock:
        frame = encode(
            payload,
            sort_keys=sort_keys,
            separators=separators,
            lone_surrogates=lone_surrogates,
        )
        sock.sendall(frame)
        return read_reply(sock, recv_bytes=recv_bytes)


__all__ = [
    "LONE_SURROGATES",
    "RECV_BYTES",
    "connect",
    "encode",
    "read_reply",
    "request",
    "send_frame",
    "unix_stream",
]
