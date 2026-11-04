"""What every authority source shares: the one network call, the cache, and
the text converters. Loaded by path from ``authority_fetch.py`` (the tool runs
as a script and cannot rely on ``sys.path``).

The network is ``http_get``; every source takes a ``Ctx`` whose ``get`` the
tests replace, so nothing in the tests leaves the machine. A source never sends
anything but a citation string, or a part of one (a party name, a volume, a
section number), to the hosts named in ``HOSTS``.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, NamedTuple

#: The tool names itself truthfully. It never presents as a browser.
USER_AGENT = "smd-authority-fetch/2 (+https://smd.services)"
#: The only hosts the tool reads from, and the only hosts a redirect may lead to.
HOSTS = frozenset(
    {
        "static.case.law",  # Caselaw Access Project, Harvard Law School Library: bulk opinions, no account
        "courts.ca.gov",  # Judicial Council: CACI, slip opinions
        "www.courts.ca.gov",
        "www4.courts.ca.gov",
        "www.govinfo.gov",  # GPO: the annual Code of Federal Regulations
        "downloads.leginfo.legislature.ca.gov",  # the Legislature's bulk law data
        "www.courtlistener.com",  # only with COURTLISTENER_TOKEN set
    }
)

Get = Callable[[str, "bytes | None", "dict[str, str]"], "tuple[int, bytes]"]
PdfPages = Callable[[bytes], "list[str]"]


class Fetched(NamedTuple):
    """What a source returns: where the text came from, the text, and any
    extra frontmatter (an edition, the printed pages, the pagination used)."""

    source: str
    text: str
    meta: dict[str, str]


class _SameHosts(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        u = urllib.parse.urlparse(newurl)
        if u.scheme != "https" or u.hostname not in HOSTS:
            raise urllib.error.HTTPError(newurl, code, f"redirect to {u.scheme}://{u.hostname} refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SameHosts)


def http_get(url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    """The one network call. POST when ``data`` is given. Only https to
    ``HOSTS``, and a redirect is followed only to ``HOSTS``. A 403 or a
    challenge page is reported as it came; the tool never retries around one."""
    u = urllib.parse.urlparse(url)
    if u.scheme != "https" or u.hostname not in HOSTS:
        raise LookupError(f"refusing to fetch from {u.scheme}://{u.hostname}")
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})  # noqa: S310 -- scheme and host checked against HOSTS above
    try:
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected — scheme and host checked against HOSTS above, redirects by _SameHosts.
        with _OPENER.open(req, timeout=120) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        try:
            body = e.read() or b""
        except Exception:  # noqa: BLE001 -- a refused redirect carries no readable body
            body = b""
        return e.code, body


def pdf_pages(data: bytes) -> list[str]:
    """A PDF's text, one string per physical page: poppler's ``pdftotext``
    when it is installed, else ``pypdf`` when it is importable."""
    exe = shutil.which("pdftotext")
    if exe:
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "in.pdf"
            src.write_bytes(data)
            out = Path(d) / "out.txt"
            subprocess.run([exe, "-enc", "UTF-8", str(src), str(out)], check=True, capture_output=True)  # noqa: S603 -- a fixed local executable and paths we created
            pages = out.read_text(encoding="utf-8", errors="replace").split("\f")
            return pages[:-1] if pages and not pages[-1].strip() else pages
    try:
        import pypdf  # type: ignore[import-not-found]
    except ImportError as e:
        raise LookupError("reading a PDF needs poppler's pdftotext (brew install poppler) or pypdf") from e
    import io

    return [p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(data)).pages]


def default_cache() -> Path:
    env = os.environ.get("AUTHORITY_FETCH_CACHE", "").strip()
    return Path(env) if env else Path.home() / ".cache" / "smd-authority-fetch"


@dataclass
class Ctx:
    """What a source needs: the network, a cache directory, a PDF reader, and
    the environment (for an optional CourtListener token)."""

    get: Get = http_get
    cache: Path = field(default_factory=default_cache)
    pdf: PdfPages = pdf_pages
    env: dict[str, str] = field(default_factory=lambda: dict(os.environ))

    def cached(self, name: str) -> Path:
        p = self.cache / name
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def get_ok(self, url: str, what: str, headers: dict[str, str] | None = None, ok: tuple[int, ...] = (200,)) -> bytes:
        status, body = self.get(url, None, headers or {})
        if status not in ok:
            raise LookupError(f"{what} answered {status}")
        return body

    def get_json(self, url: str, what: str, cache_as: str | None = None) -> Any:
        """JSON from ``url``; with ``cache_as``, read from and kept in the cache
        (for static files that never change, such as a bulk volume's index)."""
        if cache_as:
            p = self.cache / cache_as
            if p.is_file():
                return json.loads(p.read_text(encoding="utf-8"))
        body = self.get_ok(url, what)
        value = json.loads(body.decode("utf-8", errors="replace"))
        if cache_as:
            self.cached(cache_as).write_bytes(body)
        return value


def html_to_text(raw: str) -> str:
    s = re.sub(r"(?is)<(script|style|nav|header|footer)\b.*?</\1>", " ", raw)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h\d>|</li>|</tr>|</section>|</article>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    lines = [" ".join(ln.split()) for ln in s.splitlines()]
    out: list[str] = []
    blank = False
    for ln in lines:
        if ln:
            out.append(ln)
            blank = False
        elif not blank and out:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def norm(s: str) -> str:
    """A reporter or citation compared without spacing, dots or case:
    ``Cal.App.5th`` and ``Cal. App. 5th`` are the same reporter."""
    return re.sub(r"[\s.]+", "", s or "").casefold()
