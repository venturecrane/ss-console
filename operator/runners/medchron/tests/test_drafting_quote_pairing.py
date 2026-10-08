"""A nested quotation written with double marks at both levels cannot throw the
gate's quote pairing off for the rest of the document; the job sets it in single
marks first; and the job continues in a fresh process after transcription.
Fictional text throughout."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from drafting_testkit import ScriptedClient, make_inputs, make_job, seat_with, standard_docs
from medchron_testkit import PRICING
from medchron.__main__ import _argv_without_redo
from medchron.drafting import nestquote
from medchron.drafting.run import DraftingRun

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"

NESTED = (
    'A clinician recorded that pain left him with "difficulty "even climbing the back stairs."" '
    "(Clinic Note, May 2, 2025). Later he testified: "
    '"I could not lift the boxes at work anymore after that day." (Doe Dep. 12:3-9).'
)


def _gate():
    spec = importlib.util.spec_from_file_location("dgc", CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_an_empty_double_mark_no_longer_shifts_every_later_quotation():
    quotes = [q.normalized for q in _gate().extract_quotes(NESTED)]
    assert "I could not lift the boxes at work anymore after that day." in quotes
    # The shifted reading paired a citation's closing paren with the next opener.
    assert not any(q.startswith("(") for q in quotes)


def test_the_old_whole_document_pattern_did_shift(tmp_path):
    # The falsifier: the pattern the gate used before reads the gap between two
    # quotations as a quotation. If this stops being true the test above proves less.
    old = [m.group(1) for m in re.finditer(r'"([^"]{1,700})"', NESTED)]
    assert any(q.lstrip().startswith("(") for q in old)


def test_an_odd_mark_stays_in_its_own_paragraph():
    text = 'He was 6\'2" tall and "said the light was red" here.\n\n"The other driver never braked at all," she said.'
    quotes = [q.normalized for q in _gate().extract_quotes(text)]
    assert "The other driver never braked at all," in quotes


def test_a_nested_quotation_is_set_in_single_marks():
    out, log = nestquote.repair(NESTED)
    assert "\"difficulty 'even climbing the back stairs.'\"" in out
    assert log and out.count('"') == NESTED.count('"') - 2
    # every word is unchanged
    assert out.replace("'", '"') == NESTED


def test_plain_quotations_are_left_alone_and_the_repair_is_idempotent():
    plain = 'She said "the light was red when he entered" (Dep. 4:2). "Nothing else," he said.'
    assert nestquote.repair(plain) == (plain, [])
    once, _ = nestquote.repair(NESTED)
    assert nestquote.repair(once) == (once, [])


def test_an_inner_quotation_in_single_marks_matches_a_record_written_with_double():
    g = _gate()
    record = 'pain often contributes to difficulty \n"even climbing the back stairs." \nNEXT SECTION'
    draft = "difficulty 'even climbing the back stairs.'"
    flat = lambda s: g.normalize(g.strip_markdown(s))  # noqa: E731
    assert flat(draft) in flat(record.replace("\n", " "))
    # a changed word still does not match
    assert flat("difficulty 'even getting up the back stairs.'") not in flat(record.replace("\n", " "))


def test_the_nested_draft_passes_the_quote_pairing_after_repair():
    fixed, _ = nestquote.repair(NESTED)
    quotes = [q.normalized for q in _gate().extract_quotes(fixed)]
    assert "difficulty 'even climbing the back stairs.'" in quotes
    assert "I could not lift the boxes at work anymore after that day." in quotes


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["medchron", "draft", "/j", "--redo", "gate,render"], ["medchron", "draft", "/j"]),
        (["medchron", "draft", "--redo=gate", "/j"], ["medchron", "draft", "/j"]),
        (["medchron", "draft", "/j"], ["medchron", "draft", "/j"]),
    ],
)
def test_a_fresh_process_never_reopens_finished_stages(argv, expected):
    assert _argv_without_redo(argv) == expected


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


def _job(tmp_path: Path, calls: list[str]):
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps(PRICING), encoding="utf-8")
    inputs = tmp_path / "inputs"
    if not inputs.is_dir():
        make_inputs(inputs)
    jd = tmp_path / "job"
    if not jd.is_dir():
        make_job(jd)
    return lambda seat, client: DraftingRun(
        jd,
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat,
        client=client,
        log=lambda m: None,
        readback_pause=0.0,
        reexec=lambda: calls.append("reexec"),
    )


def test_the_job_asks_for_a_fresh_process_once_after_transcription(tmp_path):
    calls: list[str] = []
    make = _job(tmp_path, calls)
    seat, client = seat_with(standard_docs()), ScriptedClient()
    v = make(seat, client).run()
    assert v.outcome == "delivered" and calls == ["reexec"]
    # A resumed run, transcription already done, does not ask again.
    v2 = make(seat, client).run()
    assert v2.outcome == "delivered" and calls == ["reexec"]
