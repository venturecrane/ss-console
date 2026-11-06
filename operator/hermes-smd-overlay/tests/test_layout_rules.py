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
from shared import layout_store as ls
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
    ("Discovery sets should be double spaced.", {"line_spacing": 2.0}),
    ("Double spaced, including the headings.", {"line_spacing": 2.0, "heading_line_spacing": 2.0}),
    ("Headings single spaced.", {"heading_line_spacing": 1.0}),
    ("12 pt of space before each heading.", {"heading_space_before_pt": 12.0}),
    ("6 points after the headings.", {"heading_space_after_pt": 6.0}),
    ("The first page single spaced.", {"front_single_spaced": True}),
    ("The signature block single spaced.", {"signature_single_spaced": True}),
    ("Put the date beside the By line.", {"signature_date_beside": True}),
    ("Charts in 12 point in depo outlines.", {"table_font_pt": 12.0}),
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
    ("Charts in 12 point in mediation briefs.", "firm's own Word file"),
    ("Charts in 12 point in memos.", "firm's own Word file"),
]


def _merged(got: dict) -> dict:
    out: dict = {}
    for clause in got["compiled"]:
        out.update(clause["layout"])
    return out


@pytest.mark.parametrize("text, layout", COMPILES)
def test_each_mechanical_wording_compiles(text, layout):
    got = lr.compile_rule(text)
    assert _merged(got) == layout, got["prose"]


@pytest.mark.parametrize("text, why", PROSE)
def test_everything_else_stays_prose_and_says_why(text, why):
    got = lr.compile_rule(text)
    assert got["compiled"] == []
    assert why in got["prose"][0]["reason"]


def test_a_rule_naming_a_document_kind_is_scoped_to_it():
    assert lr.compile_rule("Two blank lines before the court name in mediation briefs.")[
        "doc_classes"
    ] == ["mediation_brief"]
    assert lr.compile_rule("Two blank lines before the court name.")["doc_classes"] == []


def test_every_compiled_value_passes_the_validator():
    for text, _ in COMPILES:
        for clause in lr.compile_rule(text)["compiled"]:
            assert all(lr.problems(k, v) == [] for k, v in clause["layout"].items())


# ---- per clause: a negation blocks its own clause, never the rule ---------------------------
def test_a_negation_blocks_only_its_own_clause():
    """FAILS ON #3784: one "never" made the whole rule prose."""
    got = lr.compile_rule(
        "In a mediation brief, never cite the deposition summary; "
        "leave two blank lines before the court name, and file it on pleading paper."
    )
    assert _merged(got) == {"front_court_gap_lines": 2}
    assert got["doc_classes"] == ["mediation_brief"]
    assert [p["reason"].split(",")[0] for p in got["prose"]] == [
        "the clause carries a condition or a negation",
        "the clause names no layout the renderer has a setting for",
    ]
    assert got["compiled"][0]["text"].startswith("leave two blank lines")


def test_a_leading_scope_phrase_is_not_a_clause_of_its_own():
    got = lr.compile_rule("In every mediation brief, leave two blank lines before the court name.")
    assert got["prose"] == [] and len(got["compiled"]) == 1
    assert got["doc_classes"] == ["mediation_brief"]


def test_a_contrast_tail_belongs_to_the_clause_before_it():
    got = lr.compile_rule(
        "The date should be even with the signature line, not on top of the signature block."
    )
    assert _merged(got) == {"signature_date_beside": True}
    assert got["prose"] == []


def test_a_condition_still_blocks_a_clause_with_a_key():
    got = lr.compile_rule("When the brief is long, two blank lines before the court name.")
    assert got["compiled"] == []


# ---- the attorney's real shapes of words (synthetic, no client text) -------------------------
WORDINGS = [
    (
        "End the solid line under the caption at the caption's vertical line, rather than full width.",
        {"caption_rule_below": "left"},
    ),
    (
        "The solid line should only extend to the vertical line in the case caption.",
        {"caption_rule_below": "left"},
    ),
    (
        "There should be a solid line under the last line of the case caption.",
        {"caption_rule_below": True},
    ),
    ("A full width line under the caption.", {"caption_rule_below": True}),
    ("Single space the attorney signature block.", {"signature_single_spaced": True}),
    (
        "Signature line of attorney should be single spaced as it is amended.",
        {"signature_single_spaced": True},
    ),
    (
        "The date line on the attorney signature should be even with the line above the name.",
        {"signature_date_beside": True},
    ),
    (
        "The attorney caption should always be single spaced, as should all information "
        "above the brief (court, case caption, date, time and place).",
        {"front_single_spaced": True},
    ),
    ("Everything above the body single spaced.", {"front_single_spaced": True}),
]


@pytest.mark.parametrize("text, layout", WORDINGS)
def test_the_attorneys_shapes_of_words_compile(text, layout):
    got = lr.compile_rule(text)
    assert _merged(got) == layout, got["prose"]
    assert got["prose"] == []


# ---- "more space": relative, one step, resolved by the lane ----------------------------------
RELATIVE = [
    (
        "There should be more spacing between the attorney info on the first page and who is represented.",
        {"front_attorney_gap_lines": 1},
    ),
    (
        "Should also be more spacing between the court and the caption on the first page.",
        {"front_caption_gap_lines": 1},
    ),
    (
        "There needs to be more spacing between Attorneys for Example Party and the "
        "Superior Court identification.",
        {"front_court_gap_lines": 1},
    ),
    (
        'Leave clear extra spacing between the "Attorneys for" line and the court identification below it.',
        {"front_court_gap_lines": 1},
    ),
    (
        "On the first page leave extra space between the attorney information and the party "
        "represented and between the court name and the caption.",
        {"front_attorney_gap_lines": 1, "front_caption_gap_lines": 1},
    ),
]


@pytest.mark.parametrize("text, relative", RELATIVE)
def test_more_space_compiles_to_a_relative_step_never_a_value(text, relative):
    got = lr.compile_rule(text)
    (clause,) = got["compiled"]
    assert clause["relative"] == relative and clause["layout"] == {}


def test_a_chart_font_ask_compiles_and_says_the_base_owns_it():
    got = lr.compile_rule(
        "Make charts more readable by enlarging font and perhaps adding some color."
    )
    (clause,) = got["compiled"]
    assert clause["relative"] == {"table_font_pt": 1}
    assert clause["base_owned"] == ["table_font_pt"]
    assert [p["text"] for p in got["prose"]] == ["perhaps adding some color."]


def test_a_chart_key_for_a_brief_alone_stays_prose():
    assert lr.compile_rule("Charts in 12 point in mediation briefs.")["compiled"] == []


def test_an_absolute_chart_key_reaching_the_base_says_so():
    (clause,) = lr.compile_rule("Charts in 12 point.")["compiled"]
    assert clause["base_owned"] == ["table_font_pt"]


def test_a_relative_rule_older_than_relative_compile_was_carried_by_hand():
    """Before relative compile existed SMD answered "more space" in the firm's
    config, and a step on top of that would add it twice."""
    text = "Leave extra space between the court name and the caption."
    old = lr.compile_adjustments(
        [{"id": "0000000a", "text": text, "at": "2026-11-05T00:05:29.747Z"}]
    )
    new = lr.compile_adjustments([{"id": "0000000b", "text": text, "at": lr.RELATIVE_FROM}])
    assert old["rules"][0]["compiled"][0]["carried"] is True
    assert new["rules"][0]["compiled"][0]["carried"] is False
    assert lr.compile_rule(text)["compiled"][0]["carried"] is False


# ---- Fix A: double spaced on a base class is the pitch, never line_spacing -------------------
PITCH = {
    "line_spacing_pt": 24.0,
    "heading_line_spacing_pt": 24.0,
    "heading_space_before_pt": 0.0,
    "heading_space_after_pt": 0.0,
}


def test_double_spaced_with_headings_on_a_brief_is_the_pitch():
    """FAILS ON #3784: this wording compiled to line_spacing 2.0, 51 paragraphs
    off the pleading pitch, and the job failed after the paid draft."""
    got = lr.compile_rule("Brief should be double spaced even between paragraph headings.")
    assert _merged(got) == PITCH
    assert got["doc_classes"] == ["mediation_brief"]


def test_double_spaced_on_a_brief_is_the_body_pitch():
    assert _merged(lr.compile_rule("Briefs should be double spaced.")) == {"line_spacing_pt": 24.0}


def test_single_spaced_body_on_a_brief_stays_prose():
    got = lr.compile_rule("Mediation briefs should be single spaced.")
    assert got["compiled"] == [] and "line pitch" in got["prose"][0]["reason"]


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
    assert [(r["rule_id"], len(r["compiled"]), len(r["prose"])) for r in doc["rules"]] == [
        ("00000001", 1, 0),
        ("00000002", 0, 1),
    ]
    assert manifest["layouts"][rel]["compiled"] == 1
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


# ---- the one reader: the letter and the lane ask the same question ---------------------------
def _installed(tmp_path):
    assert _apply(tmp_path, _doc("Two blank lines before the court name.")).applied
    return tmp_path / lr.layout_rel_path("work_product"), tmp_path / MANIFEST_NAME


def test_no_store_at_all_is_not_a_fault(tmp_path):
    assert ls.read(tmp_path).status == ls.NONE
    assert not lr.compiled_on_seat("00000001", "work_product", tmp_path)


def test_a_good_store_reads_ok(tmp_path):
    _installed(tmp_path)
    assert ls.read(tmp_path, lr.problems).status == ls.OK
    assert lr.compiled_on_seat("00000001", "work_product", tmp_path)


def _digest_differs(layout, _manifest):
    layout.write_bytes(
        layout.read_bytes().replace(b'"front_court_gap_lines": 2', b'"front_court_gap_lines": 9')
    )


def _truncated(layout, _manifest):
    layout.write_bytes(layout.read_bytes()[:20])


def _manifest_torn(_layout, manifest):
    manifest.write_text("{")


def _manifest_lost(_layout, manifest):
    manifest.unlink()


def _file_lost(layout, _manifest):
    layout.unlink()


@pytest.mark.parametrize(
    "damage", [_digest_differs, _truncated, _manifest_torn, _manifest_lost, _file_lost]
)
def test_a_damaged_store_is_unusable_and_the_letter_agrees(tmp_path, damage):
    """FAILS ON #3784: compiled_on_seat skipped the digest, so a digest mismatch
    or a torn manifest told the firm "in effect" while the lane applied nothing."""
    damage(*_installed(tmp_path))
    store = ls.read(tmp_path, lr.problems)
    assert store.status == ls.UNUSABLE and store.reason
    assert not lr.compiled_on_seat("00000001", "work_product", tmp_path)


def test_a_rule_failing_the_validator_makes_the_store_unusable(tmp_path):
    layout, manifest = _installed(tmp_path)
    blob = layout.read_bytes().replace(
        b'"front_court_gap_lines": 2', b'"front_court_gap_lines": 99'
    )
    layout.write_bytes(blob)
    doc = json.loads(manifest.read_text())
    doc["layouts"][lr.layout_rel_path("work_product")]["sha256"] = hashlib.sha256(blob).hexdigest()
    manifest.write_text(json.dumps(doc))
    assert ls.read(tmp_path, lr.problems).status == ls.UNUSABLE


def test_clause_truth_reads_the_installed_file(tmp_path):
    _apply(tmp_path, _doc("Never use the word plainly; two blank lines before the court name."))
    truth = lr.clause_truth("00000001", "work_product", tmp_path)
    assert [c["text"] for c in truth["compiled"]] == ["two blank lines before the court name."]
    assert [c["text"] for c in truth["prose"]] == ["Never use the word plainly"]
    assert lr.clause_truth("00000009", "work_product", tmp_path) is None
