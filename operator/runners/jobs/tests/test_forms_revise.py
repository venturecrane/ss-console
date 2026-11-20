"""An attorney's change to a form set the Operator drafted: from its saved values,
code decides which sections change, unaffected sections stay byte for byte.
Every name, file and value here is invented."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from jobs.drafting.forms import answer_render, answer_stage, catalog, policy, revise_hook, revise_stage, saved

from test_forms_answer_stage import CORPUS, POLICY, Door, _v

CHECKED = ("2.2", "6.4")


def _delivered() -> tuple[Any, ...]:
    reply_2 = {
        "answers": [{"number": "2.2", "kind": "answered", "items": [_v("June 1, 1980", 4, "born on June 1, 1980")]}]
    }
    reply_6 = {
        "answers": [
            {
                "number": "6.4",
                "kind": "answered",
                "items": [
                    {
                        "name": "Example Clinic",
                        "parts": {"a": _v("Example Clinic, 100 Sample Way", 1, "Example Clinic, 100 Sample Way")},
                    }
                ],
            }
        ]
    }
    pol = policy.load_policy({policy.KEY: copy.deepcopy(POLICY)})
    inp = answer_stage.Inputs(CHECKED, "Pat Example", False, (), [], "")
    res = answer_stage.answer(inp, pol, CORPUS, Door(reply_2, reply_6), "model-x", catalog.load())
    rec = saved.unit_record(
        label="Responses to Form Interrogatories, Set One",
        draft_name="Responses.docx",
        form="DISC-001",
        revision="Rev. January 1, 2024",
        inputs=inp,
        answers=res.answers,
        stats={"paper_template": "Caption\n\n{{FORM RESPONSES}}\n"},
    )
    return json.loads(json.dumps(rec)), pol, inp


def _body(answers: Any, pol: Any, inp: Any) -> str:
    return answer_render.body(answers, CHECKED, pol, answer_stage.effective_form(pol, inp), catalog.load())


def _section_lines(md: str, number: str) -> list[str]:
    lines = md.split("\n\n")
    start = lines.index(f"**FORM INTERROGATORY NO. {number}:**")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("**FORM INTERROGATORY NO.")), len(lines))
    return lines[start:end]


def test_saved_values_round_trip() -> None:
    rec, _pol, _inp = _delivered()
    back = revise_stage.load_saved(rec)
    assert [saved.answer_to(a) for a in back] == rec["answers"]


def test_which_sections_change_is_code() -> None:
    cat = catalog.load()
    assert revise_stage.affected_sections("Put each subpart on its own line for every provider.", CHECKED, cat) == ()
    got = revise_stage.affected_sections("In 6.4, add Sample Imaging at 22 Test Road.", CHECKED, cat)
    assert [g.section for g in got] == ["6.0"]
    got = revise_stage.affected_sections("The birth date is wrong; it should be June 2.", CHECKED, cat)
    assert [g.section for g in got] == ["2.0", "6.0"]  # no number named: every section


def test_a_layout_only_change_re_renders_from_saved_values_with_no_call() -> None:
    rec, pol, inp = _delivered()
    door = Door()
    got = revise_stage.revise(
        revise_stage.load_saved(rec), "Please put each subpart on its own line.", inp, pol, door, "m", catalog.load()
    )
    assert door.calls == [] and got.calls == 0 and got.sections == ()
    assert _body(got.answers, pol, inp) == _body(revise_stage.load_saved(rec), pol, inp)


def test_add_a_provider_changes_only_that_number_and_marks_it_per_attorney() -> None:
    rec, pol, inp = _delivered()
    change = "For 6.4 please add Sample Imaging, 22 Test Road, where she had an MRI."
    reply = {
        "answers": [
            {
                "number": "6.4",
                "kind": "answered",
                "items": [
                    rec["answers"][1]["items"][0],
                    {
                        "name": "Sample Imaging",
                        "parts": {
                            "a": {
                                "text": "Sample Imaging, 22 Test Road",
                                "cites": [{"file": "ATTORNEY", "page": 1, "excerpt": "Sample Imaging, 22 Test Road"}],
                            },
                            "b": {
                                "text": "an MRI",
                                "cites": [{"file": "ATTORNEY", "page": 1, "excerpt": "she had an MRI"}],
                            },
                        },
                    },
                ],
            }
        ]
    }
    door = Door(reply)
    before = _body(revise_stage.load_saved(rec), pol, inp)
    got = revise_stage.revise(revise_stage.load_saved(rec), change, inp, pol, door, "m", catalog.load())
    after = _body(got.answers, pol, inp)
    assert len(door.calls) == 1 and "2.2 [" not in door.calls[0]["messages"][0]["content"]
    assert got.per_attorney == (("6.4", "a"), ("6.4", "b"))
    assert "Sample Imaging" in after and "(a) Sample Imaging, 22 Test Road" in after.split("\n\n")
    assert "(a) Sample Imaging, 22 Test Road {{CLIENT" not in after  # the attorney stated it
    assert _section_lines(after, "2.2") == _section_lines(before, "2.2")  # unaffected: byte for byte
    assert "| 6.4 | (a) | per attorney | |" in answer_render.cite_table("x", answer_stage.cites_of(got.answers))


def test_an_unsupported_changed_value_is_no_information_not_invented() -> None:
    rec, pol, inp = _delivered()
    reply = {
        "answers": [{"number": "2.2", "kind": "answered", "items": [_v("June 2, 1980", 4, "born on June 2, 1980")]}]
    }
    got = revise_stage.revise(
        revise_stage.load_saved(rec), "Fix 2.2.", inp, pol, Door(reply, reply), "m", catalog.load()
    )
    assert got.answers[0].kind == "no_information"


class _Job:
    request_text = "Please put each subpart on its own line."


class _Revise:
    def __init__(self, data: Path) -> None:
        self.data = data

    def _file_entry(self, role: str) -> dict[str, Any] | None:
        return {"name": "Responses.docx"} if role == "draft" else None


def test_a_hand_filed_draft_has_no_saved_values_and_revises_as_before(tmp_path: Path) -> None:
    run = _Revise(tmp_path / "data")
    run.data.mkdir()
    assert revise_hook.record_for(run) is None  # nothing copied: the revise job's own path runs
    assert saved.hand_to(tmp_path / "run", "01PRIOR", run.data) is False
    rec, _p, _i = _delivered()
    job_data = tmp_path / "prior" / "data" / "set-1"
    job_data.mkdir(parents=True)
    (job_data / saved.UNIT_FILE).write_text(json.dumps(rec))
    assert saved.keep(tmp_path / "run", "01PRIOR", tmp_path / "prior" / "data") is True
    assert saved.hand_to(tmp_path / "run", "01PRIOR", run.data) is True
    assert revise_hook.record_for(run)["draft_name"] == "Responses.docx"  # type: ignore[index]
