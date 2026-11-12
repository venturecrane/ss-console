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
from jobs.drafting import base_lay, brief_base, firm, firm_rules, format_check, layout_store, pleading, render
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
    adjustments = [{**a, "at": "2026-12-01T00:00:00Z"} for a in adjustments]
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
    assert LR.RELATIVE_STEP == firm_rules.RELATIVE_STEP
    assert firm_rules.LAYOUT_REL == layout_store.LAYOUT_REL
    assert LR.SCHEMA_VERSION == layout_store.SCHEMA_VERSION
    # The bytes of layout_store.py are held to the overlay's by operator/bin/gen-skill-shared.py --check.
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
    assert rule["compiled"][0]["layout"] == {"front_court_gap_lines": 2}


def test_a_prose_rule_is_never_read(spec_store):
    doc = _install(spec_store, "Make the introduction more persuasive.")
    assert doc["rules"][0]["compiled"] == [] and doc["rules"][0]["prose"]
    assert firm_rules.read()[0]["compiled"] == []


def test_a_file_that_does_not_match_its_manifest_applies_nothing(spec_store):
    _install(spec_store, "Two blank lines before the court name.")
    p = spec_store / firm_rules.LAYOUT_REL
    p.write_bytes(p.read_bytes().replace(b'"front_court_gap_lines": 2', b'"front_court_gap_lines": 9'))
    assert firm_rules.read() == []


def test_a_value_out_of_range_is_refused_at_compile(spec_store):
    doc = _install(spec_store, "Twelve blank lines before the court name.")
    assert doc["rules"][0]["compiled"] == []
    assert "from 0 to 10" in doc["rules"][0]["prose"][0]["reason"]


# ---- our renderer -----------------------------------------------------------------------
def test_a_compiled_rule_changes_the_next_render_and_the_check_enforces_it(tmp_path, spec_store):
    _install(spec_store, "Three blank lines before the court name.")
    layout, rows, _notes = firm_rules.for_class(firm_rules.read(), v7.CLS, on_base=False)
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
    path, report, _laid, checked = base_lay.lay_out(
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
        base_lay.lay_out(
            bb.MD,
            tmp_path / "o2.docx",
            bb.CLS,
            bb._fmt(),
            "pleading",
            (tmp_path / "x", {}),
            {"front_court_gap_lines": 3},
        )


def _rule(rule_id="0000000a", layout=None, relative=None, classes=(), base_owned=(), carried=False):
    clause = {
        "text": "t",
        "layout": layout or {},
        "relative": relative or {},
        "base_owned": list(base_owned),
        "carried": carried,
    }
    return {"rule_id": rule_id, "doc_classes": list(classes), "compiled": [clause], "prose": []}


def test_a_key_the_base_owns_is_recorded_unapplied_on_a_base():
    rules = [_rule(layout={"table_font_pt": 12.0}, classes=["mediation_brief"])]
    layout, rows, _n = firm_rules.for_class(rules, "mediation_brief", on_base=True)
    assert layout == {} and rows[0]["applied"] is False
    layout, rows, _n = firm_rules.for_class(rules, "mediation_brief", on_base=False)
    assert layout == {"table_font_pt": 12.0} and rows[0]["applied"] is True


def test_a_rule_naming_another_class_leaves_this_one_alone():
    rules = [_rule(layout={"front_court_gap_lines": 1}, classes=["memo"])]
    assert firm_rules.for_class(rules, "mediation_brief", on_base=False) == ({}, [], [])


# ---- relative: the effective value plus one, capped, once ----------------------------------
def test_a_relative_rule_is_the_effective_value_plus_one():
    rules = [_rule(relative={"front_court_gap_lines": 1})]
    layout, rows, notes = firm_rules.for_class(
        rules, "mediation_brief", on_base=False, effective={"front_court_gap_lines": 2}
    )
    assert layout == {"front_court_gap_lines": 3}
    assert rows[0]["baseline"] == 2 and rows[0]["value"] == 3 and rows[0]["relative"] == 1
    assert "3" in notes[0] and "2" in notes[0] and "\u2014" not in notes[0]


def test_a_relative_rule_is_capped_by_the_keys_range():
    rules = [_rule(relative={"front_court_gap_lines": 1})]
    layout, rows, notes = firm_rules.for_class(
        rules, "mediation_brief", on_base=False, effective={"front_court_gap_lines": 10}
    )
    assert layout == {}  # already at the top: nothing to lay, and it says why
    assert rows[0]["applied"] is False and "limit" in rows[0]["reason"]
    assert notes == []
    rules = [_rule(relative={"table_font_pt": 1})]
    layout, _r, _n = firm_rules.for_class(rules, "memo", on_base=False, effective={"table_font_pt": 19.5})
    assert layout == {"table_font_pt": 20.0}


def test_a_relative_rule_reads_an_earlier_rules_value_as_its_baseline():
    rules = [
        _rule("0000000a", layout={"front_court_gap_lines": 4}),
        _rule("0000000b", relative={"front_court_gap_lines": 1}),
    ]
    layout, _r, _n = firm_rules.for_class(rules, "mediation_brief", on_base=False, effective={})
    assert layout == {"front_court_gap_lines": 5}


def test_a_pinned_relative_rule_does_not_grow():
    """FAILS WITHOUT THE PIN: an SMD release folds the rule's value into the
    firm's layout, and a fresh step on top would give it twice."""
    rules = [_rule(relative={"front_court_gap_lines": 1})]
    pins: dict = {}
    first, _r, _n = firm_rules.for_class(
        rules, "mediation_brief", on_base=False, effective={"front_court_gap_lines": 1}, pins=pins
    )
    assert first == {"front_court_gap_lines": 2}
    same, rows, notes = firm_rules.for_class(
        rules, "mediation_brief", on_base=False, effective={"front_court_gap_lines": 1}, pins=pins
    )
    assert same == {"front_court_gap_lines": 2} and rows[0]["baseline"] == 1 and "2" in notes[0]
    folded, rows, notes = firm_rules.for_class(
        rules, "mediation_brief", on_base=False, effective={"front_court_gap_lines": 2}, pins=pins
    )
    assert folded == {} and rows[0]["applied"] is False and notes == []


def test_a_later_layout_past_the_pin_wins():
    rules = [_rule(relative={"front_court_gap_lines": 1})]
    pins = {"0000000a:mediation_brief:front_court_gap_lines": {"baseline": 1, "value": 2}}
    layout, rows, _n = firm_rules.for_class(
        rules, "mediation_brief", on_base=False, effective={"front_court_gap_lines": 5}, pins=pins
    )
    assert layout == {} and "past" in rows[0]["reason"]


def test_a_relative_rule_carried_by_hand_changes_nothing():
    rules = [_rule(relative={"front_court_gap_lines": 1}, carried=True)]
    layout, rows, notes = firm_rules.for_class(
        rules, "mediation_brief", on_base=False, effective={"front_court_gap_lines": 2}
    )
    assert layout == {} and rows[0]["applied"] is False and "by hand" in rows[0]["reason"]
    assert notes == []


# ---- Fix A in the lane: a class on pleading paper or a base never takes line_spacing -------
@pytest.mark.parametrize("paper, on_base", [("pleading", False), ("plain", True), ("pleading", True)])
def test_double_spaced_with_no_class_named_becomes_the_pitch_where_the_pitch_rules(paper, on_base):
    rules = [_rule(layout={"line_spacing": 2.0, "heading_line_spacing": 2.0})]
    layout, _r, _n = firm_rules.for_class(rules, "mediation_brief", on_base=on_base, paper=paper, pitch=24.0)
    assert layout == {
        "line_spacing_pt": 24.0,
        "heading_line_spacing_pt": 24.0,
        "heading_space_before_pt": 0.0,
        "heading_space_after_pt": 0.0,
    }


def test_double_spaced_on_plain_paper_stays_line_spacing():
    rules = [_rule(layout={"line_spacing": 2.0})]
    layout, _r, _n = firm_rules.for_class(rules, "discovery_set", on_base=False, paper="plain", pitch=24.0)
    assert layout == {"line_spacing": 2.0}


def test_single_spaced_is_refused_where_the_pitch_rules():
    rules = [_rule(layout={"line_spacing": 1.0})]
    layout, rows, _n = firm_rules.for_class(rules, "mediation_brief", on_base=True, paper="pleading", pitch=24.0)
    assert layout == {} and rows[0]["applied"] is False and rows[0]["refused"] is True


# ---- job start ----------------------------------------------------------------------------
class _Run:
    def __init__(self, tmp_path: Path, base: bool = False) -> None:
        self.cls = "mediation_brief"
        self.data = tmp_path / "data"
        self.data.mkdir(parents=True)
        self.job = SimpleNamespace(job_id="01DRAFTJ0B000000000000000A", revise=None)
        data = copy.deepcopy(firm_data())
        self.firm = SimpleNamespace(data=data, firm_base=lambda cls: (Path("b"), {}) if base else None)
        self.st: dict = {}
        self.rule_notes: list = []

    def paper(self) -> str:
        return "plain"

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


def test_a_revision_reads_no_rules_and_records_none(tmp_path, spec_store):
    """A revision edits the attorney's accepted file and only as he asked: it
    never applies a firm rule, so it must never record one as applied."""
    from jobs.drafting import run as run_mod

    _install(spec_store, "Three blank lines before the court name.")
    assert run_mod.rules_for_job(_Run(tmp_path / "first")) == {"front_court_gap_lines": 3}
    run = _Run(tmp_path / "revision")
    run.job.revise = {"prior_job_id": "x"}
    assert run_mod.rules_for_job(run) == {}
    assert not (run.data / firm_rules.LEDGER).exists()
    assert "front_court_gap_lines" not in run.firm.data["format"]["layout"]["mediation_brief"]


# ---- the probe: a value the check would refuse never reaches a paid draft ------------------
def _fake_probe(monkeypatch, fails_when):
    """format_check reads the laid layout: ``fails_when(layout)`` is its finding."""

    def lay_out(_md, dest, _cls, _fmt, _paper, _held, rules=None):
        dest.write_bytes(b"x")
        return dest, {"notes": []}, [], {"layout": dict(rules or {})}

    def check(_path, _cls, checked, *_a):
        found = fails_when(checked["layout"])
        return SimpleNamespace(fails=[found] if found else [])

    monkeypatch.setattr(base_lay, "lay_out", lay_out)
    monkeypatch.setattr(format_check, "check", check)


def test_a_value_the_check_refuses_is_dropped_before_any_paid_draft(tmp_path, spec_store, monkeypatch):
    _install(spec_store, "Three blank lines before the court name.", "One blank line before the caption.")
    _fake_probe(monkeypatch, lambda laid: "pleading: off pitch" if "front_court_gap_lines" in laid else "")
    run = _Run(tmp_path)
    assert firm_rules.start(run) == {"front_caption_gap_lines": 1}
    rows = [json.loads(x) for x in (run.data / firm_rules.LEDGER).read_text().splitlines()]
    refused = [r for r in rows if r.get("refused")]
    assert [r["key"] for r in refused] == ["front_court_gap_lines"]
    assert any("front_court_gap_lines" in r["key"] and r["applied"] is False for r in rows)
    assert run.rule_notes and "could not" in run.rule_notes[-1]


def test_a_probe_that_cannot_run_refuses_every_rule(tmp_path, spec_store, monkeypatch):
    _install(spec_store, "Three blank lines before the court name.")

    def boom(*_a, **_k):
        raise brief_base.DraftingFailed("firm_base_unfit: x")

    monkeypatch.setattr(base_lay, "lay_out", boom)
    run = _Run(tmp_path)
    assert firm_rules.start(run) == {}
    (row,) = [json.loads(x) for x in (run.data / firm_rules.LEDGER).read_text().splitlines()]
    assert row["refused"] is True


def test_the_probe_really_catches_line_spacing_on_pleading_paper(tmp_path, tmpl, monkeypatch):
    """The falsifier for the probe with nothing faked: the value #3784 let through."""
    monkeypatch.setattr(brief_base, "load_base", lambda *_a: tmpl)
    fmt = bb._fmt()
    assert firm_rules.probe_new_fails(tmp_path, bb.CLS, fmt, "pleading", (tmp_path / "x", {}), {"line_spacing": 2.0})
    assert not firm_rules.probe_new_fails(
        tmp_path, bb.CLS, fmt, "pleading", (tmp_path / "x", {}), {"front_court_gap_lines": 3}
    )


# ---- Fix C: an unusable store is loud, and a resume is not frozen on it --------------------
def _tear(spec_store: Path) -> None:
    p = spec_store / firm_rules.LAYOUT_REL
    p.write_bytes(p.read_bytes()[:30])


def test_a_torn_store_writes_a_row_and_applies_nothing(tmp_path, spec_store, caplog):
    _install(spec_store, "Three blank lines before the court name.")
    _tear(spec_store)
    run = _Run(tmp_path)
    assert firm_rules.start(run) == {}
    (row,) = [json.loads(x) for x in (run.data / firm_rules.LEDGER).read_text().splitlines()]
    assert row["action_type"] == "RULE_STORE_UNUSABLE" and row["reason"]
    assert "unusable" in caplog.text


def test_a_resume_after_repair_reads_the_rules_a_torn_start_could_not(tmp_path, spec_store):
    """FAILS ON #3784: the empty result was frozen in the job's state."""
    _install(spec_store, "Three blank lines before the court name.")
    p = spec_store / firm_rules.LAYOUT_REL
    good = p.read_bytes()
    _tear(spec_store)
    run = _Run(tmp_path)
    assert firm_rules.start(run) == {}
    assert firm_rules.STATE_KEY not in run.st
    p.write_bytes(good)
    again = _Run(tmp_path / "r")
    again.st = run.st
    assert firm_rules.start(again) == {"front_court_gap_lines": 3}
    assert firm_rules.STATE_KEY in again.st


def test_an_absent_store_is_silent_and_writes_nothing(tmp_path, spec_store):
    run = _Run(tmp_path)
    assert firm_rules.start(run) == {}
    assert not (run.data / firm_rules.LEDGER).exists()


class _Daemon:
    LANE = "drafting"

    def __init__(self, jd: Path) -> None:
        self.jd = jd
        self.alerts: list = []

    def job_dir(self, _job_id):
        return self.jd


def test_the_daemon_pages_an_unusable_store_before_the_child_runs(tmp_path, spec_store, monkeypatch):
    from jobs.proof import watch

    _install(spec_store, "Three blank lines before the court name.")
    d = _Daemon(tmp_path)
    monkeypatch.setattr(watch, "alert", lambda _d, job_id, code, detail="": d.alerts.append((job_id, code, detail)))
    firm_rules.alert_unusable(d, "J1")
    assert d.alerts == []
    _tear(spec_store)
    firm_rules.alert_unusable(d, "J1")
    assert d.alerts[0][:2] == ("J1", firm_rules.UNUSABLE_CODE)


def test_the_daemon_pages_a_refused_value_with_ids_only(tmp_path, monkeypatch):
    from jobs.proof import watch

    d = _Daemon(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(watch, "alert", lambda _d, job_id, code, detail="": d.alerts.append((job_id, code, detail)))
    firm_rules.alert_refused(d, "J1")
    assert d.alerts == []
    (data / firm_rules.LEDGER).write_text(
        json.dumps({"rule_id": "0000000a", "key": "line_spacing", "applied": False, "refused": True}) + "\n"
    )
    firm_rules.alert_refused(d, "J1")
    assert d.alerts == [("J1", firm_rules.REFUSED_CODE, "0000000a:line_spacing")]


def test_the_proof_codes_are_the_brokers():
    from jobs.proof import gate

    assert {firm_rules.UNUSABLE_CODE, firm_rules.REFUSED_CODE} <= gate.CODES
    src = (Path(__file__).resolve().parents[3] / "workspace_broker" / "delivery_proof.py").read_text()
    assert f'"{firm_rules.UNUSABLE_CODE}"' in src and f'"{firm_rules.REFUSED_CODE}"' in src


# ---- pins on the volume --------------------------------------------------------------------
def test_pins_survive_a_new_job_through_the_state_dir(tmp_path, spec_store, monkeypatch):
    monkeypatch.setenv(firm_rules.PINS_ENV, str(tmp_path / "pins"))
    (tmp_path / "pins").mkdir()
    _install(spec_store, "Leave extra space between the court name and the caption.")
    run = _Run(tmp_path / "one")
    run.firm.data["format"]["layout"]["mediation_brief"]["front_caption_gap_lines"] = 1
    assert firm_rules.start(run) == {"front_caption_gap_lines": 2}
    assert run.rule_notes and "we used 2" in run.rule_notes[0]
    later = _Run(tmp_path / "two")
    later.firm.data["format"]["layout"]["mediation_brief"]["front_caption_gap_lines"] = 2
    assert firm_rules.start(later) == {}  # no second step on the same ask
