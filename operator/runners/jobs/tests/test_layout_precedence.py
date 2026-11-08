"""One order of precedence for a document's layout (design 2026-11-06, piece 5;
``jobs/drafting/layout_precedence``).

What a court rule requires first; then, between a rule the firm said and a
change the firm made in its own library Word file, the later one wins; a rule a
later edit changes is recorded as replaced by it, with the version and the
date; an edit the ledger cannot place against a rule leaves the rule standing
and pages SMD.

Two kinds of test. The order itself is decided by a pure function over
:class:`layout_precedence.Facts`, so each branch is pinned on Facts built by
hand. Then whole drafting jobs run on made-up inputs: the firm's accepted
marked file is the brief the brief-plan tests adopt, the firm's saves are
constructed (``test_library_edit.word_save`` and :func:`court_gap_save`), the
rules are compiled by the seat's own compiler and installed the way the spec
applier installs them. Every name in them is made up.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from dataclasses import replace
from pathlib import Path

import docx
import pytest

import library_testkit as lt
from operator_connector_sdk.library import MARKED, MemoryLibrary
from operator_connector_sdk.library.caller import record_problems
from jobs.drafting import layout_into_library as LIL, layout_precedence as LP, layout_props as P, library_pick
from test_drafting_brief_base import _job_brief, accepted, tmpl  # noqa: F401 - pytest fixtures used by name
from test_drafting_firm_rules import LR
from test_drafting_library_pick import _accept, _env, _filed, _run, laid, spec  # noqa: F401 - fixtures used by name
from test_library_edit import Daemon, _left, daemon, word_save  # noqa: F401 - the daemon fixture, used by name

CLS = "mediation_brief"
RULE = "0000000a"
SAID = "2026-11-01T09:00:00Z"
PLEAD = (0.5, 0.5, 0.69, 1.5)


# ---- the order, decided on facts -------------------------------------------------------------
def _facts(**over) -> LP.Facts:
    base = {
        "cls": CLS,
        "paper": "plain",
        "rules": {"front_court_gap_lines": (RULE, 3)},
        "rule_at": {RULE: SAID},
        "authored": {"front_court_gap_lines": 3, "front_attorney_gap_lines": 1},
        "authored_margins": None,
        "accepted_by": "firm_made",
        "accepted_at": "2026-11-05T10:00:00Z",
        "version": "acc-1-v3",
        "after": {"front_court_gap_lines": 4.0, "front_attorney_gap_lines": 1.0},
        "before": {"front_court_gap_lines": 2.0, "front_attorney_gap_lines": 1.0},
    }
    return LP.Facts(**{**base, **over})


def test_a_later_firm_edit_that_changed_what_a_rule_set_replaces_it_with_the_date():
    got = LP.decide(_facts())
    won = got.winners["front_court_gap_lines"]
    assert (won["winner"], won["version"], won["at"], won["replaces_rule"]) == (
        "firm_edit", "acc-1-v3", "2026-11-05T10:00:00Z", RULE)  # fmt: skip
    assert got.replaced == [
        {"rule_id": RULE, "key": "front_court_gap_lines", "by_version": "acc-1-v3", "at": "2026-11-05T10:00:00Z"}
    ]
    assert got.unplaced == []


def test_a_later_firm_edit_that_did_not_touch_it_leaves_the_rule_standing():
    got = LP.decide(_facts(before={"front_court_gap_lines": 4.0}))
    assert got.winners["front_court_gap_lines"]["winner"] == "firm_rule" and got.replaced == []


def test_a_rule_said_after_the_firms_edit_wins():
    got = LP.decide(_facts(rule_at={RULE: "2026-11-06T00:00:00Z"}))
    assert got.winners["front_court_gap_lines"] == {
        "winner": "firm_rule", "rule_id": RULE, "at": "2026-11-06T00:00:00Z", "value": 3, "in_file": False}  # fmt: skip
    assert got.replaced == [] and got.unplaced == []


def test_a_file_no_one_at_the_firm_edited_never_beats_a_rule_or_the_authored_layout():
    got = LP.decide(_facts(accepted_by="admin@firm.example", authored={"front_attorney_gap_lines": 2}))
    assert got.winners["front_court_gap_lines"]["winner"] == "firm_rule"
    assert got.winners["front_attorney_gap_lines"] == {"winner": "authored", "value": 2}


def test_a_later_job_places_the_edit_by_the_rules_own_version_in_the_ledger():
    chain = (("admin@firm.example", "2026-10-01T00:00:00Z"), (f"layout_rule:{RULE}", "2026-11-02T00:00:00Z"))
    got = LP.decide(_facts(before=None, chain=chain))
    assert got.winners["front_court_gap_lines"]["winner"] == "firm_edit" and len(got.replaced) == 1


def test_an_edit_the_ledger_cannot_place_leaves_the_rule_standing_and_says_so():
    got = LP.decide(_facts(before=None))
    assert got.winners["front_court_gap_lines"]["winner"] == "firm_rule" and got.replaced == []
    assert got.unplaced == [
        {
            "rule_id": RULE,
            "key": "front_court_gap_lines",
            "why": "the ledger cannot say whether the firm's edit changed it",
        }
    ]


def test_a_rule_whose_date_cannot_be_read_stands_and_is_unplaced():
    for rule_at in (None, {RULE: "the fifth"}):
        got = LP.decide(_facts(rule_at=rule_at))
        assert got.winners["front_court_gap_lines"]["winner"] == "firm_rule"
        assert [u["why"] for u in got.unplaced] == ["the rule's date cannot be read"]


def test_the_courts_line_pitch_comes_first_on_pleading_paper():
    got = LP.decide(_facts(paper="pleading", rules={"line_spacing_pt": (RULE, 24.0)}, after={"line_spacing_pt": 12.0},
                           authored={"heading_line_spacing_pt": 24.0}))  # fmt: skip
    assert got.winners["line_spacing_pt"]["winner"] == "court_rule"
    assert got.winners["heading_line_spacing_pt"]["winner"] == "court_rule"
    assert got.replaced == [] and got.unplaced == []


def test_a_firms_margin_wins_within_the_courts_floors_and_never_under_them():
    edited = (0.5, 0.5, 0.69, 1.62)
    got = LP.decide(_facts(paper="pleading", authored_margins=PLEAD, after={P.MARGINS: edited}))
    assert got.winners[P.MARGINS]["winner"] == "firm_edit"
    under = LP.decide(_facts(paper="pleading", authored_margins=PLEAD, after={P.MARGINS: (0.5, 0.5, 0.69, 0.5)}))
    assert under.winners[P.MARGINS]["winner"] == "court_rule"
    assert under.winners[P.MARGINS]["court"] == "California Rules of Court, rule 2.107"


def test_a_value_that_cannot_be_read_is_never_a_difference():
    got = LP.decide(_facts(after={}, before={}))
    assert got.winners["front_court_gap_lines"]["winner"] == "firm_rule"
    assert got.winners["front_attorney_gap_lines"]["winner"] == "authored"


def test_apply_takes_what_the_edit_won_out_of_the_layout_the_check_reads_and_the_rules_laid():
    fmt = {
        "layout": {CLS: {"front_court_gap_lines": 3, "front_attorney_gap_lines": 1}},
        "pleading": {"margins_in": PLEAD},
    }
    prec = LP.decide(_facts(paper="pleading", authored_margins=PLEAD, after={
        "front_court_gap_lines": 4.0, "front_attorney_gap_lines": 1.0, P.MARGINS: (0.5, 0.5, 0.69, 1.62)}))  # fmt: skip
    out, rules = LP.apply(fmt, CLS, {"front_court_gap_lines": 3}, prec)
    assert out["layout"][CLS] == {"front_attorney_gap_lines": 1} and rules == {}
    assert out["pleading"]["margins_in"] == [0.5, 0.5, 0.69, 1.62]
    assert fmt["layout"][CLS]["front_court_gap_lines"] == 3  # the firm's inputs are not changed
    assert LP.apply(fmt, CLS, {"a": 1}, None) == (fmt, {"a": 1})


# ---- whole jobs ------------------------------------------------------------------------------
def _install_rule(spec: Path, text: str, at: str = SAID) -> None:
    """One confirmed rule, as the seat's spec applier installs it: ``layout.json``,
    the spec file it is compiled from, both in the manifest beside the ledger."""
    doc = LR.compile_adjustments([{"id": RULE, "text": text, "at": at}])
    layout = json.dumps(doc, sort_keys=True).encode()
    md = f"Body.\n\n## Adjustments\n\n- [rule {RULE}] (instructed by Alpha Example, {at}): {text}\n".encode()
    for rel, blob in (("classes/work_product/layout.json", layout), ("classes/work_product/format.md", md)):
        (spec / rel).parent.mkdir(parents=True, exist_ok=True)
        (spec / rel).write_bytes(blob)
    manifest = json.loads((spec / "manifest.json").read_text()) if (spec / "manifest.json").exists() else {}
    manifest["layouts"] = {"classes/work_product/layout.json": {"sha256": hashlib.sha256(layout).hexdigest()}}
    manifest["specs"] = {"classes/work_product/format.md": {"sha256": hashlib.sha256(md).hexdigest()}}
    (spec / "manifest.json").write_text(json.dumps(manifest))


_BLANK = b'<w:p><w:pPr><w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/></w:pPr></w:p>'


def court_gap_save(blob: bytes, more: int = 2) -> bytes:
    """The firm's save, constructed: ``more`` blank lines added before the court's
    name in its library file, and the saver's marks (``word_save``)."""
    saved = word_save(blob, left=None)
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(saved)) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "word/document.xml":
                at = data.rfind(_BLANK, 0, data.find(b"[[court]]"))
                data = data[:at] + _BLANK * more + data[at:]
            out.writestr(info, data)
    return buf.getvalue()


def _court_gap(blob: bytes) -> int:
    texts = [p.text for p in docx.Document(io.BytesIO(blob)).paragraphs]
    c = next(i for i, t in enumerate(texts) if t.startswith("SUPERIOR COURT"))
    return next(n for n in range(c) if texts[c - n - 1].strip()) if c else 0


def _prec(r) -> dict:
    rec = lt.record(r.data)
    assert record_problems(rec) == []
    return rec["layout_precedence"]


def test_with_nothing_accepted_the_record_carries_no_order(spec, tmp_path):
    _install_rule(spec, "Three blank lines before the court name.")
    r, v, _ = _run(tmp_path / "job")
    assert v.outcome == "delivered" and "layout_precedence" not in lt.record(r.data)


def test_a_firms_later_edit_replaces_the_rule_it_changed_and_the_draft_is_the_edit(spec, tmp_path, monkeypatch, tmpl):
    lib = _accept(monkeypatch, spec, tmpl.bytes, contract=MARKED)
    _install_rule(spec, "Three blank lines before the court name.")
    new = lib.save("acc-1", court_gap_save(tmpl.bytes))
    r, v, log = _run(tmp_path / "job", base=tmpl.bytes, draft=_job_brief())
    assert v.outcome == "delivered", (v.stage, v.reason, log[-5:])
    prec = _prec(r)
    assert prec["replaced"] == [{"rule_id": RULE, "key": "front_court_gap_lines", "by_version": new,
                                 "at": prec["winners"]["front_court_gap_lines"]["at"]}]  # fmt: skip
    assert _court_gap(_filed(r)) == 4


def test_a_firms_later_edit_of_something_else_leaves_the_rule_in_the_draft(spec, tmp_path, monkeypatch, tmpl):
    lib = _accept(monkeypatch, spec, tmpl.bytes, contract=MARKED)
    _install_rule(spec, "Three blank lines before the court name.")
    lib.save("acc-1", word_save(tmpl.bytes, left="2333"))
    r, v, log = _run(tmp_path / "job", base=tmpl.bytes, draft=_job_brief())
    assert v.outcome == "delivered", (v.stage, v.reason, log[-5:])
    prec = _prec(r)
    assert prec["winners"]["front_court_gap_lines"]["winner"] == "firm_rule" and prec["replaced"] == []
    assert prec["winners"]["page_margins"]["winner"] == "firm_edit"
    assert _court_gap(_filed(r)) == 3 and _left(_filed(r)) == "2333"


def _installed_firm_save(spec: Path, saved: bytes, earlier: tuple[bytes, str, str] | None = None) -> MemoryLibrary:
    """The ledger after the root writer installed the firm's save (and, before it,
    the version ``earlier`` names: its bytes, who, when), with no newer version
    in the library."""
    lib = MemoryLibrary()
    entries = []
    if earlier is not None:
        blob, who, at = earlier
        first = lt.entry(MemoryLibrary(), "acc-1", CLS, blob, contract=MARKED)
        entries.append(replace(first, version="acc-1-v0", accepted_by=who, accepted_at=at))
    last = lt.entry(lib, "acc-1", CLS, saved, contract=MARKED)
    lt.install(spec, *entries, replace(last, accepted_by="firm_made", accepted_at="2026-11-05T10:00:00Z"))
    return lib


def _job_of(daemon: Daemon, r) -> str:
    daemon.root = r.data.parent.parent
    return r.data.parent.name


def test_after_the_save_is_installed_the_next_draft_keeps_the_firms_margin(spec, tmp_path, monkeypatch, tmpl):
    lib = _installed_firm_save(spec, word_save(tmpl.bytes, left="2333"))
    monkeypatch.setattr(library_pick, "open_store", lambda _seat: lib)
    r, v, log = _run(tmp_path / "job", base=tmpl.bytes, draft=_job_brief())
    assert v.outcome == "delivered", (v.stage, v.reason, log[-5:])
    assert _prec(r)["winners"]["page_margins"]["winner"] == "firm_edit" and _left(_filed(r)) == "2333"


def test_a_later_job_that_cannot_place_the_edit_keeps_the_rule_and_pages_smd(spec, tmp_path, monkeypatch, tmpl, daemon):
    lib = _installed_firm_save(spec, court_gap_save(tmpl.bytes))
    monkeypatch.setattr(library_pick, "open_store", lambda _seat: lib)
    _install_rule(spec, "Three blank lines before the court name.")
    r, v, log = _run(tmp_path / "job", base=tmpl.bytes, draft=_job_brief())
    assert v.outcome == "delivered", (v.stage, v.reason, log[-5:])
    prec = _prec(r)
    assert prec["winners"]["front_court_gap_lines"]["winner"] == "firm_rule"
    assert [u["rule_id"] for u in prec["unplaced"]] == [RULE] and _court_gap(_filed(r)) == 3
    job_id = _job_of(daemon, r)
    LIL.alert_unplaced(daemon, job_id)
    assert daemon.paged == [(job_id, LIL.UNPLACED, f"the rule stood: {RULE}:front_court_gap_lines")]


def test_a_later_job_places_the_edit_after_the_rules_own_version(spec, tmp_path, monkeypatch, tmpl):
    lib = _installed_firm_save(
        spec, court_gap_save(tmpl.bytes), (tmpl.bytes, f"layout_rule:{RULE}", "2026-11-02T00:00:00Z")
    )
    monkeypatch.setattr(library_pick, "open_store", lambda _seat: lib)
    _install_rule(spec, "Three blank lines before the court name.")
    r, v, log = _run(tmp_path / "job", base=tmpl.bytes, draft=_job_brief())
    assert v.outcome == "delivered", (v.stage, v.reason, log[-5:])
    assert [x["rule_id"] for x in _prec(r)["replaced"]] == [RULE] and _court_gap(_filed(r)) == 4


def test_a_margin_under_the_courts_floor_is_never_used_and_the_job_stops(spec, tmp_path, monkeypatch, tmpl):
    lib = _accept(monkeypatch, spec, tmpl.bytes, contract=MARKED)
    lib.save("acc-1", word_save(tmpl.bytes, left="720"))
    r, v, _ = _run(tmp_path / "job", base=tmpl.bytes, draft=_job_brief())
    assert _prec(r)["winners"]["page_margins"]["winner"] == "court_rule"
    assert v.outcome == "failed" and "margins" in v.reason


# ---- a rule applied into the library file ----------------------------------------------------
def _switch(tmp_path: Path, monkeypatch, mode: str) -> None:
    cfg = tmp_path / "customer.yaml"
    cfg.write_text(f"logic_in_code:\n  layout_into_library: {mode}\n")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(cfg))


def test_observe_records_what_a_rule_would_write_into_the_file_and_writes_nothing(spec, tmp_path, monkeypatch, tmpl):
    lib = _accept(monkeypatch, spec, tmpl.bytes, contract=MARKED)
    _install_rule(spec, "Three blank lines before the court name.")
    r, v, log = _run(tmp_path / "job", base=tmpl.bytes, draft=_job_brief())
    assert v.outcome == "delivered", (v.stage, v.reason, log[-5:])
    into = _prec(r)["into_library"]
    assert into["mode"] == "observe" and into["written"] is False and into["reason"] == "observe"
    assert into["would_apply"] == [{"rule_id": RULE, "key": "front_court_gap_lines", "value": 3}]
    assert len(lib.list_versions("acc-1").versions) == 1 and not (r.data / LIL.RULE_JSON).exists()
    assert _court_gap(_filed(r)) == 3  # the rule is laid onto the draft as before


def test_enforce_files_the_rule_as_a_new_version_and_hands_it_to_the_root_writer(
    spec, tmp_path, monkeypatch, tmpl, daemon
):
    _switch(tmp_path, monkeypatch, "enforce")
    lib = _accept(monkeypatch, spec, tmpl.bytes, contract=MARKED)
    _install_rule(spec, "Three blank lines before the court name.")
    r, v, log = _run(tmp_path / "job", base=tmpl.bytes, draft=_job_brief())
    assert v.outcome == "delivered", (v.stage, v.reason, log[-5:])
    into = _prec(r)["into_library"]
    assert into["written"] is True and into["reason"] == ""
    versions = lib.list_versions("acc-1").versions
    assert len(versions) == 2 and into["version"] == versions[0].version  # newest first
    left = json.loads((r.data / LIL.RULE_JSON).read_text())["acceptance"]
    kept = (r.data / LIL.RULE_DOCX).read_bytes()
    assert (left["accepted_by"], left["version"], left["sha256"]) == (f"layout_rule:{RULE}", into["version"], hashlib.sha256(kept).hexdigest())  # fmt: skip
    assert (
        left["replaces"]["version"] == "acc-1-v1"
        and P.read(kept, MARKED, CLS, "pleading")["front_court_gap_lines"] == 3
    )
    job_id = _job_of(daemon, r)
    daemon.answers = {"library_edit_submit": [{"run_id": "r1"}],
                      "library_edit_status": [{"status": "complete", "result": {"status": "installed"}}]}  # fmt: skip
    assert LIL.hand_over(daemon, job_id) == "installed"
    assert daemon.sent[0]["acceptance"]["accepted_by"] == f"layout_rule:{RULE}" and daemon.paged == []


def test_enforce_writes_nothing_while_the_job_is_accepting_the_firms_own_save(spec, tmp_path, monkeypatch, tmpl):
    _switch(tmp_path, monkeypatch, "enforce")
    lib = _accept(monkeypatch, spec, tmpl.bytes, contract=MARKED)
    _install_rule(spec, "Three blank lines before the court name.")
    lib.save("acc-1", word_save(tmpl.bytes, left="2333"))
    r, v, _ = _run(tmp_path / "job", base=tmpl.bytes, draft=_job_brief())
    into = _prec(r)["into_library"]
    assert into["written"] is False and "accepting the firm's own save" in into["reason"]
    assert len(lib.list_versions("acc-1").versions) == 2


def test_an_unreadable_switch_writes_nothing(spec, tmp_path, monkeypatch, tmpl):
    _switch(tmp_path, monkeypatch, "sometimes")
    lib = _accept(monkeypatch, spec, tmpl.bytes, contract=MARKED)
    _install_rule(spec, "Three blank lines before the court name.")
    r, _v, _ = _run(tmp_path / "job", base=tmpl.bytes, draft=_job_brief())
    assert _prec(r)["into_library"]["written"] is False and len(lib.list_versions("acc-1").versions) == 1


# ---- when a rule was said --------------------------------------------------------------------
def test_a_rules_date_is_read_only_from_a_spec_file_its_manifest_vouches_for(spec):
    from jobs.drafting import rule_times

    _install_rule(spec, "Three blank lines before the court name.")
    assert rule_times.read(spec) == {RULE: SAID}
    (spec / rule_times.SPEC_REL).write_text("- [rule 0000000a] (instructed by X, 2027-01-01T00:00:00Z): torn\n")
    assert rule_times.read(spec) is None
    assert rule_times.read(spec / "nowhere") is None


@pytest.mark.parametrize("who", ["instructed by A, applied by B", "instructed by A (of counsel)"])
def test_a_rules_date_is_read_past_any_name(spec, who):
    from jobs.drafting import rule_times

    md = f"- [rule {RULE}] ({who}, {SAID}): A rule, with (parens): and more.\n".encode()
    (spec / rule_times.SPEC_REL).parent.mkdir(parents=True, exist_ok=True)
    (spec / rule_times.SPEC_REL).write_bytes(md)
    (spec / "manifest.json").write_text(
        json.dumps({"specs": {rule_times.SPEC_REL: {"sha256": hashlib.sha256(md).hexdigest()}}})
    )
    assert rule_times.read(spec) == {RULE: SAID}
    assert not re.search(r"[A-Z][a-z]", json.dumps(rule_times.read(spec)).replace(RULE, ""))


# ---- the rehearsal on a seat's installed rules -----------------------------------------------
def _tool():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "tools" / "library_setup_rehearse.py"
    spec_ = importlib.util.spec_from_file_location("rehearse_tool_layout", path)
    mod = importlib.util.module_from_spec(spec_)
    assert spec_.loader is not None
    spec_.loader.exec_module(mod)
    return mod


def _rehearse(tmp_path: Path, tmpl_bytes: bytes, capsys, edited: bytes | None = None) -> dict:
    from drafting_testkit import make_inputs
    from test_drafting_brief_base import _with_base

    inputs = make_inputs(tmp_path / "inputs")
    _with_base(inputs, tmpl_bytes)
    doc = LR.compile_adjustments([{"id": RULE, "text": "Three blank lines before the court name.", "at": SAID}])
    files = {"layout.json": json.dumps(doc).encode(), "accepted.docx": tmpl_bytes, "edited.docx": edited or b""}
    for name, blob in files.items():
        (tmp_path / name).write_bytes(blob)
    argv = ["--kind", CLS, "--layout", str(tmp_path / "layout.json"), "--accepted", str(tmp_path / "accepted.docx"),
            "--inputs", str(inputs)] + (["--edited", str(tmp_path / "edited.docx")] if edited else [])  # fmt: skip
    assert _tool().main(argv) == 0
    out = capsys.readouterr().out
    assert "Alpha" not in out and "GAMMA" not in out and "Example" not in out
    return json.loads(out)["layout"]


def test_the_rehearsal_names_each_winner_and_whether_the_check_holds_it(tmp_path, tmpl, capsys):
    got = _rehearse(tmp_path, tmpl.bytes, capsys)
    assert got["properties"]["front_court_gap_lines"] == {"winner": "firm_rule", "format_check": "pass"}
    assert got["would_apply_into_file"] == [f"{RULE}:front_court_gap_lines"] and got["replaced"] == []
    assert got["all_rules_together"] == "pass" and got["rules"] == 1


def test_the_rehearsal_on_a_firms_later_save_shows_the_rule_replaced(tmp_path, tmpl, capsys):
    got = _rehearse(tmp_path, tmpl.bytes, capsys, edited=court_gap_save(word_save(tmpl.bytes, left="2333")))
    assert got["properties"]["front_court_gap_lines"]["winner"] == "firm_edit"
    assert got["replaced"] == [f"{RULE}:front_court_gap_lines"]
    assert got["properties"]["page_margins"] == {"winner": "firm_edit", "format_check": "pass"}


def test_the_rehearsals_check_can_fail_a_margin_under_the_courts_floor(tmp_path, tmpl, capsys):
    got = _rehearse(tmp_path, tmpl.bytes, capsys, edited=word_save(tmpl.bytes, left="720"))
    assert got["properties"]["page_margins"] == {"winner": "court_rule", "format_check": "fail"}
