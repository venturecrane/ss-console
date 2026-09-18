"""Where an emailed attachment's bytes come from: a URL, or the seat's spool.

THE DEFECT THIS CLOSES (proven live 2026-09-18). ``read_attachment_text``,
``stage_vendor_invoice`` and ``file_attachment_to_matter`` were all written
against ONE way of getting an attachment: a time-limited vendor download URL,
host-allowlisted. The mail vendor a seat actually runs on mints no such URL. Its
attachment endpoint returns raw bytes to an authenticated caller, so the
URL-shaped argument could never be filled and the whole path was unreachable.
A seat answered a vendor invoice with "Your message arrived without any
attachments" and the reply was, from inside the turn, correct.

So an attachment reference now has two forms, in ONE argument:

``https://…``      a vendor URL that IS time-limited and host-allowlisted. The
                   original contract, unchanged, allowlist untouched. Some
                   vendors mint one; nothing is taken away from them.
``spool:<token>``  a 32-hex token naming a file the mail side already wrote on
                   THIS machine. The bytes never travelled through the agent's
                   context and no credential crossed a connector.

One argument, not two, on purpose. With a separate ``spool_token`` parameter a
caller could name a URL and a token that disagree about WHICH document is being
filed, and the tool would have to pick one. Here that state cannot be expressed.

THIS MODULE IS THE READER HALF OF A MIRRORED PAIR. The writer is the overlay's
``shared/attachment_spool.py`` (hermes-smd-overlay), which owns the layout:
``<token>.bin`` beside ``<token>.json``, the token alphabet, the env var, the
size cap. The two run in different processes from different repos and share no
import path, so the layout is the contract and each side validates the token
INDEPENDENTLY — a check only the writer performs is a check this side's caller
can skip, and this side's caller is the agent loop on a tainted turn.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any

from .client import SmokeballWriteError

#: Mirrors the overlay's ``shared.attachment_spool`` constants. Same names, same
#: values, on purpose: a divergence here is a handoff that silently stops
#: finding entries.
SPOOL_DIR_ENV = "SMD_ATTACHMENT_SPOOL_DIR"
DEFAULT_SPOOL_DIR = "/opt/data/attachment-spool"
MAX_SPOOL_BYTES = 25 * 1024 * 1024
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
SPOOL_PREFIX = "spool:"
_BYTES_SUFFIX = ".bin"


def spool_dir() -> Path:
    return Path(os.environ.get(SPOOL_DIR_ENV, "").strip() or DEFAULT_SPOOL_DIR)


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def parse_source(source: Any) -> tuple[str, str]:
    """One attachment reference, split into ``(download_url, spool_token)``.

    Exactly one side is ever non-empty. An empty or unrecognized reference is
    refused here rather than reaching a fetch that would have to guess at it.
    """
    text = _clean(source)
    if text.lower().startswith(SPOOL_PREFIX):
        return "", text[len(SPOOL_PREFIX) :].strip()
    if text.lower().startswith("http"):
        return text, ""
    raise SmokeballWriteError(
        "no attachment source: pass 'spool:<token>' from the mail attachment tool, or a "
        f"https:// download URL if the vendor minted one (got {text[:40]!r})"
    )


def resolve_spool_path(token: Any) -> Path:
    """The path holding this token's bytes, or refuse.

    Four refusals, each closing a different way a caller-supplied string becomes
    a path it should not reach: a token that is not the exact issued shape
    (which alone forecloses ``..``, an absolute path and a NUL); an entry that
    is not inside the spool directory after resolution; a symlink; anything
    that is not a regular file.
    """
    base = spool_dir().resolve()
    name = _clean(token)
    if not TOKEN_RE.match(name):
        raise SmokeballWriteError("a spool reference must carry the 32-lowercase-hex token the mail tool returned")
    path = base / f"{name}{_BYTES_SUFFIX}"
    if path.is_symlink():
        raise SmokeballWriteError("refusing to read a spool entry that is a symlink")
    try:
        resolved = path.resolve(strict=True)
        mode = resolved.lstat().st_mode
    except OSError as exc:
        raise SmokeballWriteError(
            f"no spooled attachment for that token (it expires; spool it again): {exc.__class__.__name__}"
        ) from exc
    if resolved.parent != base:
        raise SmokeballWriteError("refusing a spool entry outside the spool directory")
    if not stat.S_ISREG(mode):
        raise SmokeballWriteError("refusing a spool entry that is not a regular file")
    return resolved


def read_spool(token: Any) -> bytes:
    """The spooled bytes for this token, size-capped."""
    path = resolve_spool_path(token)
    try:
        size = path.stat().st_size
        if size > MAX_SPOOL_BYTES:
            raise SmokeballWriteError(f"the spooled attachment is {size} bytes, over the {MAX_SPOOL_BYTES}-byte limit")
        return path.read_bytes()
    except OSError as exc:
        raise SmokeballWriteError(f"the spooled attachment could not be read: {exc}") from exc


def fetch_bytes(client: Any, source: Any) -> bytes:
    """The attachment's bytes, from whichever form the reference takes.

    The URL form goes through the client's existing allowlisted fetch (https
    only, allowed hosts only, no redirects, 25 MB cap) — unchanged. The spool
    form never leaves this machine.
    """
    url, token = parse_source(source)
    # Branch on the URL, not the token: a ``spool:`` reference with an empty or
    # malformed token must reach ``read_spool`` and be refused THERE, not fall
    # through to a URL fetch with an empty string.
    return client.fetch_attachment_url(url) if url else read_spool(token)


__all__ = [
    "DEFAULT_SPOOL_DIR",
    "MAX_SPOOL_BYTES",
    "SPOOL_DIR_ENV",
    "SPOOL_PREFIX",
    "TOKEN_RE",
    "fetch_bytes",
    "parse_source",
    "read_spool",
    "resolve_spool_path",
    "spool_dir",
]
