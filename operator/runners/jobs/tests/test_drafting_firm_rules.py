"""A firm's installed layout rule changes the next document, checked in code
(2026-11-05).

THE DEFECT. An attorney's layout rule was installed to the seat's spec store
(``specs/classes/work_product/format.md``) and nothing that renders the firm's
documents read it, so the next brief ignored it. The seat's spec applier now
compiles a mechanical rule into ``classes/work_product/layout.json``
(``shared/layout_rules.py`` in the overlay) and ``firm_rules`` lays it over the
firm's layout at job start: on our renderer through ``format.layout``, on the
firm's own Word file by revise's in-place editor, proven to touch only the
rule's leaves. Every fixture is synthetic.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace

import docx
import pytest

import test_drafting_brief_base as bb
import test_drafting_briefpage_v7 as v7
from drafting_testkit import firm_data
from jobs import firmbase
from jobs.drafting import brief_base, firm, firm_rules, format_check, pleading, render
from jobs.drafting.revise import checklist, inplace

_OVERLAY = Path(__file__).resolve().parents[3] / "hermes-smd-overlay" / "shared" / "layout_rules.py"


def _overlay():
    spec = importlib.util.spec_from_file_location("overlay_layout_rules", _OVERLAY)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


LR = _overlay()


def _install(spec_dir: Path, *texts: str) -> dict:
    """What the seat's spec applier installs for these confirmed rules."""
    adjustments = [{"id": f"{i:08x}", "text": t} for i, t in enumerate(texts, start=1)]
    doc = LR.compile_adjustments(adjustments)
    blob = json.dumps(doc, sort_keys=True).encode()
    path = spec_dir / firm_rules.LAYOUT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
    manifest = {"layouts": {firm_rules.LAYOUT_REL: {"sha256": hashlib.sha256(blob).hexdigest()}}, "specs": {}}
    (spec_dir / "manifest.json").write_text(json.dumps(manifest))
    return doc


# ---- parity: the seat's compiler and this lane hold one key set --------------------------
def test_the_compilers_keys_are_the_lanes_keys():
    assert LR.LAYOUT_KEYS == checklist.LAYOUT_KEYS
    assert set(LR.LAYOUT_KEYS) <= set(firm.LAYOUT_KEYS)
    assert tuple(LR.DOC_CLASSES) == firm.CLASSES
    assert set(LR.LAYOUT_RULE_CLASSES) == {firm_rules.RULE_CLASS}
    assert LR.layout_rel_path(firm_rules.RULE_CLASS) == firm_rules.LAYOUT_REL
    assert LR.SCHEMA_VERSION == firm_rules.SCHEMA_VERSION
    assert set(LR.BASE_OWNS) == set(brief_base.BASE_OWNS)
    assert set(LR.BASE_CLASSES) == set(firm.FIRM_BASE_CLASSES)


@pytest.mark.parametrize("key", sorted(checklist.LAYOUT_KEYS))
@pytest.mark.parametrize("value", [0, 1, 2, 3, 10, 11, 1.5, 6.0, 49.0, True, "left", "1F3864", "nothex", None])
def test_the_compilers_validator_is_the_lanes(key, value):
    assert bool(LR.problems(key, value)) == bool(checklist._layout_problems({key: value}))


def test_every_compiled_key_has_an_in_place_applier():
    """A base-rendered brief takes a rule only by the in-place editor, so every
    key the compiler can emit for a base class must be one it edits."""
    for key in set(LR.LAYOUT_KEYS) - set(LR.BASE_OWNS):
        assert (
            key in inplace._TABLE_LEAVES
            or key in inplace.lay.FRONT_KEYS
            or key
            in (
                "line_spacing",
                "line_spacing_pt",
                "heading_line_spacing",
                "heading_line_spacing_pt",
                "heading_space_before_pt",
                "heading_space_after_pt",
            )
        ), key


# ---- the read ---------------------------------------------------------------------------
def test_an_installed_rule_is_read(spec_store):
    _install(spec_store, "Two blank lines before the court name.")
    (rule,) = firm_rules.read()
    assert rule["layout"] == {"front_court_gap_lines": 2}


def test_a_prose_rule_is_never_read(spec_store):
    doc = _install(spec_store, "Make the introduction more persuasive.")
    assert doc["rules"] == [] and doc["prose"][0]["rule_id"] == "00000001"
    assert firm_rules.read() == []


def test_a_file_that_does_not_match_its_manifest_applies_nothing(spec_store):
    _install(spec_store, "Two blank lines before the court name.")
    p = spec_store / firm_rules.LAYOUT_REL
    p.write_bytes(p.read_bytes().replace(b'"front_court_gap_lines": 2', b'"front_court_gap_lines": 9'))
    assert firm_rules.read() == []


def test_a_value_out_of_range_is_refused_at_compile(spec_store):
    doc = _install(spec_store, "Twelve blank lines before the court name.")
    assert doc["rules"] == []
    assert "from 0 to 10" in doc["prose"][0]["reason"]


# ---- our renderer -----------------------------------------------------------------------
def test_a_compiled_rule_changes_the_next_render_and_the_check_enforces_it(tmp_path, spec_store):
    _install(spec_store, "Three blank lines before the court name.")
    layout, rows = firm_rules.for_class(firm_rules.read(), v7.CLS, on_base=False)
    assert layout == {"front_court_gap_lines": 3} and rows[0]["applied"] is True
    ruled = v7._fmt(**{**v7.V7, **layout})
    path = _render(tmp_path / "a", ruled)
    d = docx.Document(str(path))
    texts = [p.text for p in d.paragraphs]
    g = texts.index("GAMMA EXAMPLE")
    assert texts[g + 1 : g + 4] == ["", "", ""]
    assert format_check.check(path, v7.CLS, ruled, "", None, "plain").ok
    # The falsifier: the brief rendered before the rule, read against it.
    before = _render(tmp_path / "b", v7._fmt(**v7.V7))
    res = format_check.check(before, v7.CLS, ruled, "", None, "plain")
    assert any("blank line(s) before the court's name" in f for f in res.fails), res.fails


def _render(where: Path, fmt: dict) -> Path:
    where.mkdir(parents=True, exist_ok=True)
    return v7._render(where, fmt, "plain")


# ---- the firm's own Word file -----------------------------------------------------------
@pytest.fixture(scope="module")
def tmpl(tmp_path_factory) -> firmbase.FirmBase:
    fmt = bb._fmt()
    path, _ = render.render(bb.MD, bb.CLS, tmp_path_factory.mktemp("accepted") / "accepted.docx", fmt)
    pleading.apply_for(path, fmt, bb.CLS)
    buf = io.BytesIO()
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(buf, "w") as out:
        for i in src.infolist():
            data = src.read(i.filename)
            if i.filename == "docProps/core.xml":
                data = re.sub(rb"(<dc:creator>)[^<]*", rb"\1A Firm Person", data)
                data = re.sub(rb"(<cp:lastModifiedBy>)[^<]*", rb"\1A Firm Person", data)
            out.writestr(i, data)
    accepted = buf.getvalue()
    blob, _notes = brief_base.adopt(accepted, bb._accepted(accepted))
    return firmbase.FirmBase.load(bb.CLS, firmbase.Source("m", "f", "A firm brief.docx", "0" * 64), blob)


def _court_gap(path: Path) -> int:
    texts = [p.text for p in docx.Document(str(path)).paragraphs]
    g = texts.index("GAMMA EXAMPLE")
    c = next(i for i, t in enumerate(texts) if t.startswith("SUPERIOR COURT"))
    return c - g - 1


def test_a_compiled_rule_moves_a_base_rendered_brief_and_the_check_passes(tmp_path, tmpl, monkeypatch):
    rule = {"front_court_gap_lines": 3}
    fmt = bb._fmt()
    fmt["layout"][bb.CLS].update(rule)
    monkeypatch.setattr(brief_base, "load_base", lambda *_a: tmpl)
    path, report, _laid, checked = brief_base.lay_out(
        bb.MD, tmp_path / "out.docx", bb.CLS, fmt, "pleading", (tmp_path / "x", {}), rule
    )
    assert _court_gap(path) == 3
    assert any("layout rules laid onto the base" in n for n in report["notes"])
    res = format_check.check(path, bb.CLS, checked, "", None, "pleading")
    assert [f for f in res.fails if "court" in f or "pleading" in f or "first page" in f] == [], res.fails
    # The falsifier: the same base without the rule, read against it.
    plain, _ = brief_base.render_into(bb.MD, tmp_path / "plain.docx", tmpl, "pleading")
    assert _court_gap(plain) == 2
    res = format_check.check(plain, bb.CLS, checked, "", None, "pleading")
    assert any("blank line(s) before the court's name" in f for f in res.fails), res.fails


def test_a_tamper_outside_the_rules_leaves_is_refused(tmp_path, tmpl, monkeypatch):
    path, _ = brief_base.render_into(bb.MD, tmp_path / "out.docx", tmpl, "pleading")
    before = path.read_bytes()
    real = inplace.edit_layout

    def tamper(ed, layout):
        real(ed, layout)
        heading = next(p for p in ed.doc.paragraphs if p.text.startswith("I."))
        heading.runs[0].bold = not heading.runs[0].bold  # a leaf no rule names

    monkeypatch.setattr(inplace, "edit_layout", tamper)
    lost = firm_rules.onto_base(path, {"front_court_gap_lines": 3})
    assert lost and any("no ask names" in x for x in lost), lost
    assert path.read_bytes() == before  # nothing written
    monkeypatch.setattr(brief_base, "load_base", lambda *_a: tmpl)
    with pytest.raises(brief_base.DraftingFailed, match="firm_base_unfit: a firm layout rule"):
        brief_base.lay_out(
            bb.MD,
            tmp_path / "o2.docx",
            bb.CLS,
            bb._fmt(),
            "pleading",
            (tmp_path / "x", {}),
            {"front_court_gap_lines": 3},
        )


def test_a_key_the_base_owns_is_recorded_unapplied_on_a_base():
    rules = [{"rule_id": "0000000a", "layout": {"table_font_pt": 12.0}, "doc_classes": ["mediation_brief"]}]
    layout, rows = firm_rules.for_class(rules, "mediation_brief", on_base=True)
    assert layout == {} and rows[0]["applied"] is False
    layout, rows = firm_rules.for_class(rules, "mediation_brief", on_base=False)
    assert layout == {"table_font_pt": 12.0} and rows[0]["applied"] is True


def test_a_rule_naming_another_class_leaves_this_one_alone():
    rules = [{"rule_id": "0000000b", "layout": {"front_court_gap_lines": 1}, "doc_classes": ["memo"]}]
    assert firm_rules.for_class(rules, "mediation_brief", on_base=False) == ({}, [])


# ---- job start ----------------------------------------------------------------------------
class _Run:
    def __init__(self, tmp_path: Path, base: bool = False) -> None:
        self.cls = "mediation_brief"
        self.data = tmp_path / "data"
        self.data.mkdir(parents=True)
        self.job = SimpleNamespace(job_id="01DRAFTJ0B000000000000000A")
        data = copy.deepcopy(firm_data())
        self.firm = SimpleNamespace(data=data, firm_base=lambda cls: (Path("b"), {}) if base else None)
        self.st: dict = {}

    def _state(self) -> dict:
        return dict(self.st)

    def _put(self, k, v) -> None:
        self.st[k] = v


def test_job_start_lays_the_rule_over_and_records_a_rule_read(tmp_path, spec_store):
    _install(spec_store, "Three blank lines before the court name.")
    run = _Run(tmp_path)
    assert firm_rules.start(run) == {"front_court_gap_lines": 3}
    assert run.firm.data["format"]["layout"]["mediation_brief"]["front_court_gap_lines"] == 3
    (row,) = [json.loads(x) for x in (run.data / firm_rules.LEDGER).read_text().splitlines()]
    assert row == {
        **row,
        "action_type": "RULE_READ",
        "rule_id": "00000001",
        "job_id": run.job.job_id,
        "lane": "drafting",
        "key": "front_court_gap_lines",
        "applied": True,
    }


def test_a_resumed_job_keeps_the_rules_it_started_with(tmp_path, spec_store):
    _install(spec_store, "Three blank lines before the court name.")
    run = _Run(tmp_path)
    firm_rules.start(run)
    _install(spec_store, "One blank line before the court name.")
    again = _Run(tmp_path / "r")
    again.st = run.st
    assert firm_rules.start(again) == {"front_court_gap_lines": 3}


@pytest.fixture
def spec_store(_spec_store: Path) -> Path:
    _spec_store.mkdir(parents=True, exist_ok=True)
    return _spec_store
