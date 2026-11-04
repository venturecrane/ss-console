"""Statute and regulation sources for ``authority_fetch.py``.

California codes. leginfo.legislature.ca.gov answers 403 to a script, so the
tool does not use it. The Legislature publishes the same law as bulk data at
downloads.leginfo.legislature.ca.gov: each session's ``pubinfo_<year>.zip``
carries ``LAW_SECTION_TBL.dat`` (one row per code section version, with an
active flag and the name of the file holding its text) and one ``.lob`` XML
file per section. The archive is 1.2 GB, so the tool never downloads it: it
reads the zip's central directory and then only the members it needs, by HTTP
range requests (the server advertises ``Accept-Ranges: bytes``). The index and
the 6 MB section table are cached; after the first run a section is one range
request.

Federal regulations. The eCFR website answers a script with a challenge page,
and its full-text API is closed to robots by ecfr.gov's robots.txt, so the tool
uses neither. It reads GPO's official annual edition through govinfo's link
service (``/link/cfr/<title>/<part>?sectionnum=<n>&year=mostrecent``), which
answers the section as XML, and records the edition date it prints.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import struct
import xml.etree.ElementTree as ET
import zlib
from typing import Any

from authority_common import Ctx, Fetched, html_to_text

BULK = "https://downloads.leginfo.legislature.ca.gov"
GOVINFO = "https://www.govinfo.gov"
TABLE = "LAW_SECTION_TBL.dat"
#: LAW_SECTION_TBL.dat columns, from the Legislature's pubinfo_Readme.
_COL = {"code": 1, "section": 2, "effective": 6, "history": 13, "lob": 14, "active": 15}
CODE_NAMES = {
    "BPC": "Business and Professions Code",
    "CCP": "Code of Civil Procedure",
    "CIV": "Civil Code",
    "EVID": "Evidence Code",
    "GOV": "Government Code",
    "HSC": "Health and Safety Code",
    "INS": "Insurance Code",
    "LAB": "Labor Code",
    "PEN": "Penal Code",
    "VEH": "Vehicle Code",
}
_CFR = re.compile(
    r"(?i)\b(\d{1,2})\s*C\.?\s?F\.?\s?R\.?\s*(?:§+|sec(?:tion)?\.?|part|pt\.)?\s*(\d{1,4})\.(\d{1,5}[a-z]?)"
)


# ---- a zip archive read by range requests ---------------------------------------------------------


class RangeZip:
    """The members of a remote zip, read without downloading the archive."""

    def __init__(self, url: str, ctx: Ctx) -> None:
        self.url, self.ctx = url, ctx
        tail = self._range("-131072", "the archive's end")
        at = tail.rfind(b"PK\x05\x06")
        if at < 0:
            raise LookupError("no zip directory at the archive's end")
        _s, _d, _cd, _nd, n, cd_size, cd_off, _c = struct.unpack("<4s4H2LH", tail[at : at + 22])
        if n == 0xFFFF or cd_off == 0xFFFFFFFF or cd_size == 0xFFFFFFFF:
            loc = tail.rfind(b"PK\x06\x07", 0, at)
            if loc < 0:
                raise LookupError("a zip64 archive with no zip64 locator")
            (z64,) = struct.unpack("<Q", tail[loc + 8 : loc + 16])
            rec = self._range(f"{z64}-{z64 + 55}", "the zip64 directory record")
            if rec[:4] != b"PK\x06\x06":
                raise LookupError("a zip64 locator that points nowhere")
            n, _n2, cd_size, cd_off = struct.unpack("<4Q", rec[24:56])
        self.key = f"{cd_off}-{cd_size}"
        self.cd_off, self.cd_size, self.count = cd_off, cd_size, n
        self._index: dict[str, list[int]] | None = None

    def _range(self, spec: str, what: str) -> bytes:
        return self.ctx.get_ok(self.url, what, {"Range": f"bytes={spec}"}, ok=(206,))

    def index(self, keep: str = "") -> dict[str, list[int]]:
        """name -> [local header offset, compressed size, method, crc32] for the
        members whose names start with ``keep``, cached by directory identity."""
        if self._index is not None:
            return self._index
        name = f"leginfo/{self.key}-index.json"
        p = self.ctx.cache / name
        if p.is_file():
            loaded: dict[str, list[int]] = json.loads(p.read_text(encoding="utf-8"))
            self._index = loaded
            return loaded
        cd = self._range(f"{self.cd_off}-{self.cd_off + self.cd_size - 1}", "the zip directory")
        out: dict[str, list[int]] = {}
        i = 0
        while i + 46 <= len(cd) and cd[i : i + 4] == b"PK\x01\x02":
            (_s, _vm, _vn, _f, method, _t, _d, crc, csize, usize, nlen, xlen, clen, _dk, _ia, _ea, lho) = struct.unpack(
                "<4s6H3L5H2L", cd[i : i + 46]
            )
            fname = cd[i + 46 : i + 46 + nlen].decode("utf-8", errors="replace")
            extra = cd[i + 46 + nlen : i + 46 + nlen + xlen]
            if 0xFFFFFFFF in (csize, usize, lho):
                csize, lho = _zip64_sizes(extra, usize, csize, lho)
            if fname.startswith(keep):
                out[fname] = [lho, csize, method, crc]
            i += 46 + nlen + xlen + clen
        if not out:
            raise LookupError(f"no {keep or 'member'} in the archive")
        self.ctx.cached(name).write_text(json.dumps(out), encoding="utf-8")
        self._index = out
        return out

    def read(self, name: str) -> bytes:
        ent = self.index().get(name)
        if ent is None:
            raise LookupError(f"{name} is not in the archive")
        lho, csize, method, crc = ent
        head = self._range(f"{lho}-{lho + 29}", f"{name}'s header")
        nlen, xlen = struct.unpack("<2H", head[26:30])
        start = lho + 30 + nlen + xlen
        raw = self._range(f"{start}-{start + csize - 1}", name) if csize else b""
        data = zlib.decompressobj(-15).decompress(raw) if method == 8 else raw
        if method not in (0, 8) or zlib.crc32(data) != crc:
            raise LookupError(f"{name} did not read back whole (method {method})")
        return data


def _zip64_sizes(extra: bytes, usize: int, csize: int, lho: int) -> tuple[int, int]:
    j = 0
    while j + 4 <= len(extra):
        tag, size = struct.unpack("<2H", extra[j : j + 4])
        if tag == 1:
            vals, k = extra[j + 4 : j + 4 + size], 0
            if usize == 0xFFFFFFFF:
                k += 8
            if csize == 0xFFFFFFFF:
                (csize,) = struct.unpack("<Q", vals[k : k + 8])
                k += 8
            if lho == 0xFFFFFFFF:
                (lho,) = struct.unpack("<Q", vals[k : k + 8])
            break
        j += 4 + size
    return csize, lho


# ---- California codes ---------------------------------------------------------------------------


def session_zips(today: dt.date | None = None) -> list[str]:
    """The current two-year session's archive, then the one before it."""
    y = (today or dt.date.today()).year
    first = y if y % 2 else y - 1
    return [f"{BULK}/pubinfo_{first}.zip", f"{BULK}/pubinfo_{first - 2}.zip"]


def _rows(table: bytes, code: str, section: str) -> list[list[str]]:
    want_code, want_sec = f"`{code}`", f"`{section}.`"
    out = []
    for line in table.decode("utf-8", errors="replace").splitlines():
        cols = line.split("\t")
        if len(cols) > _COL["active"] and cols[_COL["code"]] == want_code and cols[_COL["section"]] == want_sec:
            out.append([c.strip("`") if c != "NULL" else "" for c in cols])
    return out


def lob_text(xml: bytes) -> str:
    s = xml.decode("utf-8", errors="replace")
    s = re.sub(r'(?i)<span class="EnSpace"\s*/>', " ", s)
    return html_to_text(s)


def fetch_statute(code: str, section: str, ctx: Ctx, zips: list[str] | None = None) -> Fetched:
    reasons = []
    for url in zips or session_zips():
        try:
            z = RangeZip(url, ctx)
            z.index("LAW_SECTION_TBL")
            cache = ctx.cache / f"leginfo/{z.key}-{TABLE}"
            if cache.is_file():
                table = cache.read_bytes()
            else:
                table = z.read(TABLE)
                ctx.cached(f"leginfo/{z.key}-{TABLE}").write_bytes(table)
            rows = [r for r in _rows(table, code, section) if r[_COL["active"]] == "Y"]
            if not rows:
                raise LookupError(f"no active {code} section {section} in the Legislature's table")
            rows.sort(key=lambda r: r[_COL["effective"]])
            parts = []
            for r in rows:
                body = lob_text(z.read(r[_COL["lob"]]))
                hist = f"\n\n({r[_COL['history']]})" if r[_COL["history"]] else ""
                parts.append(f"{section}. {body}{hist}")
            name = CODE_NAMES.get(code, code)
            text = f"{name} section {section}\n\n" + "\n\n[another operative version]\n\n".join(parts)
            return Fetched(
                f"{url} ({TABLE}, {code} {section})",
                text,
                {"edition": f"California Legislature bulk law data, {url.rsplit('/', 1)[-1]}"},
            )
        except LookupError as e:
            reasons.append(f"{url.rsplit('/', 1)[-1]}: {e}")
    raise LookupError("; ".join(reasons))


# ---- the Code of Federal Regulations -----------------------------------------------------------


def parse_cfr(citation: str) -> tuple[str, str, str] | None:
    """(title, part, section number within the part) or None."""
    m = _CFR.search(citation)
    return (m.group(1), m.group(2), m.group(3)) if m else None


def _block_text(el: Any) -> str:
    return " ".join("".join(el.itertext()).split())


def cfr_xml_text(raw: bytes) -> tuple[str, str]:
    """(section text, edition date) from a govinfo CFR section granule."""
    root = ET.fromstring(raw)
    sect = root.find(".//SECTION")
    if sect is None:
        raise LookupError("govinfo's answer carries no SECTION")
    date = (root.findtext(".//FDSYS/DATE") or "").strip()
    lines = []
    for el in sect:
        tag = el.tag
        if tag in ("SECTNO", "SUBJECT"):
            continue
        if tag == "GPOTABLE":
            heads = [_block_text(h) for h in el.iter("CHED")]
            if heads:
                lines.append("[table] " + " | ".join(heads))
            for row in el.iter("ROW"):
                lines.append(" | ".join(_block_text(e) for e in row.iter("ENT")))
            for note in el.iter("TNOTE"):
                lines.append(_block_text(note))
        else:
            t = _block_text(el)
            if t:
                lines.append(t)
    head = f"{_block_text(sect.find('SECTNO')) if sect.find('SECTNO') is not None else ''} {sect.findtext('SUBJECT') or ''}"
    text = "\n\n".join([" ".join(head.split())] + lines).strip()
    if len(text) < 40:
        raise LookupError("govinfo's section carries no text")
    return text, date


def fetch_cfr(citation: str, ctx: Ctx) -> Fetched:
    p = parse_cfr(citation)
    if p is None:
        raise LookupError("no C.F.R. title, part and section in the citation")
    title, part, sec = p
    url = f"{GOVINFO}/link/cfr/{title}/{part}?sectionnum={sec}&year=mostrecent&link-type=xml"
    raw = ctx.get_ok(url, "govinfo")
    if not raw.lstrip().startswith(b"<?xml") and b"<CFRGRANULE" not in raw[:2000]:
        raise LookupError("govinfo did not answer with the section's XML")
    text, date = cfr_xml_text(raw)
    return Fetched(
        url,
        text,
        {
            "edition": f"Code of Federal Regulations, annual edition revised as of {date or 'an unstated date'} (GPO, govinfo)"
        },
    )
