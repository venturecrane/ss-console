"""Address-safe HTTPS fetch for the sealed web reader (ADR 0099).

Every page the reader fetches goes through :func:`safe_get`: https only, at
most 3 redirects, every hop's host resolved and the address ACTUALLY CONNECTED
checked (private, loopback, link-local, CGNAT, Fly 6PN ``fdaa::/16``,
multicast, reserved, IPv4-mapped and tunnelled IPv6 and metadata addresses are
refused; ``*.internal``, ``.local`` and ``localhost`` names are refused before
resolving), text content types only, and 2 MB after decompression. TLS still
verifies the certificate against the URL's host name, so pinning the address
cannot impersonate a site. Split out of ``shared.sealed_reader`` to keep each
module under the file-size ceiling; the reader re-exports these names.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
import zlib
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

MAX_REDIRECTS = 3
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_PAGE_CHARS = 15_000

_CONTROL_RE = re.compile(
    "[\\x00-\\x08\\x0b-\\x1f\\x7f\\u200b-\\u200f\\u2028-\\u202e\\u2066-\\u2069\\ufeff]"
)

_FLY_6PN = ipaddress.ip_network("fdaa::/16")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_METADATA_IPS = frozenset(
    ipaddress.ip_address(a) for a in ("169.254.169.254", "fd00:ec2::254", "100.100.100.200")
)


class ReaderError(Exception):
    """A refusal or failure with a sentence for the model. Never carries page text."""


# ---------------------------------------------------------------------------
# Address safety
# ---------------------------------------------------------------------------


def ip_allowed(raw: str) -> bool:
    """True only for a public unicast address."""
    try:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None or ip.sixtofour is not None or ip.teredo is not None:
            return False
        if ip in _FLY_6PN:
            return False
    if ip in _METADATA_IPS:
        return False
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )


def host_allowed(host: str) -> bool:
    h = (host or "").strip().lower().rstrip(".")
    if not h or h == "localhost" or h.endswith(".localhost"):
        return False
    if h.endswith(".internal") or h == "internal" or h.endswith(".local"):
        return False
    try:
        ipaddress.ip_address(h.strip("[]"))
    except ValueError:
        return True
    # A literal IP in a URL is checked as an address.
    return ip_allowed(h.strip("[]"))


def _resolve(host: str, port: int) -> list[str]:
    """Seam: the host's addresses. Tests replace this."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """An HTTPS connection to one PRE-CHECKED address, re-checked after connect.

    TLS still verifies the certificate against the URL's host name (SNI), so a
    pinned address cannot impersonate a site. The peer address actually
    connected is checked again, which is what closes DNS rebinding."""

    def __init__(self, host: str, ip: str, *, timeout: float) -> None:
        self._tls = ssl.create_default_context()
        super().__init__(host, 443, timeout=timeout, context=self._tls)
        self._pinned_ip = ip

    def connect(self) -> None:  # noqa: D401
        sock = socket.create_connection((self._pinned_ip, 443), timeout=self.timeout)
        peer = sock.getpeername()[0]
        if not ip_allowed(str(peer)):
            sock.close()
            raise ReaderError("a page resolved to an address that is not public")
        self.sock = self._tls.wrap_socket(sock, server_hostname=self.host)


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise ReaderError("the research deadline passed")
    return left


def _read_capped(resp: Any, encoding: str, deadline: float) -> bytes:
    """Read and decompress a body, refusing anything over 2 MB DECOMPRESSED."""
    encoding = (encoding or "").strip().lower()
    if encoding not in ("", "identity", "gzip", "deflate"):
        raise ReaderError("a page used an unsupported content encoding")
    decomp: Any = None
    if encoding == "gzip":
        decomp = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif encoding == "deflate":
        decomp = zlib.decompressobj()
    out = bytearray()
    raw_total = 0
    while True:
        _remaining(deadline)
        chunk = resp.read(65536)
        if not chunk:
            break
        raw_total += len(chunk)
        if raw_total > MAX_BODY_BYTES:
            raise ReaderError("a page was larger than 2 MB")
        if decomp is None:
            out += chunk
        else:
            out += decomp.decompress(chunk, MAX_BODY_BYTES + 1 - len(out))
            if decomp.unconsumed_tail:
                raise ReaderError("a page was larger than 2 MB after decompression")
        if len(out) > MAX_BODY_BYTES:
            raise ReaderError("a page was larger than 2 MB after decompression")
    if decomp is not None:
        out += decomp.flush(MAX_BODY_BYTES + 1 - len(out))
        if len(out) > MAX_BODY_BYTES:
            raise ReaderError("a page was larger than 2 MB after decompression")
    return bytes(out)


def _open(url: str, ip: str, deadline: float) -> Response:
    """Seam: one GET of ``url`` over a connection pinned to ``ip``."""
    parts = urlsplit(url)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    conn = _PinnedHTTPSConnection(parts.hostname or "", ip, timeout=_remaining(deadline))
    try:
        conn.request(
            "GET",
            path,
            headers={
                "Accept": "text/html,text/plain,application/atom+xml;q=0.9",
                "Accept-Encoding": "gzip",
                "User-Agent": "SMD-Research/1.0",
            },
        )
        resp = conn.getresponse()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        if 300 <= resp.status < 400:
            return Response(resp.status, headers, b"")
        body = _read_capped(resp, headers.get("content-encoding", ""), deadline)
        return Response(resp.status, headers, body)
    finally:
        conn.close()


def safe_get(url: str, deadline: float, *, content_types: tuple[str, ...]) -> tuple[str, bytes]:
    """GET ``url`` under every address rule. Returns ``(final_url, body)``."""
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        parts = urlsplit(current)
        if parts.scheme != "https" or not parts.hostname:
            raise ReaderError("only https pages are read")
        if parts.username or parts.password or (parts.port not in (None, 443)):
            raise ReaderError("a page URL carried credentials or a non-standard port")
        if not host_allowed(parts.hostname):
            raise ReaderError("a page pointed at a host that is not public")
        addrs = _resolve(parts.hostname, 443)
        if not addrs or not all(ip_allowed(a) for a in addrs):
            raise ReaderError("a page resolved to an address that is not public")
        resp = _open(current, addrs[0], deadline)
        if 300 <= resp.status < 400:
            location = resp.headers.get("location", "")
            if not location:
                raise ReaderError("a page redirected nowhere")
            current = urljoin(current, location)
            continue
        if resp.status != 200:
            raise ReaderError(f"a page answered HTTP {resp.status}")
        ctype = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if ctype not in content_types:
            raise ReaderError("a page was not text")
        if len(resp.body) > MAX_BODY_BYTES:
            raise ReaderError("a page was larger than 2 MB after decompression")
        return current, resp.body
    raise ReaderError("a page redirected more than 3 times")


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------


class _TextExtractor(HTMLParser):
    _SKIP = frozenset({"script", "style", "noscript", "svg", "template", "head", "iframe"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._parts: list[str] = []
        self._size = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._SKIP:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._depth:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._depth or self._size > MAX_PAGE_CHARS * 2:
            return
        self._parts.append(data)
        self._size += len(data)

    def text(self) -> str:
        return " ".join(self._parts)


def extract_text(body: bytes, content_type: str = "text/html", limit: int = MAX_PAGE_CHARS) -> str:
    raw = body.decode("utf-8", errors="replace")
    if content_type == "text/html" or "<html" in raw[:2000].lower():
        parser = _TextExtractor()
        try:
            parser.feed(raw)
            parser.close()
        except Exception:  # noqa: BLE001 - malformed markup yields what parsed
            pass
        raw = parser.text()
    raw = _CONTROL_RE.sub(" ", raw)
    return re.sub(r"\s+", " ", raw).strip()[:limit]


def _json_request(
    host: str, method: str, path: str, headers: dict[str, str], body: bytes | None, deadline: float
) -> Any:
    """Seam: a JSON request to a FIXED public API host (Brave, Anthropic)."""
    # Certificate verification is explicit (a default context), not inherited.
    conn = http.client.HTTPSConnection(  # nosemgrep: python.lang.security.audit.httpsconnection-detected.httpsconnection-detected
        host, 443, timeout=_remaining(deadline), context=ssl.create_default_context()
    )
    try:
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        hdrs = {k.lower(): v for k, v in resp.getheaders()}
        data = _read_capped(resp, hdrs.get("content-encoding", ""), deadline)
        if resp.status != 200:
            raise ReaderError(f"{host} answered HTTP {resp.status}")
        return json.loads(data.decode("utf-8"))
    finally:
        conn.close()
