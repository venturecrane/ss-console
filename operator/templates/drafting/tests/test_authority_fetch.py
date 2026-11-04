#!/usr/bin/env python3
"""Tests for the authority fetch tool. The network is stubbed; nothing here
leaves the machine.

The bars: only a citation string is ever sent anywhere; a lookup that does
not resolve to exactly one opinion writes nothing; an entry is written in the
store's format and never silently overwritten; nothing but https to the named
hosts is fetched.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "authority_fetch.py"
_spec = importlib.util.spec_from_file_location("authority_fetch", _PATH)
af = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
assert _spec and _spec.loader
_spec.loader.exec_module(af)

HOWELL = "Howell v. Hamilton Meats & Provisions, Inc. (2011) 52 Cal.4th 541"


class Net:
    def __init__(self, routes: dict[str, tuple[int, object]]) -> None:
        self.routes, self.calls = routes, []

    def __call__(self, url: str, data: bytes | None, headers: dict[str, str]) -> tuple[int, bytes]:
        self.calls.append((url, data))
        for prefix, (status, body) in self.routes.items():
            if url.startswith(prefix):
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                return status, raw
        return 404, b""


CL = "https://www.courtlistener.com/api/rest/v4/"


class TestCitations(unittest.TestCase):
    def test_only_citation_strings_pass(self) -> None:
        self.assertEqual(af.kind(HOWELL), "case")
        self.assertEqual(af.kind("Civ. Code, § 3333.4"), "statute")
        self.assertEqual(af.kind("CACI No. 3903A"), "instruction")
        for bad in ("Plaintiff was rear-ended on the 101 and treated at the ER", "", "x" * 300, "Smith v. Jones"):
            with self.assertRaises(af.Refused):
                af.check_citation(bad)

    def test_statute_url(self) -> None:
        self.assertEqual(
            af.statute_url("Civ. Code, § 3333.4"),
            "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=CIV&sectionNum=3333.4",
        )


class TestFetch(unittest.TestCase):
    def test_case_fetch_sends_only_the_citation(self) -> None:
        net = Net(
            {
                CL + "citation-lookup/": (
                    200,
                    [{"clusters": [{"absolute_url": "/opinion/1/howell/", "sub_opinions": [CL + "opinions/9/"]}]}],
                ),
                CL + "opinions/9/": (200, {"plain_text": "We hold the measure is the amount paid."}),
            }
        )
        src, text = af.fetch(HOWELL, net)
        self.assertEqual(src, "https://www.courtlistener.com/opinion/1/howell/")
        self.assertIn("amount paid", text)
        posted = [d for _u, d in net.calls if d]
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0].decode(), "text=" + __import__("urllib.parse").parse.quote_plus(HOWELL))

    def test_ambiguous_lookup_writes_nothing(self) -> None:
        net = Net({CL + "citation-lookup/": (200, [{"clusters": [{}, {}]}])})
        with tempfile.TemporaryDirectory() as d:
            rc = af.main(["--out", d, HOWELL], get=net)
            self.assertEqual(rc, 1)
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_main_writes_store_format_and_never_overwrites(self) -> None:
        net = Net(
            {
                CL + "citation-lookup/": (
                    200,
                    [{"clusters": [{"absolute_url": "/o/1/", "sub_opinions": [CL + "opinions/9/"]}]}],
                ),
                CL + "opinions/9/": (200, {"html_with_citations": "<p>Paid or incurred.</p>"}),
            }
        )
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(af.main(["--out", d, HOWELL], get=net), 0)
            p = Path(d) / f"{af.slug(HOWELL)}.md"
            body = p.read_text()
            self.assertTrue(body.startswith(f"---\ncitation: {HOWELL}\nsource: https://www.courtlistener.com/o/1/\n"))
            self.assertIn("fetched_at: ", body)
            self.assertIn("Paid or incurred.", body)
            self.assertEqual(af.main(["--out", d, HOWELL], get=net), 1)  # already there
            self.assertEqual(af.main(["--out", d, "--force", HOWELL], get=net), 0)

    def test_wanted_file_and_hand_text(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            w = Path(d) / "authorities-wanted.json"
            w.write_text(json.dumps({"job": {}, "wanted": [{"citation": "CACI No. 3903A", "slug": "x"}]}))
            out = Path(d) / "store"
            self.assertEqual(af.main(["--out", str(out), "--wanted", str(w)], get=Net({})), 1)  # no auto source
            t = Path(d) / "caci.txt"
            t.write_text("Medical expenses, past and future.")
            rc = af.main(
                ["--out", str(out), "--text", str(t), "--source", "https://courts.ca.gov/caci", "CACI No. 3903A"]
            )
            self.assertEqual(rc, 0)
            self.assertIn("Medical expenses", (out / "caci-no-3903a.md").read_text())

    def test_only_named_https_hosts(self) -> None:
        for url in ("file:///etc/passwd", "http://www.courtlistener.com/x", "https://evil.test/x"):
            with self.assertRaises(LookupError):
                af.http_get(url)


if __name__ == "__main__":
    unittest.main()
