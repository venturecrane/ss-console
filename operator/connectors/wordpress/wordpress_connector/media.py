"""Load the bytes ``upload_media`` sends, from a seat file or an https URL.

An upload puts bytes on the firm's public site, so this is the one place the
connector could carry something out of the seat. Three rules hold that shut:

1. IMAGES ONLY, decided from the bytes. The file's magic number must be JPEG,
   PNG, GIF or WebP; the name and any declared content type are not trusted.
   A seat's tokens, config and mail are text, and none of them begins with an
   image signature.
2. A LOCAL PATH must resolve (symlinks followed) inside an allowed root:
   ``WORDPRESS_UPLOAD_ROOTS`` (``os.pathsep``-separated) when set, else the
   seat's attachment spool and the system temp directory.
3. A URL must be https, must not resolve to a private, loopback, link-local or
   otherwise non-public address, and is fetched without following redirects.

Both paths cap the size at ``MAX_UPLOAD_BYTES``.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
DEFAULT_ROOTS = ("/opt/data/attachment-spool",)

_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
)


class MediaRefused(ValueError):
    """The requested upload is not allowed. The message says why."""


def sniff_image(blob: bytes) -> tuple[str, str]:
    """Return (mime, extension) for a supported image, else raise."""
    for sig, mime, ext in _SIGNATURES:
        if blob.startswith(sig):
            return mime, ext
    if len(blob) >= 12 and blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "image/webp", ".webp"
    raise MediaRefused("only JPEG, PNG, GIF or WebP images can be uploaded (checked from the file's bytes)")


def allowed_roots() -> list[Path]:
    raw = os.environ.get("WORDPRESS_UPLOAD_ROOTS", "").strip()
    roots = [r for r in raw.split(os.pathsep) if r.strip()] if raw else [*DEFAULT_ROOTS, tempfile.gettempdir()]
    return [Path(r).resolve() for r in roots]


def read_local(file_path: str) -> bytes:
    p = Path(file_path).expanduser()
    try:
        real = p.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise MediaRefused(f"file not found: {file_path}") from exc
    roots = allowed_roots()
    if not any(real == r or r in real.parents for r in roots):
        raise MediaRefused(
            f"{file_path} is outside the folders uploads may come from ({', '.join(str(r) for r in roots)})"
        )
    if not real.is_file():
        raise MediaRefused(f"{file_path} is not a regular file")
    size = real.stat().st_size
    if size > MAX_UPLOAD_BYTES:
        raise MediaRefused(f"{file_path} is {size} bytes; the limit is {MAX_UPLOAD_BYTES}")
    return real.read_bytes()


def _public_host(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise MediaRefused(f"cannot resolve {host}") from exc
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if not addr.is_global:
            raise MediaRefused(f"{host} resolves to a non-public address; refusing to fetch it")


def fetch_url(url: str, *, http: httpx.Client | None = None) -> bytes:
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or not parts.hostname:
        raise MediaRefused("image URLs must be absolute https URLs")
    _public_host(parts.hostname)
    client = http or httpx.Client(timeout=30.0, follow_redirects=False)
    with client.stream("GET", url) as resp:
        if resp.status_code != 200:
            raise MediaRefused(f"fetching the image returned HTTP {resp.status_code} (redirects are not followed)")
        buf = bytearray()
        for chunk in resp.iter_bytes():
            buf.extend(chunk)
            if len(buf) > MAX_UPLOAD_BYTES:
                raise MediaRefused(f"the image is over the {MAX_UPLOAD_BYTES}-byte limit")
    return bytes(buf)
