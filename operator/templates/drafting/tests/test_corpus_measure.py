#!/usr/bin/env python3
"""Tests for the free corpus measurement.

Runnable either way:

    python3 -m unittest discover operator/templates/drafting/tests
    python3 -m pytest operator/templates/drafting/tests

Only the pure accounting is exercised here -- classification, the tally, and
coverage. The seat-side half needs a connector and a real matter and is proven
by running it, not by a fixture that would only re-assert its own mock.

The bar: the tally must never let one outcome masquerade as another. A failed
download reading as an empty document, or a scanned page reading as a document
with no content, is how a corpus measurement under-reports the job and a quote
comes in low.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[1] / "corpus_measure.py"
_spec = importlib.util.spec_from_file_location("corpus_measure", _MODULE_PATH)
cm = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(cm)


class ClassifyTest(unittest.TestCase):
    def test_record_types(self):
        for e in ("pdf", "docx", "doc", "txt", "rtf"):
            self.assertEqual(cm.classify(e), "record", e)

    def test_mail_is_not_record(self):
        # Mail is 83 of 192 files on a real matter. Counting it as record
        # would inflate the denominator and understate coverage.
        self.assertEqual(cm.classify("msg"), "mail")
        self.assertEqual(cm.classify("eml"), "mail")

    def test_media_and_images_are_other(self):
        for e in ("jpg", "heic", "mp3", "wav", "iso", "png", "zip"):
            self.assertEqual(cm.classify(e), "other", e)

    def test_extension_is_normalised(self):
        self.assertEqual(cm.classify(".PDF"), "record")
        self.assertEqual(cm.classify("PDF"), "record")

    def test_missing_extension_is_other_not_record(self):
        self.assertEqual(cm.classify(""), "other")
        self.assertEqual(cm.classify(None), "other")


class AccountTest(unittest.TestCase):
    def setUp(self):
        self.t = cm.new_tally("m1")

    def test_a_read_document_adds_chars_and_counts_once(self):
        cm.account(self.t, "pdf", chars=1000, outcome="read")
        self.assertEqual(self.t["read_ok"], 1)
        self.assertEqual(self.t["chars"], 1000)
        self.assertEqual(self.t["per_ext_chars"]["pdf"], 1000)

    def test_zero_chars_is_dark_not_read(self):
        # The whole point: a record PDF with no text layer is scanned paper the
        # drafter cannot see, not a document that happens to be empty.
        cm.account(self.t, "pdf", chars=0, outcome="read")
        self.assertEqual(self.t["dark_scanned"], 1)
        self.assertEqual(self.t["read_ok"], 0)
        self.assertEqual(self.t["chars"], 0)

    def test_error_is_not_dark_and_not_read(self):
        # A failed download must never total as an unreadable document; that
        # would make a transport problem look like a scanning problem.
        cm.account(self.t, "pdf", outcome="error")
        self.assertEqual(self.t["errors"], 1)
        self.assertEqual(self.t["dark_scanned"], 0)
        self.assertEqual(self.t["read_ok"], 0)

    def test_unsupported_is_its_own_bucket(self):
        cm.account(self.t, "xyz", outcome="unsupported")
        self.assertEqual(self.t["unsupported"], 1)
        self.assertEqual(self.t["dark_scanned"], 0)

    def test_budget_exhaustion_is_counted_not_dropped(self):
        cm.account(self.t, "pdf", outcome="budget")
        self.assertEqual(self.t["not_attempted_budget"], 1)
        self.assertEqual(self.t["read_ok"], 0)
        self.assertEqual(self.t["dark_scanned"], 0)

    def test_chars_accumulate_per_extension(self):
        cm.account(self.t, "pdf", chars=10, outcome="read")
        cm.account(self.t, "pdf", chars=5, outcome="read")
        cm.account(self.t, "docx", chars=7, outcome="read")
        self.assertEqual(self.t["per_ext_chars"], {"pdf": 15, "docx": 7})
        self.assertEqual(self.t["chars"], 22)

    def test_outcomes_are_mutually_exclusive(self):
        for outcome in ("read", "dark", "unsupported", "error", "budget"):
            t = cm.new_tally("m")
            cm.account(t, "pdf", chars=1, outcome=outcome)
            counted = t["read_ok"] + t["dark_scanned"] + t["unsupported"] + t["errors"] + t["not_attempted_budget"]
            self.assertEqual(counted, 1, outcome)


class CoverageTest(unittest.TestCase):
    def test_none_when_there_are_no_record_documents(self):
        # None, never 1.0: a matter with no record documents has not been
        # fully read, it has nothing to read, and a projection must tell those
        # apart.
        self.assertIsNone(cm.coverage(cm.new_tally("m")))

    def test_half_read_reports_half(self):
        t = cm.new_tally("m")
        t["record_files"] = 107
        t["read_ok"] = 52
        self.assertAlmostEqual(cm.coverage(t), 0.4860, places=4)

    def test_dark_documents_lower_coverage(self):
        t = cm.new_tally("m")
        t["record_files"] = 10
        for _ in range(4):
            cm.account(t, "pdf", chars=100, outcome="read")
        for _ in range(6):
            cm.account(t, "pdf", chars=0, outcome="read")
        self.assertEqual(cm.coverage(t), 0.4)
        self.assertEqual(t["dark_scanned"], 6)


class SafetyTest(unittest.TestCase):
    def test_size_cap_is_declared_and_finite(self):
        self.assertGreater(cm.SIZE_CAP_BYTES, 0)

    def test_record_and_mail_sets_do_not_overlap(self):
        self.assertFalse(cm.RECORD_EXT & cm.MAIL_EXT)

    def test_module_does_not_reference_the_billable_vision_path(self):
        # This module's free-ness is structural: it calls extract_text, which
        # cannot reach vision. If someone swaps in extract_text_ex with
        # allow_vision, the measurement silently starts billing per page.
        src = _MODULE_PATH.read_text()
        code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
        body = code.split('"""', 2)[-1]
        self.assertNotIn("allow_vision=True", body)
        self.assertNotIn("extract_text_ex", body)


if __name__ == "__main__":
    unittest.main()
