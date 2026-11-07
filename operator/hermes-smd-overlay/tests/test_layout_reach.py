"""A layout rule covers the kinds the firm said, never every kind (shared/layout_reach.py).

Each proof can fail on the pre-change code: there, a rule that names no kind
compiled with ``doc_classes == []`` and the drafting lane applied it to every
kind. Names are made up.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from shared import layout_reach, layout_rules, rule_reach, wording_rules
from spec_applier.applier import SCHEMA_VERSION, SpecApplyOutcome, apply

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "layout_reach"
CASES = json.loads((FIXTURES / "cases.json").read_text("utf-8"))["adjustments"]
MAIN = json.loads((FIXTURES / "main_compiled.json").read_text("utf-8"))


def _seat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    path = tmp_path / "customer.yaml"
    path.write_text(
        "logic_in_code:\n  wording_rules: enforce\n" if on else "customer_id: x\n", "utf-8"
    )
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))


def _adj(text: str, **reach) -> dict:
    return {"id": "b0000001", "text": text, "at": "2026-12-01T00:00:00Z", **reach}


# ---- off: main's bytes --------------------------------------------------------------------
def test_off_every_case_compiles_exactly_as_main_did():
    assert layout_reach.compile_adjustments(CASES, reach_on=False) == MAIN


def test_off_the_applier_installs_mains_layout(tmp_path, monkeypatch):
    _seat(tmp_path, monkeypatch, on=False)
    spec_dir = _install(tmp_path, CASES)
    assert json.loads((spec_dir / "classes/work_product/layout.json").read_text()) == MAIN


def test_the_unchanged_compile_rule_still_reads_kinds_from_words():
    """No caller-supplied kinds: the function main shipped, unchanged."""
    for adj in CASES:
        got = layout_rules.compile_rule(adj["text"], adj["at"])
        (want,) = [r for r in MAIN["rules"] if r["rule_id"] == adj["id"]]
        assert got["doc_classes"] == want["doc_classes"]


# ---- on: the recorded kinds, and only them ------------------------------------------------
TEXT = "Two blank lines before the court name."


def test_on_a_rule_naming_one_kind_compiles_for_that_kind_only():
    (rule,) = layout_reach.compile_adjustments([_adj(TEXT, doc_kinds=["memo"])], True)["rules"]
    assert rule["doc_classes"] == ["memo"]
    assert rule["compiled"][0]["layout"] == {"front_court_gap_lines": 2}


def test_on_a_rule_naming_two_kinds_compiles_for_those_two():
    adj = _adj(TEXT, doc_kinds=["discovery_set", "memo"])
    (rule,) = layout_reach.compile_adjustments([adj], True)["rules"]
    assert rule["doc_classes"] == ["discovery_set", "memo"]


def test_on_a_rule_for_no_laid_out_kind_compiles_nothing_and_never_an_empty_list():
    adj = _adj(TEXT, doc_kinds=["demand_letter"])
    (rule,) = layout_reach.compile_adjustments([adj], True)["rules"]
    assert rule["compiled"] == []
    assert rule["doc_classes"] == ["demand_letter"]
    assert rule["prose"][0]["reason"] == layout_reach.NO_LAID_OUT_KIND


def test_on_an_old_rule_with_no_recorded_kinds_keeps_what_it_compiled_to():
    """Existing rules are not rewritten: no recorded kinds, main's compile."""
    adj = _adj(TEXT)
    on = layout_reach.compile_adjustments([adj], True)
    assert on == layout_rules.compile_adjustments([adj])
    assert on["rules"][0]["doc_classes"] == []


# ---- on: a rule naming no kind is sent back to ask, at proposal and at install ------------
def _format_subject(**over):
    return {"output_class": "work_product", "property": "format", **over}


def test_on_a_layout_rule_naming_no_kind_is_sent_back_with_the_ask():
    verdict = rule_reach.check(
        _format_subject(doc_kinds=["memo"]), "Double space the headings.", None
    )
    assert "names no kind of document" in verdict.refusal
    bare = rule_reach.check(_format_subject(), "Double space the headings.", None)
    assert "ask them which documents" in bare.refusal


def test_on_the_readback_states_the_kinds_exactly():
    own = "In briefs and memos, two blank lines before the court name."
    verdict = rule_reach.check(_format_subject(doc_kinds=["mediation_brief", "memo"]), own, None)
    assert verdict.refusal == ""
    readback = rule_reach.readback_with_reach(
        f"[rule b0000001] {own}", _format_subject(doc_kinds=list(verdict.kinds)), True
    )
    assert readback.endswith(
        "(This rule covers mediation briefs and memos, for any attorney, for any reader, "
        "and nothing else.)"
    )


def test_on_a_confirmed_rule_with_no_kinds_cannot_install(tmp_path, monkeypatch):
    _seat(tmp_path, monkeypatch, on=True)
    problems = wording_rules.reach_required("work_product", _adj(TEXT))
    assert problems and "which documents" in problems[0]
    assert wording_rules.reach_required("work_product", _adj(TEXT, doc_kinds=["memo"])) == []
    assert wording_rules.reach_required("staff", _adj(TEXT)) == []
    _seat(tmp_path, monkeypatch, on=False)
    assert wording_rules.reach_required("work_product", _adj(TEXT)) == []


@pytest.mark.parametrize(
    "on, kinds, refused", [(True, None, True), (True, ["memo"], False), (False, None, False)]
)
def test_the_intake_refuses_a_rule_that_records_no_kinds_only_when_on(
    tmp_path, monkeypatch, on, kinds, refused
):
    from establish_intake.intake import STATUS_REJECTED
    from tests.test_establish_intake_firm_adjust import _result, adjustment, build_run, make_intake

    _seat(tmp_path, monkeypatch, on=on)
    intake, _s3, spool = make_intake(tmp_path, classes=("work_product",))
    reach = {"doc_kinds": kinds} if kinds else {}
    build_run(spool, output_class="work_product", property="format", adjustment=adjustment(**reach))
    intake.process_run(spool / "runs" / "run-a1")
    result = _result(spool)
    assert (result["status"] == STATUS_REJECTED) is refused
    if refused:
        assert any("which documents" in r for r in result["reasons"])


# ---- the applier: recorded kinds installed, and a switch flip recompiles ------------------
def _install(tmp_path: Path, adjustments: list[dict]) -> Path:
    doc = {
        "schema_version": SCHEMA_VERSION,
        "classes": {
            "work_product": {
                "format": {
                    "adjustments": [
                        {"instructed_by": "admin@firm.example", **a} for a in adjustments
                    ]
                }
            }
        },
    }

    class S3:
        def get_object(self, **_k):
            return {"Body": io.BytesIO(json.dumps(doc).encode())}

    spec_dir = tmp_path / "specs"
    result = apply(s3_client=S3(), bucket="b", slug="s", spec_dir=spec_dir)
    assert result.outcome in (SpecApplyOutcome.APPLIED, SpecApplyOutcome.UNCHANGED)
    return spec_dir


def test_on_the_applier_installs_the_recorded_kinds(tmp_path, monkeypatch):
    _seat(tmp_path, monkeypatch, on=True)
    spec_dir = _install(tmp_path, [_adj(TEXT, doc_kinds=["memo"])])
    (rule,) = json.loads((spec_dir / "classes/work_product/layout.json").read_text())["rules"]
    assert rule["doc_classes"] == ["memo"]


def test_switching_on_recompiles_an_unchanged_vault(tmp_path, monkeypatch):
    _seat(tmp_path, monkeypatch, on=False)
    spec_dir = _install(tmp_path, [_adj(TEXT, doc_kinds=["memo"])])
    path = spec_dir / "classes/work_product/layout.json"
    assert json.loads(path.read_text())["rules"][0]["doc_classes"] == []
    _seat(tmp_path, monkeypatch, on=True)
    _install(tmp_path, [_adj(TEXT, doc_kinds=["memo"])])
    assert json.loads(path.read_text())["rules"][0]["doc_classes"] == ["memo"]


# ---- the report a person reads before a seat is switched on -------------------------------
def test_the_report_lists_installed_rules_whose_words_name_no_kind():
    texts = {a["id"]: a["text"] for a in CASES}
    report = layout_reach.unscoped_report(MAIN, texts)
    listed = {row["rule_id"]: row for row in report}
    # compiled, and their own words name no laid-out kind
    assert set(listed) == {"a0000004", "a0000005", "a0000007", "a0000009"}
    assert listed["a0000005"]["applies_to"] == list(layout_rules.DOC_CLASSES)
    assert all(row["text_known"] for row in report)


def test_the_report_lists_a_rule_whose_text_is_unknown():
    report = layout_reach.unscoped_report(MAIN, {})
    assert {row["rule_id"] for row in report} >= {"a0000002", "a0000003"}
    (row,) = [r for r in report if r["rule_id"] == "a0000002"]
    assert row == {"rule_id": "a0000002", "text_known": False, "applies_to": ["mediation_brief"]}


def test_the_report_writes_nothing(tmp_path):
    doc = json.loads(json.dumps(MAIN))
    layout_reach.unscoped_report(doc, {})
    assert doc == MAIN
