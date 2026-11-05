"""A firm's layout rule compiles to a renderer key, or stays prose (2026-11-05).

THE DEFECT. A work_product rule ("two blank lines before the court name") was
installed to ``classes/work_product/format.md`` and nothing that renders the
firm's documents read it. ``shared.layout_rules`` compiles the mechanical ones
and the spec applier writes them to ``classes/work_product/layout.json`` for the
drafting lane. What is pinned here: exactly which wordings compile, that an
out-of-range or conditional rule never does, that the applier writes the file
under the manifest's digest, and that a seat installed before compiling existed
recompiles on its next poll. The lane's side (that it applies the file, and the
key and range parity) is ``runners/jobs/tests/test_drafting_firm_rules.py``.
"""

from __future__ import annotations

import hashlib
import io
import json

import pytest

from shared import layout_rules as lr
from spec_applier.applier import MANIFEST_NAME, SCHEMA_VERSION, SpecApplyOutcome, apply

#: Every wording that compiles, and to what. This table IS the grammar's
#: contract: a wording missing here is prose.
COMPILES = [
    ("Two blank lines before the court name.", {"front_court_gap_lines": 2}),
    ("Put 2 blank lines above the court's name.", {"front_court_gap_lines": 2}),
    ('Two blank lines from "Attorneys for" to the court.', {"front_court_gap_lines": 2}),
    ("Two blank lines between the party represented and the court.", {"front_court_gap_lines": 2}),
    ("One blank line before the caption.", {"front_caption_gap_lines": 1}),
    ("A blank line between the court name and the caption.", {"front_caption_gap_lines": 1}),
    ('One blank line before "Attorneys for".', {"front_attorney_gap_lines": 1}),
    ("Two blank signature lines above the Dated line.", {"signature_gap_lines": 2}),
    ("Briefs should be double spaced.", {"line_spacing": 2.0}),
    ("Double spaced, including the headings.", {"line_spacing": 2.0, "heading_line_spacing": 2.0}),
    ("Headings single spaced.", {"heading_line_spacing": 1.0}),
    ("12 pt of space before each heading.", {"heading_space_before_pt": 12.0}),
    ("6 points after the headings.", {"heading_space_after_pt": 6.0}),
    ("The first page single spaced.", {"front_single_spaced": True}),
    ("The signature block single spaced.", {"signature_single_spaced": True}),
    ("Put the date beside the By line.", {"signature_date_beside": True}),
    ("Charts in 12 point in memos.", {"table_font_pt": 12.0}),
]

PROSE = [
    ("Make the introduction more persuasive.", "no layout"),
    ("Do not put blank lines before the court name.", "condition or a negation"),
    ("Two blank lines before the court name when the brief is long.", "condition or a negation"),
    ("Twelve blank lines before the court name.", "from 0 to 10"),
    ("Charts in 4 point in memos.", "from 8.0 to 20.0"),
    (
        "Two blank lines before the court name, and three blank lines before the court.",
        "more than one value",
    ),
    ("Charts in 12 point.", "firm's own Word file"),  # reaches the base-rendered brief
    ("Charts in 12 point in mediation briefs.", "firm's own Word file"),
]


@pytest.mark.parametrize("text, layout", COMPILES)
def test_each_mechanical_wording_compiles(text, layout):
    got, _classes, reason = lr.compile_rule(text)
    assert got == layout, reason


@pytest.mark.parametrize("text, why", PROSE)
def test_everything_else_stays_prose_and_says_why(text, why):
    got, _classes, reason = lr.compile_rule(text)
    assert got is None
    assert why in reason


def test_a_rule_naming_a_document_kind_is_scoped_to_it():
    assert lr.compile_rule("Two blank lines before the court name in mediation briefs.")[1] == [
        "mediation_brief"
    ]
    assert lr.compile_rule("Two blank lines before the court name.")[1] == []


def test_every_compiled_value_passes_the_validator():
    for text, _ in COMPILES:
        layout, _c, _r = lr.compile_rule(text)
        assert all(lr.problems(k, v) == [] for k, v in layout.items())


def test_the_layout_class_is_an_output_class():
    from shared import output_classes

    assert lr.LAYOUT_RULE_CLASSES <= output_classes.OUTPUT_CLASSES


# ---- the applier writes it ------------------------------------------------------------------
class _S3:
    def __init__(self, blob: bytes):
        self.blob = blob

    def get_object(self, *, Bucket: str, Key: str):  # noqa: N803 - boto3 kwargs
        return {"Body": io.BytesIO(self.blob)}


def _doc(*texts: str) -> bytes:
    adjustments = [
        {
            "id": f"{i:08x}",
            "text": t,
            "instructed_by": "a@firm.example",
            "at": "2026-11-05T00:00:00Z",
        }
        for i, t in enumerate(texts, start=1)
    ]
    entry = (
        {"format": {"adjustments": adjustments}}
        if adjustments
        else {"voice": {"body": "x", "sha256": _h("x")}}
    )
    return json.dumps(
        {"schema_version": SCHEMA_VERSION, "classes": {"work_product": entry}}
    ).encode()


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _apply(tmp_path, blob):
    return apply(s3_client=_S3(blob), bucket="b", slug="s", spec_dir=tmp_path)


def test_the_applier_writes_the_compiled_rules_under_the_manifests_digest(tmp_path):
    res = _apply(tmp_path, _doc("Two blank lines before the court name.", "Be warmer."))
    assert res.applied
    rel = lr.layout_rel_path("work_product")
    blob = (tmp_path / rel).read_bytes()
    manifest = json.loads((tmp_path / MANIFEST_NAME).read_text())
    assert manifest["layouts"][rel]["sha256"] == hashlib.sha256(blob).hexdigest()
    doc = json.loads(blob)
    assert [r["rule_id"] for r in doc["rules"]] == ["00000001"]
    assert [r["rule_id"] for r in doc["prose"]] == ["00000002"]
    assert lr.compiled_on_seat("00000001", "work_product", tmp_path)
    assert not lr.compiled_on_seat("00000002", "work_product", tmp_path)
    assert rel not in manifest["specs"]  # the spec gate's map is unchanged


def test_a_seat_installed_before_compiling_recompiles_on_its_next_poll(tmp_path):
    blob = _doc("Two blank lines before the court name.")
    assert _apply(tmp_path, blob).applied
    manifest_path = tmp_path / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    assert _apply(tmp_path, blob).outcome is SpecApplyOutcome.UNCHANGED
    manifest.pop("layout_schema")
    manifest.pop("layouts")
    manifest_path.write_text(json.dumps(manifest))
    (tmp_path / lr.layout_rel_path("work_product")).unlink()
    assert _apply(tmp_path, blob).applied
    assert lr.compiled_on_seat("00000001", "work_product", tmp_path)


def test_a_layout_file_no_longer_declared_is_pruned(tmp_path):
    _apply(tmp_path, _doc("Two blank lines before the court name."))
    res = _apply(tmp_path, _doc())
    assert lr.layout_rel_path("work_product") in res.pruned
    assert not (tmp_path / lr.layout_rel_path("work_product")).exists()
