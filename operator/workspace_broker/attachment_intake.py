"""The broker's own spool for outbound files too large for one broker frame.

WHY THIS EXISTS (ADR 0102). A broker request frame is bounded at 6 MiB
(``server.MAX_REQUEST_BYTES``), and a firm's record asked for by its own staff
can be 25 MiB. The bytes therefore cross into the broker in chunks, and land in
a directory the BROKER owns (beside its audit ledger, root's, never the agent
uid's), hashed by the broker as they arrive. A send then names the file by an
opaque handle and the sha256 the caller expects; the broker reads its own copy
and refuses unless the hash it computes is that one. Nothing the agent process
can write afterwards changes what is sent.

THE VERB. ``attachment_put``, gateway only::

    {"action": "attachment_put",
     "payload": {"handle": <absent on the first chunk>, "offset": int,
                 "chunk_b64": <standard base64, at most 4 MiB decoded>,
                 "final": bool, "sha256": <hex, required with final>}}

The first chunk opens a handle; each later chunk must start exactly where the
file ends (no gaps, no overwrites); ``final`` closes it, and the broker refuses
the close unless the whole file's sha256 is the one the caller named. Answers
``{"handle", "received"}`` mid-way and ``{"handle", "sha256", "size"}`` on
close. A closed handle is read once by a send (:func:`read_handle`) and expires
after :data:`TTL_SECONDS` either way.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import stat
import time
from pathlib import Path
from typing import Any

from .broker_context import BrokerContext

#: Decoded bytes one chunk may carry (4 MiB, under the 6 MiB frame with base64).
MAX_CHUNK_BYTES = 4 * 1024 * 1024
#: One file's bound: the per-message cap (25 MiB) is also the per-file cap.
MAX_FILE_BYTES = 25 * 1024 * 1024
#: Open and closed handles alike are gone after this.
TTL_SECONDS = 24 * 60 * 60  # a reply held for SMD review (matter_file_review) sends these handles
#: Handles open at once. A reply carries at most three files; a few requests
#: in flight is generous, and a bound stops a caller filling the disk.
MAX_HANDLES = 32
HANDLE_RE = re.compile(r"^[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class IntakeRefused(ValueError):
    """The chunk or the read is refused; the message says why."""


def spool_dir(audit_db_path: str | None) -> Path:
    """The broker-owned spool: beside the audit ledger, which only root writes."""
    if not audit_db_path:
        raise IntakeRefused("this broker has no state directory, so it cannot take files")
    return Path(audit_db_path).parent / "attachment-spool"


def _prepare(base: Path) -> None:
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions - owner-only on purpose
    os.chmod(base, 0o700)
    cutoff = time.time() - TTL_SECONDS
    for path in base.iterdir():
        try:
            if path.is_symlink() or path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def _path(base: Path, handle: Any, suffix: str) -> Path:
    if not isinstance(handle, str) or not HANDLE_RE.fullmatch(handle):
        raise IntakeRefused("handle must be 32 lowercase hex characters issued by this broker")
    return base / f"{handle}{suffix}"


def _regular(path: Path) -> int:
    """The size of ``path`` when it is a regular file, never following a link."""
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise IntakeRefused("no such file in the broker's spool (it may have expired)") from exc
    if not stat.S_ISREG(st.st_mode):
        raise IntakeRefused("refusing a spool entry that is not a regular file")
    return st.st_size


def _chunk(payload: dict[str, Any]) -> bytes:
    raw = payload.get("chunk_b64")
    if not isinstance(raw, str) or not raw or len(raw) > 4 * ((MAX_CHUNK_BYTES + 2) // 3):
        raise IntakeRefused(f"chunk_b64 must be base64 of 1 to {MAX_CHUNK_BYTES} bytes")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise IntakeRefused("chunk_b64 is not valid standard base64") from exc
    if not data or len(data) > MAX_CHUNK_BYTES:
        raise IntakeRefused(f"chunk_b64 must be base64 of 1 to {MAX_CHUNK_BYTES} bytes")
    return data


def put(base: Path, payload: Any) -> dict[str, Any]:
    """One chunk. See the module docstring for the protocol."""
    if not isinstance(payload, dict):
        raise IntakeRefused("payload must be an object")
    _prepare(base)
    data = _chunk(payload)
    offset = payload.get("offset")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise IntakeRefused("offset must be a non-negative integer")
    handle = payload.get("handle")
    if handle is None:
        if offset != 0:
            raise IntakeRefused("a new file starts at offset 0")
        if sum(1 for p in base.iterdir() if p.suffix in (".part", ".bin")) >= MAX_HANDLES:
            raise IntakeRefused("the broker's spool is full; try again shortly")
        handle = secrets.token_hex(16)
        part = _path(base, handle, ".part")
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    else:
        part = _path(base, handle, ".part")
        if _regular(part) != offset:
            raise IntakeRefused("offset is not where the file ends; chunks go in order, once each")
        fd = os.open(part, os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0))
    try:
        if offset + len(data) > MAX_FILE_BYTES:
            raise IntakeRefused(f"the file is over {MAX_FILE_BYTES // 1048576} MiB")
        os.write(fd, data)
    finally:
        os.close(fd)
    received = offset + len(data)
    if not payload.get("final"):
        return {"handle": handle, "received": received}
    return _close(base, handle, part, payload.get("sha256"))


def _close(base: Path, handle: str, part: Path, want: Any) -> dict[str, Any]:
    if not isinstance(want, str) or not _SHA256_RE.fullmatch(want):
        part.unlink(missing_ok=True)
        raise IntakeRefused("final needs the sha256 of the whole file")
    digest = hashlib.sha256()
    size = 0
    with open(part, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
            size += len(block)
    if digest.hexdigest() != want:
        part.unlink(missing_ok=True)
        raise IntakeRefused("the file the broker received does not match its sha256; send it again")
    os.replace(part, _path(base, handle, ".bin"))
    meta = {"sha256": want, "size": size, "closed_at": time.time()}
    fd = os.open(_path(base, handle, ".json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, json.dumps(meta).encode())
    finally:
        os.close(fd)
    return {"handle": handle, "sha256": want, "size": size}


def read_handle(base: Path, handle: Any, sha256: Any) -> bytes:
    """The bytes of a closed handle, hashed here and refused unless they are ``sha256``."""
    data = read_spooled(handle, base=base)
    if not isinstance(sha256, str) or hashlib.sha256(data).hexdigest() != sha256:
        raise IntakeRefused("the spooled file does not match the sha256 the send names")
    return data


def discard(base: Path, handle: Any) -> None:
    """Drop a handle once its send is decided. Never raises."""
    for suffix in (".bin", ".json", ".part"):
        try:
            _path(base, handle, suffix).unlink(missing_ok=True)
        except (IntakeRefused, OSError):
            continue


def default_spool_dir() -> Path:
    """The spool for callers with no broker context: ``SMD_BROKER_ATTACHMENT_SPOOL``,
    else beside ``SMD_AUDIT_DB_PATH`` (the same directory :func:`spool_dir` names)."""
    explicit = os.environ.get("SMD_BROKER_ATTACHMENT_SPOOL", "").strip()
    return Path(explicit) if explicit else spool_dir(os.environ.get("SMD_AUDIT_DB_PATH"))


def write_spooled(raw: bytes, name: str, *, base: Path | None = None) -> dict[str, Any]:
    """Spool ``raw`` whole, for a root-side caller (staged_send): ``{handle, sha256, size}``.

    ``name`` is the caller's label, kept in the entry's metadata only; it never
    becomes part of a path."""
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        raise IntakeRefused("nothing to spool")
    if len(raw) > MAX_FILE_BYTES:
        raise IntakeRefused(f"the file is over {MAX_FILE_BYTES // 1048576} MiB")
    base = base or default_spool_dir()
    _prepare(base)
    handle = secrets.token_hex(16)
    sha = hashlib.sha256(raw).hexdigest()
    fd = os.open(_path(base, handle, ".bin"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, bytes(raw))
    finally:
        os.close(fd)
    meta = {"sha256": sha, "size": len(raw), "name": str(name)[:200], "closed_at": time.time()}
    fd = os.open(_path(base, handle, ".json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, json.dumps(meta).encode())
    finally:
        os.close(fd)
    return {"handle": handle, "sha256": sha, "size": len(raw)}


def read_spooled(handle: str, *, base: Path | None = None) -> bytes:
    """The bytes of a closed handle. Raises :class:`IntakeRefused` on an unknown,
    malformed or expired handle. The caller re-hashes (:func:`read_handle` does)."""
    base = base or default_spool_dir()
    path = _path(base, handle, ".bin")
    size = _regular(path)
    if size > MAX_FILE_BYTES:
        raise IntakeRefused("spool entry is over the file bound")
    if os.lstat(path).st_mtime < time.time() - TTL_SECONDS:
        raise IntakeRefused("that file has expired from the broker's spool")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as fh:
        return fh.read(MAX_FILE_BYTES + 1)


def put_verb(
    broker: BrokerContext, _action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    """``attachment_put``: one chunk into the broker's own spool."""
    try:
        return {"ok": True, **put(spool_dir(broker.audit_db_path), request.get("payload"))}
    except IntakeRefused as exc:
        raise ValueError(f"attachment refused: {exc}") from exc


__all__ = [
    "HANDLE_RE",
    "MAX_CHUNK_BYTES",
    "MAX_FILE_BYTES",
    "IntakeRefused",
    "discard",
    "put",
    "default_spool_dir",
    "put_verb",
    "read_handle",
    "read_spooled",
    "spool_dir",
    "write_spooled",
]
