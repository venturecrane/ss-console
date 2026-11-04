#!/usr/bin/env python3
"""Tests for the authority fetch tool. The network is stubbed; nothing here
leaves the machine.

The bars: only a citation string (or a part of one) is ever sent anywhere; a
lookup that does not resolve to exactly one authority writes nothing; an entry
is written in the store's format and never silently overwritten; nothing but
https to the named hosts is fetched, and no redirect leaves them; CourtListener
is asked only when a token is set; a statute is read from the bulk archive by
range requests, never by downloading it.
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "authority_fetch.py"
_spec = importlib.util.spec_from_file_location("authority_fetch", _PATH)
af = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
assert _spec and _spec.loader
_spec.loader.exec_module(af)
common = sys.modules["authority_common"]
cases = sys.modules["authority_cases"]
codes = sys.modules["authority_codes"]
caci = sys.modules["authority_caci"]

HOWELL = "Howell v. Hamilton Meats & Provisions, Inc. (2011) 52 Cal.4th 541"
LEO = "Leo v. Dunham (1953) 41 Cal.2d 712"
SHIVER = "Shiver v. Laramee (2018) 24 Cal.App.5th 395"
CL = "https://www.courtlistener.com/api/rest/v4/"
CAP = "https://static.case.law/"


class Net:
    """Routes by URL prefix; records every call. A route body that is a dict
    or list is served as JSON; bytes as they are."""

    def __init__(self, routes: dict[str, tuple[int, object]]) -> None:
        self.routes, self.calls = routes, []

    def __call__(self, url: str, data: bytes | None, headers: dict[str, str]) -> tuple[int, bytes]:
        self.calls.append((url, data, dict(headers)))
        for prefix, (status, body) in sorted(self.routes.items(), key=lambda kv: -len(kv[0])):
            if url.startswith(prefix):
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                return status, raw
        return 404, b""


class RangeNet(Net):
    """Serves one zip archive at ``url`` honoring Range headers, as the
    Legislature's download host does; a request with no Range is refused, so a
    test fails if the tool ever downloads the whole archive."""

    def __init__(self, url: str, archive: bytes) -> None:
        super().__init__({})
        self.url, self.archive = url, archive

    def __call__(self, url: str, data: bytes | None, headers: dict[str, str]) -> tuple[int, bytes]:
        self.calls.append((url, data, dict(headers)))
        if url != self.url:
            return 404, b""
        spec = headers.get("Range", "")
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", spec)
        if not m:
            return 200, b"WHOLE ARCHIVE"  # the tool must never take this
        a, b = m.groups()
        if not a:
            return 206, self.archive[-int(b) :]
        return 206, self.archive[int(a) : int(b) + 1 if b else None]


def ctx(net: Net, d: str, env: dict[str, str] | None = None, pdf=None):  # type: ignore[no-untyped-def]
    return common.Ctx(get=net, cache=Path(d) / "cache", pdf=pdf or (lambda b: []), env=env or {})


def cap_routes(volume_cases: list[dict], html: str, slug: str = "cal-2d", vol: str = "41") -> dict:
    return {
        CAP + "ReportersMetadata.json": (
            200,
            [{"slug": "cal-2d", "short_name": "Cal. 2d"}, {"slug": "cal-app-5th", "short_name": "Cal. App. 5th"}],
        ),
        f"{CAP}{slug}/{vol}/CasesMetadata.json": (200, volume_cases),
        f"{CAP}{slug}/{vol}/html/": (200, html.encode()),
    }


LEO_META = {
    "name_abbreviation": "Leo v. Dunham",
    "file_name": "0712-01",
    "citations": [{"type": "official", "cite": "41 Cal. 2d 712"}],
}
LEO_HTML = (
    "<section class='casebody'><p>EDMONDS, J.</p><p>A Ford tank truck struck Leo.</p>"
    '<a id="p713" data-label="713" class="page-label">*713</a><p>'
    + "The doctrine of imminent peril applies only where the peril was not of the person's own making. " * 4
    + "</p></section>"
)


class TestCitations(unittest.TestCase):
    def test_only_citation_strings_pass(self) -> None:
        self.assertEqual(af.kind(HOWELL), "case")
        self.assertEqual(af.kind("Civ. Code, § 3333.4"), "statute")
        self.assertEqual(af.kind("Cal. Code Civ. Proc. § 437c"), "statute")
        self.assertEqual(af.kind("CACI No. 3903A"), "instruction")
        self.assertEqual(af.kind("49 C.F.R. § 393.52"), "regulation")
        self.assertEqual(af.kind("49 C.F.R. § 390.3(f)(2)"), "regulation")
        self.assertEqual(af.check_citation("49  C.F.R. § 393.52"), "49 C.F.R. § 393.52")
        for bad in ("Plaintiff was rear-ended on the 101 and treated at the ER", "", "x" * 300, "Smith v. Jones"):
            with self.assertRaises(af.Refused):
                af.check_citation(bad)

    def test_statute_and_cfr_parts(self) -> None:
        self.assertEqual(
            af.statute_url("Civ. Code, § 3333.4"),
            "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=CIV&sectionNum=3333.4",
        )
        self.assertEqual(af.statute_parts("Cal. Code Civ. Proc. § 437c"), ("CCP", "437c"))
        self.assertEqual(af.statute_parts("Cal. Gov. Code § 815.2"), ("GOV", "815.2"))
        self.assertEqual(codes.parse_cfr("49 C.F.R. § 390.3(f)(2)"), ("49", "390", "3"))
        self.assertEqual(codes.parse_cfr("49 CFR 393.52"), ("49", "393", "52"))

    def test_case_parse(self) -> None:
        c = cases.parse(SHIVER)
        self.assertEqual((c["volume"], c["reporter"], c["page"], c["year"]), ("24", "Cal.App.5th", "395", 2018))
        self.assertEqual(c["parties"], ["Shiver", "Laramee"])
        d = cases.parse("Damele v. Mack Trucks, Inc. (1990) 219 Cal.App.3d 29")
        self.assertEqual((d["volume"], d["reporter"], d["page"]), ("219", "Cal.App.3d", "29"))


class TestCourtListener(unittest.TestCase):
    ROUTES = {
        CL + "citation-lookup/": (
            200,
            [{"clusters": [{"absolute_url": "/opinion/1/howell/", "sub_opinions": [CL + "opinions/9/"]}]}],
        ),
        CL + "opinions/9/": (200, {"plain_text": "We hold the measure is the amount paid. " * 10}),
    }

    def test_with_a_token_it_sends_only_the_citation(self) -> None:
        net = Net(self.ROUTES)
        with tempfile.TemporaryDirectory() as d:
            got = af.fetch(HOWELL, ctx=ctx(net, d, env={"COURTLISTENER_TOKEN": "t0k"}))
        self.assertEqual(got.source, "https://www.courtlistener.com/opinion/1/howell/")
        self.assertIn("amount paid", got.text)
        posted = [data for _u, data, _h in net.calls if data]
        self.assertEqual(posted, [("text=" + urllib.parse.quote_plus(HOWELL)).encode()])
        self.assertEqual(net.calls[0][2].get("Authorization"), "Token t0k")

    def test_without_a_token_courtlistener_is_never_asked(self) -> None:
        net = Net(self.ROUTES)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(LookupError) as e:
                af.fetch(HOWELL, ctx=ctx(net, d))
        self.assertIn("no COURTLISTENER_TOKEN", str(e.exception))
        self.assertFalse([u for u, _d, _h in net.calls if "courtlistener" in u])

    def test_ambiguous_lookup_writes_nothing(self) -> None:
        net = Net({CL + "citation-lookup/": (200, [{"clusters": [{}, {}]}])})
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "store"
            rc = af.main(["--out", str(out), HOWELL], ctx=ctx(net, d, env={"COURTLISTENER_TOKEN": "t"}))
            self.assertEqual(rc, 1)
            self.assertFalse(out.exists())


class TestCaselawAccess(unittest.TestCase):
    def test_direct_volume_keeps_page_labels(self) -> None:
        net = Net(cap_routes([LEO_META], LEO_HTML))
        with tempfile.TemporaryDirectory() as d:
            got = af.fetch(LEO, ctx=ctx(net, d))
        self.assertEqual(got.source, CAP + "cal-2d/41/html/0712-01.html")
        self.assertIn("[*713]", got.text)
        self.assertIn("imminent peril", got.text)
        self.assertEqual(got.meta["pagination"], "41 Cal.2d 712 pages as [*N]")

    def test_two_cases_on_a_page_resolve_by_name_or_write_nothing(self) -> None:
        other = {**LEO_META, "name_abbreviation": "People v. Smith", "file_name": "0712-02"}
        net = Net(cap_routes([other, LEO_META], LEO_HTML))
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("[*713]", af.fetch(LEO, ctx=ctx(net, d)).text)
        twin = {**LEO_META, "file_name": "0712-03"}
        net = Net(cap_routes([twin, LEO_META], LEO_HTML))
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "store"
            self.assertEqual(af.main(["--out", str(out), LEO], ctx=ctx(net, d)), 1)
            self.assertFalse(out.exists())

    def test_parallel_volume_tells_the_two_paginations_apart(self) -> None:
        meta = {
            "name_abbreviation": "Shiver v. Laramee",
            "file_name": "0256-01",
            "citations": [
                {"type": "official", "cite": "234 Cal. Rptr. 3d 256"},
                {"type": "parallel", "cite": "24 Cal. App. 5th 395"},
            ],
        }
        html = (
            '<p>The sudden emergency doctrine.</p><a class="page-label" data-label="397">*397</a>'
            '<p>More.</p><a class="page-label" data-label="260">*260</a><p>' + "Road rage. " * 30 + "</p>"
        )
        routes = {
            CAP + "ReportersMetadata.json": (200, [{"slug": "cal-app-5th", "short_name": "Cal. App. 5th"}]),
            CAP + "cal-rptr-3d/VolumesMetadata.json": (
                200,
                [
                    {"volume_number": "233", "start_year": 2018, "end_year": 2018},
                    {"volume_number": "234", "start_year": 2018, "end_year": 2018},
                    {"volume_number": "260", "start_year": 2020, "end_year": 2020},
                ],
            ),
            CAP + "cal-rptr-3d/233/CasesMetadata.json": (200, []),
            CAP + "cal-rptr-3d/234/CasesMetadata.json": (200, [meta]),
            CAP + "cal-rptr-3d/234/html/0256-01.html": (200, html.encode()),
        }
        net = Net(routes)
        with tempfile.TemporaryDirectory() as d:
            got = af.fetch(SHIVER, ctx=ctx(net, d))
            self.assertIn("[*397]", got.text)
            self.assertIn("[Cal. Rptr. 3d *260]", got.text)
            self.assertIn("parallel 234 Cal. Rptr. 3d 256", got.meta["pagination"])
            self.assertFalse([u for u, _d, _h in net.calls if "/260/" in u])  # 2020 volume never scanned
            # the volume indexes are static: a second fetch reads them from the cache
            before = len(net.calls)
            af.fetch(SHIVER, ctx=ctx(net, d))
            index_calls = [u for u, _d, _h in net.calls[before:] if u.endswith("CasesMetadata.json")]
            self.assertEqual(index_calls, [CAP + "cal-app-5th/24/CasesMetadata.json"])


SLIP_LIST = (
    "<div>A174766 July 6, 2026 1st District Court of Appeal • Published Opinion "
    "Smith v. Krause Industries 7/6/26 CA1/4 PDF</div>"
    "<div>B000001 July 2, 2025 2nd District Court of Appeal • Published Opinion "
    "Smith v. Krause Industries 7/2/25 CA2/1 PDF</div>"
)


class TestSlipOpinion(unittest.TestCase):
    def test_found_by_name_and_year_and_marked_as_slip_pagination(self) -> None:
        routes = {
            CAP + "ReportersMetadata.json": (200, []),
            "https://courts.ca.gov/opinions/publishedcitable-opinions": (200, SLIP_LIST.encode()),
            "https://courts.ca.gov/opinions/published-opinions-older-120-days": (200, b"<p>No results</p>"),
            "https://www.courts.ca.gov/opinions/documents/A174766.PDF": (200, b"%PDF-1.6 slip"),
        }
        net = Net(routes)
        pages = ["Filed 7/6/26\nCERTIFIED FOR PUBLICATION\n" + "The trial court erred. " * 20, "DISPOSITION"]
        with tempfile.TemporaryDirectory() as d:
            got = af.fetch("Smith v. Krause Industries (2026) 120 Cal.App.5th 1", ctx=ctx(net, d, pdf=lambda b: pages))
        self.assertEqual(got.source, "https://www.courts.ca.gov/opinions/documents/A174766.PDF")
        self.assertIn("[slip op. p. 2]\nDISPOSITION", got.text)
        self.assertIn("not Cal.App.5th pages", got.meta["pagination"])
        sent = [urllib.parse.urlparse(u).query for u, _d, _h in net.calls if "opinions/pub" in u]
        self.assertEqual(sent, ["title=Smith", "title=Smith"])  # the first party's name, nothing else


def caci_pages() -> list[str]:
    toc = "\n".join(f"{n}. Title {n}" for n in range(400, 410)) + "\n229\n"
    return [
        "Judicial Council of California\nCivil Jury Instructions",
        "Table of New and Revised CACI\nThis 2026 Edition of CACI includes all of the new instructions.",
        toc,
        "430. Causation: Substantial Factor\nA substantial factor in causing harm is a factor\nthat a reasonable"
        " person would consider.\nDirections for Use\nGive it.\n296\n",
        "NEGLIGENCE\n\nCACI No. 430\n\nSources and Authority\n• Mitchell v. Gonzales.\n297\n",
        "431. Causation: Multiple Causes\nA person's negligence may combine.\n298\n",
    ]


class TestCaci(unittest.TestCase):
    URL = "https://courts.ca.gov/system/files/file/judicial_council_of_california_civil_jury_instructions_2026.pdf"

    def test_instruction_is_cut_heading_to_next_heading_with_printed_pages(self) -> None:
        net = Net({self.URL: (200, b"%PDF-1.6 caci")})
        converted = []

        def pdf(b: bytes) -> list[str]:
            converted.append(b)
            return caci_pages()

        with tempfile.TemporaryDirectory() as d:
            c = ctx(net, d, pdf=pdf)
            got = af.fetch("CACI No. 430", ctx=c)
            self.assertTrue(got.text.startswith("[CACI p. 296]\n430. Causation: Substantial Factor"))
            self.assertIn("[CACI p. 297]\nSources and Authority", got.text)
            self.assertNotIn("CACI No. 430\n", got.text)  # the running header is dropped
            self.assertNotIn("Multiple Causes", got.text)
            self.assertEqual(
                got.meta, {"edition": "CACI 2026 Edition (Judicial Council of California)", "printed_pages": "296-297"}
            )
            self.assertEqual(got.source, self.URL + "#page=4")
            # the second instruction reads the cached pages: one download, one conversion
            self.assertIn("combine", af.fetch("CACI No. 431", ctx=c).text)
            self.assertEqual(len(converted), 1)
            self.assertEqual(len(net.calls), 1)

    def test_an_instruction_not_in_the_pdf_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            net = Net({self.URL: (200, b"%PDF-1.6")})
            out = Path(d) / "store"
            rc = af.main(["--out", str(out), "CACI No. 3927"], ctx=ctx(net, d, pdf=lambda b: caci_pages()))
            self.assertEqual(rc, 1)
            self.assertFalse(out.exists())


def law_archive() -> bytes:
    row = "\t".join(
        [
            "`VEH22350.1963252`",
            "`VEH`",
            "`22350.`",
            "`1963`",
            "`252`",
            "NULL",
            "NULL",
            "`id_x`",
            "`11.`",
            "NULL",
            "NULL",
            "`7.`",
            "`1.`",
            "`Amended by Stats. 1963, Ch. 252.`",
            "LAW_SECTION_TBL_7.lob",
            "`Y`",
            "`LEG_ESI`",
            "2026-09-26 01:01:10",
        ]
    )
    old = row.replace("`Y`", "`N`").replace("_7.lob", "_6.lob")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("BILL_TBL.dat", "x" * 5000)
        z.writestr("LAW_SECTION_TBL.dat", old + "\n" + row + "\n")
        z.writestr(
            "LAW_SECTION_TBL_7.lob",
            '<caml:Content xmlns:caml="x"><p>No person shall drive a vehicle upon a highway at a speed greater than'
            " is reasonable or prudent.</p></caml:Content>",
        )
        z.writestr("LAW_SECTION_TBL_6.lob", "<p>REPEALED TEXT</p>")
    return buf.getvalue()


class TestStatutes(unittest.TestCase):
    URL = "https://downloads.leginfo.legislature.ca.gov/pubinfo_2025.zip"

    def test_section_read_by_range_requests_only(self) -> None:
        net = RangeNet(self.URL, law_archive())
        with tempfile.TemporaryDirectory() as d:
            c = ctx(net, d)
            got = codes.fetch_statute("VEH", "22350", c, zips=[self.URL])
            self.assertIn("22350. No person shall drive", got.text)
            self.assertIn("(Amended by Stats. 1963, Ch. 252.)", got.text)
            self.assertNotIn("REPEALED", got.text)  # only the active version
            self.assertTrue(all("Range" in h for _u, _d, h in net.calls))
            # second section: the index and the table come from the cache
            before = len(net.calls)
            codes.fetch_statute("VEH", "22350", c, zips=[self.URL])
            ranges = [h["Range"] for _u, _d, h in net.calls[before:]]
            self.assertEqual(len(ranges), 3)  # the archive's end, the .lob header, the .lob body

    def test_missing_section_names_both_sessions(self) -> None:
        net = RangeNet(self.URL, law_archive())
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(LookupError) as e:
                codes.fetch_statute("VEH", "99999", ctx(net, d), zips=[self.URL, self.URL.replace("2025", "2023")])
        self.assertIn("no active VEH section 99999", str(e.exception))
        self.assertIn("pubinfo_2023.zip: ", str(e.exception))

    def test_session_years(self) -> None:
        import datetime as dt

        self.assertEqual(
            codes.session_zips(dt.date(2026, 11, 4)),
            [
                "https://downloads.leginfo.legislature.ca.gov/pubinfo_2025.zip",
                "https://downloads.leginfo.legislature.ca.gov/pubinfo_2023.zip",
            ],
        )


CFR_XML = b"""<?xml version="1.0"?>
<CFRGRANULE><FDSYS><CFRTITLE>49</CFRTITLE><DATE>2025-10-01</DATE></FDSYS>
<SECTION><SECTNO>\xc2\xa7 393.52</SECTNO><SUBJECT>Brake performance.</SUBJECT>
<P>(a) Upon application of its service brakes, a motor vehicle must be capable of stopping.</P>
<GPOTABLE><BOXHD><CHED>Type of motor vehicle</CHED><CHED>Stopping distance</CHED></BOXHD>
<ROW><ENT>Passenger cars</ENT><ENT>20</ENT></ROW></GPOTABLE>
<CITA>[36 FR 20298, Oct. 20, 1971]</CITA></SECTION></CFRGRANULE>"""


class TestCfr(unittest.TestCase):
    LINK = "https://www.govinfo.gov/link/cfr/49/393?sectionnum=52"

    def test_section_text_with_table_and_edition(self) -> None:
        net = Net({self.LINK: (200, CFR_XML)})
        with tempfile.TemporaryDirectory() as d:
            got = af.fetch("49 C.F.R. § 393.52", ctx=ctx(net, d))
        self.assertTrue(got.text.startswith("§ 393.52 Brake performance."))
        self.assertIn("Passenger cars | 20", got.text)
        self.assertIn("[36 FR 20298", got.text)
        self.assertIn("revised as of 2025-10-01", got.meta["edition"])

    def test_an_entity_declaration_is_refused_before_parsing(self) -> None:
        bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><CFRGRANULE>&a;</CFRGRANULE>'
        with self.assertRaises(LookupError):
            codes.cfr_xml_text(bomb)

    def test_a_challenge_page_is_reported_not_parsed(self) -> None:
        net = Net({self.LINK: (200, b"<!DOCTYPE html><title>Request Access</title>")})
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(LookupError):
                af.fetch("49 C.F.R. § 393.52", ctx=ctx(net, d))


class TestStore(unittest.TestCase):
    def test_main_writes_store_format_with_extras_and_never_overwrites(self) -> None:
        net = Net(cap_routes([LEO_META], LEO_HTML))
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "store"
            argv = ["--out", str(out), "--cache", str(Path(d) / "c"), LEO]
            self.assertEqual(af.main(argv, ctx=ctx(net, d)), 0)
            body = (out / f"{af.slug(LEO)}.md").read_text()
            self.assertTrue(body.startswith(f"---\ncitation: {LEO}\nsource: {CAP}cal-2d/41/html/0712-01.html\n"))
            self.assertIn("\npagination: 41 Cal.2d 712 pages as [*N]\n---\n", body)
            self.assertEqual(af.main(argv, ctx=ctx(net, d)), 1)  # already there
            self.assertEqual(af.main(["--force"] + argv, ctx=ctx(net, d)), 0)

    def test_wanted_file_and_hand_text(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            w = Path(d) / "authorities-wanted.json"
            w.write_text(
                json.dumps({"job": {}, "wanted": [{"citation": "Cal. Rules of Court, rule 3.1350", "slug": "x"}]})
            )
            out = Path(d) / "store"
            self.assertEqual(af.main(["--out", str(out), "--wanted", str(w)], ctx=ctx(Net({}), d)), 1)  # no auto source
            t = Path(d) / "rule.txt"
            t.write_text("Separate statement requirements.")
            rc = af.main(
                [
                    "--out",
                    str(out),
                    "--text",
                    str(t),
                    "--source",
                    "https://courts.ca.gov/rules",
                    "Cal. Rules of Court, rule 3.1350",
                ]
            )
            self.assertEqual(rc, 0)
            self.assertIn("Separate statement", (out / "cal-rules-of-court-rule-3-1350.md").read_text())


class TestNetwork(unittest.TestCase):
    def test_only_named_https_hosts(self) -> None:
        for url in (
            "file:///etc/passwd",
            "http://static.case.law/x",
            "https://evil.test/x",
            "https://leginfo.legislature.ca.gov/x",
        ):
            with self.assertRaises(LookupError):
                af.http_get(url)

    def test_a_redirect_off_the_named_hosts_is_refused(self) -> None:
        h = common._SameHosts()
        req = urllib.request.Request("https://static.case.law/x")
        with self.assertRaises(urllib.error.HTTPError):
            h.redirect_request(req, io.BytesIO(), 302, "Found", {}, "https://evil.test/y")
        self.assertIsNotNone(h.redirect_request(req, io.BytesIO(), 302, "Found", {}, "https://www.courts.ca.gov/y"))

    def test_the_tool_names_itself(self) -> None:
        self.assertTrue(common.USER_AGENT.startswith("smd-authority-fetch/"))
        self.assertNotIn("Mozilla", common.USER_AGENT)


if __name__ == "__main__":
    unittest.main()
