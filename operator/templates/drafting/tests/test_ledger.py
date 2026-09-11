#!/usr/bin/env python3
"""Tests for the drafting lane's cost ledger.

Runnable either way:

    python3 -m unittest discover operator/templates/drafting/tests
    python3 -m pytest operator/templates/drafting/tests

The bar these hold: every dollar the lane reports is derived from recorded
tokens at the shipped rate card, an unknown model is never silently priced at
zero, and a stage that loses its environment still writes somewhere visible.
Each of those is a defect the medical-chronology pipeline actually shipped
before it was instrumented this way.
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

_DIR = Path(__file__).resolve().parents[1]
_MODULE_PATH = _DIR / "ledger.py"
_CARD_PATH = _DIR / "rate-card.json"

_spec = importlib.util.spec_from_file_location("drafting_ledger", _MODULE_PATH)
ledger = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(ledger)


class _Usage:
    """Stand-in for the SDK usage object: same attribute names, no dependency."""

    def __init__(self, i, o, cr=0, cw=0):
        self.input_tokens = i
        self.output_tokens = o
        self.cache_read_input_tokens = cr
        self.cache_creation_input_tokens = cw


class _Env:
    """Point the ledger at a scratch data root for the duration of a test."""

    def __init__(self, slug="matter", unit=None):
        self.slug = slug
        self.unit = unit
        self._prev = {}

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = {"SMD_DRAFT_DATA": self.tmp.name, "SMD_SLUG": self.slug, "SMD_UNIT": self.unit}
        for k, v in env.items():
            self._prev[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self.tmp.name

    def __exit__(self, *exc):
        for k, v in self._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()
        return False


class RateCardTest(unittest.TestCase):
    def test_card_declares_its_provenance(self):
        card = json.loads(_CARD_PATH.read_text())
        meta = card["_meta"]
        # An undated rate card is the $145.11 defect waiting to happen: the
        # figure is only trustworthy if you can see how old the prices are.
        self.assertIn("as_of", meta)
        self.assertIn("source", meta)
        self.assertEqual(meta["units"], "cents per million tokens")

    def test_card_carries_the_multipliers_pricing_depends_on(self):
        meta = json.loads(_CARD_PATH.read_text())["_meta"]
        for key in ("batch", "cache_read", "cache_write_5m"):
            self.assertIn(key, meta)
        # Direction, not value: caching must be cheaper than fresh input and
        # batching cheaper than interactive, or price() computes backwards.
        self.assertLess(meta["cache_read"], 1.0)
        self.assertLess(meta["batch"], 1.0)
        self.assertGreater(meta["cache_write_5m"], 1.0)

    def test_every_model_entry_has_both_directions(self):
        card = json.loads(_CARD_PATH.read_text())
        models = [k for k in card if not k.startswith("_")]
        self.assertTrue(models)
        for m in models:
            self.assertIn("in", card[m])
            self.assertIn("out", card[m])
            self.assertGreater(card[m]["out"], card[m]["in"])


class RateResolutionTest(unittest.TestCase):
    def test_exact_match(self):
        self.assertIsNotNone(ledger.rate_for("claude-opus-5"))

    def test_dated_snapshot_resolves_by_prefix(self):
        # A dated id must price against its family, or a model pin silently
        # turns every row unpriced.
        self.assertEqual(ledger.rate_for("claude-opus-5-20260814"), ledger.rate_for("claude-opus-5"))

    def test_unknown_model_returns_none_not_a_default(self):
        self.assertIsNone(ledger.rate_for("some-other-vendor-model"))
        self.assertIsNone(ledger.rate_for(""))
        self.assertIsNone(ledger.rate_for(None))

    def test_meta_is_never_matched_as_a_model(self):
        self.assertIsNone(ledger.rate_for("_meta"))


class PriceTest(unittest.TestCase):
    def test_priced_from_tokens_at_the_card(self):
        card = json.loads(_CARD_PATH.read_text())
        r = card["claude-opus-5"]
        row = {"model": "claude-opus-5", "in": 1_000_000, "out": 1_000_000}
        expected = (r["in"] + r["out"]) / 100.0  # cents-per-M -> dollars
        self.assertAlmostEqual(ledger.price(row), expected, places=6)

    def test_cache_read_is_cheaper_than_fresh_input(self):
        fresh = {"model": "claude-opus-5", "in": 1_000_000, "out": 0}
        cached = {"model": "claude-opus-5", "in": 0, "out": 0, "cache_read": 1_000_000}
        self.assertLess(ledger.price(cached), ledger.price(fresh))

    def test_batch_halves_the_row(self):
        plain = {"model": "claude-sonnet-5", "in": 500_000, "out": 100_000}
        batched = dict(plain, batch=True)
        meta = json.loads(_CARD_PATH.read_text())["_meta"]
        self.assertAlmostEqual(ledger.price(batched), ledger.price(plain) * meta["batch"], places=9)

    def test_unknown_model_is_unpriced_not_free(self):
        # The failure this prevents: an unpriced row totalling as $0.00 and a
        # run reading cheap because a model name was misspelled.
        self.assertIsNone(ledger.price({"model": "nope", "in": 9_999_999, "out": 9_999_999}))

    def test_missing_token_fields_do_not_raise(self):
        self.assertEqual(ledger.price({"model": "claude-sonnet-5"}), 0.0)


class RecordTest(unittest.TestCase):
    def test_row_lands_under_slug_and_unit(self):
        with _Env(slug="matter-alpha", unit="demand-r1") as root:
            ledger.record("compose", "claude-opus-5", _Usage(100, 200))
            path = Path(root) / "matter-alpha" / "runs" / "demand-r1" / "usage-ledger.jsonl"
            self.assertTrue(path.exists())
            row = json.loads(path.read_text().strip())
            self.assertEqual(row["stage"], "compose")
            self.assertEqual(row["in"], 100)
            self.assertEqual(row["out"], 200)

    def test_unit_defaults_to_slug(self):
        with _Env(slug="matter-beta", unit=None) as root:
            ledger.record("audit", "claude-sonnet-5", _Usage(10, 20))
            self.assertTrue((Path(root) / "matter-beta" / "runs" / "matter-beta" / "usage-ledger.jsonl").exists())

    def test_a_stage_without_a_slug_still_writes_visibly(self):
        # The Moussa defect: stages ran without the env block and their money
        # vanished from the ledger entirely. An orphan file keeps the total
        # honest and makes the gap obvious.
        prev = os.environ.get("SMD_SLUG")
        with _Env(slug="x") as root:
            os.environ.pop("SMD_SLUG", None)
            try:
                ledger.record("compose", "claude-opus-5", _Usage(1, 1))
                self.assertTrue((Path(root) / "usage-ledger-orphan.jsonl").exists())
            finally:
                if prev is not None:
                    os.environ["SMD_SLUG"] = prev

    def test_record_never_raises_without_a_data_root(self):
        prev = os.environ.pop("SMD_DRAFT_DATA", None)
        try:
            ledger.record("compose", "claude-opus-5", _Usage(1, 1))  # must not raise
        finally:
            if prev is not None:
                os.environ["SMD_DRAFT_DATA"] = prev

    def test_record_never_raises_on_a_malformed_usage_object(self):
        with _Env():
            ledger.record("compose", "claude-opus-5", object())  # must not raise

    def test_cache_fields_are_captured(self):
        with _Env(slug="m", unit="u") as root:
            ledger.record("compose", "claude-opus-5", _Usage(5, 6, cr=7, cw=8))
            row = json.loads((Path(root) / "m" / "runs" / "u" / "usage-ledger.jsonl").read_text().strip())
            self.assertEqual(row["cache_read"], 7)
            self.assertEqual(row["cache_write"], 8)


class ReportTest(unittest.TestCase):
    def test_report_attributes_by_stage_and_totals(self):
        with _Env(slug="m", unit="u"):
            ledger.record("compose", "claude-opus-5", _Usage(1_000_000, 0))
            ledger.record("audit", "claude-sonnet-5", _Usage(1_000_000, 0))
            out = ledger.report("m", "u")
            blob = json.loads(out.strip().splitlines()[-1])
            self.assertIn("compose", blob["tokens_by_stage"])
            self.assertIn("audit", blob["tokens_by_stage"])
            # Opus input is dearer than Sonnet input, so compose must outrank
            # audit on identical token counts; if it did not, the card is
            # wired backwards.
            self.assertGreater(blob["dollars_by_stage"]["compose"], blob["dollars_by_stage"]["audit"])
            self.assertAlmostEqual(
                blob["dollars_total"], blob["dollars_by_stage"]["compose"] + blob["dollars_by_stage"]["audit"], places=6
            )

    def test_report_counts_unpriced_rows(self):
        with _Env(slug="m", unit="u"):
            ledger.record("compose", "mystery-model", _Usage(10, 10))
            blob = json.loads(ledger.report("m", "u").strip().splitlines()[-1])
            self.assertEqual(blob["unpriced_rows"], 1)

    def test_report_names_an_unrouted_stage(self):
        # An Opus call hiding under an unrecognised stage name is exactly how
        # a routing decision gets silently reversed.
        with _Env(slug="m", unit="u"):
            ledger.record("mystery_stage", "claude-opus-5", _Usage(10, 10))
            out = ledger.report("m", "u")
            blob = json.loads(out.strip().splitlines()[-1])
            self.assertEqual(blob["unrouted_stages"], ["mystery_stage"])
            self.assertIn("unrouted stage", out)

    def test_known_stages_are_not_flagged(self):
        with _Env(slug="m", unit="u"):
            ledger.record("compose", "claude-opus-5", _Usage(10, 10))
            blob = json.loads(ledger.report("m", "u").strip().splitlines()[-1])
            self.assertEqual(blob["unrouted_stages"], [])


class CalibrationTest(unittest.TestCase):
    def test_row_carries_class_chars_and_rate_card_age(self):
        with _Env(slug="m", unit="u") as root:
            ledger.record("compose", "claude-opus-5", _Usage(1000, 100))
            row = ledger.append_calibration("m", "u", artifact_class="demand", chars=2_192_627)
            self.assertEqual(row["artifact_class"], "demand")
            self.assertEqual(row["chars"], 2_192_627)
            # Without the card's age on the row, a stored dollar figure cannot
            # be re-derived later -- the exact defect that produced $145.11.
            self.assertIn("rate_card_as_of", row)
            self.assertTrue((Path(root) / "calibration.jsonl").exists())

    def test_anchors_filter_by_class_and_sort_by_distance(self):
        with _Env(slug="m", unit="u"):
            ledger.record("compose", "claude-opus-5", _Usage(10, 10))
            for cls, chars in (("demand", 1_000_000), ("demand", 5_000_000), ("chronology", 1_050_000)):
                ledger.append_calibration("m", "u", artifact_class=cls, chars=chars)
            got = ledger.anchors("demand", 1_100_000, k=3)
            self.assertEqual([r["chars"] for r in got], [1_000_000, 5_000_000])
            # A chronology must never anchor a demand projection: different
            # cost shape, and the wrong anchor is worse than none.
            self.assertTrue(all(r["artifact_class"] == "demand" for r in got))

    def test_anchors_empty_when_no_corpus(self):
        with _Env(slug="m", unit="u"):
            self.assertEqual(ledger.anchors("demand", 1_000), [])


if __name__ == "__main__":
    unittest.main()
