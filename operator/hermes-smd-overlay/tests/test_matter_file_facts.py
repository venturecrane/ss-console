"""The facts of a records reply are code's, from the page finder (ADR 0102, 2026-11-02).

On an A&P records request the Operator's two replies retyped the finder's ranges
and counts: pp 48-49 were dropped and 57 became 59. These tests pin that the
block is rendered from the finder's result and the staged files, that the
model's own words cannot state a page or count the facts do not, that a seat
whose connector and overlay disagree on the result's version holds rather than
sending a partial block, and that every reply of a turn that searched a file's
pages waits for SMD while review is on, files or not."""

# ruff: noqa: F811 - the lane fixture is imported and used by name, as in test_matter_file_review.py

from __future__ import annotations

import json

import pytest

from shared import matter_file_facts as facts
from shared import matter_file_reply as mfr
from shared import matter_file_review as rev
from shared import output_checklist
from tests.test_matter_file_reply import (  # noqa: F401 - the fixtures and helpers the lane uses
    MATTER,
    STAFF,
    _clean,
    _email,
    _pdf,
    _result,
    _spooled,
    lane,
)


@pytest.fixture(autouse=True)
def _facts(tmp_path, monkeypatch):
    facts._reset_for_tests()
    monkeypatch.setenv(rev.REVIEW_DIR_ENV, str(tmp_path / "review"))
    monkeypatch.setenv(rev.DECISIONS_DIR_ENV, str(tmp_path / "decisions"))
    yield
    facts._reset_for_tests()


def _ptr(section: str, pages: str, heading: str, date: str) -> dict:
    return {"section": section, "pages": pages, "heading": heading, "date": date}


def _finding(*, version=2, file_id="f0", unreadable=0, not_reached=0) -> dict:
    """A find_pages_in_file result in the connector's shape (page_finder.locate)."""
    read = 223 - unreadable - not_reached
    out = {
        "matterId": MATTER,
        "fileId": file_id,
        "name": "Hospital records",
        "pageCount": 223,
        "counts": {"pages": 223, "read": read, "unreadable": unreadable, "notReached": not_reached},
        "complete": not (unreadable or not_reached),
        "items": [
            {
                "asked": "the 3/3/2021 clinic visit",
                "found": True,
                "established": True,
                "ranges": [
                    {
                        "pages": "1-4",
                        "evidence": [
                            {
                                "section": "S1",
                                "pages": "1-4",
                                "heading": "OFFICE VISIT",
                                "date": "3/3/2021",
                            }
                        ],
                    },
                    {
                        "pages": "48-49",
                        "evidence": [
                            {
                                "section": "S9",
                                "pages": "48-49",
                                "heading": "X-RAY REPORT",
                                "date": "3/3/2021",
                            }
                        ],
                    },
                ],
                "reason": "S1 is the visit; THE MODEL'S OWN SENTENCE",
                "closest": [],
                "relatedCandidates": [_ptr("S2", "5-14", "LAB RESULTS", "3/3/2021")],
            },
            {
                "asked": "the discharge summary",
                "found": False,
                "established": not (unreadable or not_reached),
                "ranges": [],
                "reason": "There is no discharge summary.",
                "closest": [_ptr("S7", "29-38", "PROGRESS NOTE", "6/30/2024")],
                "relatedCandidates": [],
            },
        ],
    }
    if version is not None:
        out["schemaVersion"] = version
    return out


def _staging(*files: tuple[str, str, int], refusal: str = "") -> mfr.Staging:
    return mfr.Staging(
        "m1",
        MATTER,
        [
            mfr.StagedFile(fid, f"{fid}.pdf", "a" * 64, 10, "b" * 32, pages, count)
            for fid, pages, count in files
        ],
        refusal,
    )


def _searched(result: dict, session: str = "s1", message: str = "m1") -> None:
    facts.record_from_result(session, {}, json.dumps(result), message_id=message)


# ---- the block --------------------------------------------------------------


def test_the_block_is_the_finders_facts_and_the_staged_files() -> None:
    _searched(_finding(unreadable=24, not_reached=57))
    block = facts.build("s1", "m1", _staging(("f0", "1-4", 4), ("f0", "48-49", 2)))
    assert block is not None and block.hold == ""
    text = block.text
    assert "Attached:\n- f0.pdf, pages 1-4 (4 pages): Office Visit, 3/3/2021" in text
    assert "- f0.pdf, pages 48-49 (2 pages): X-Ray Report, 3/3/2021" in text
    assert "Possibly relevant, not attached (candidates the finder named):" in text
    assert "- Hospital records, pages 5-14, Lab Results, 3/3/2021" in text
    assert "Not established, 24 pages of Hospital records could not be read and 57 pages" in text
    assert "not found on the 142 pages of Hospital records that were read" in text
    assert "Closest: pages 29-38, Progress Note, 6/30/2024" in text
    # The models' sentences never reach the reply.
    assert "OWN SENTENCE" not in text and "There is no" not in text
    # The block passes the staff checklist by itself (no capitals, no em dash).
    assert (
        output_checklist.refusing(output_checklist.check(text, output_checklist.STAFF_SEND)) == []
    )
    assert "<ul><li>f0.pdf, pages 1-4" in block.html


def test_an_established_negative_says_every_page_was_read() -> None:
    _searched(_finding())
    block = facts.build("s1", "m1", None)
    assert "Not found:\n- the 3/3/2021 clinic visit" not in block.text
    assert (
        "- the discharge summary: not found in Hospital records (all 223 pages were read)"
        in block.text
    )


def test_a_turn_that_neither_searched_nor_staged_gets_no_block() -> None:
    assert facts.build("s1", "m1", None) is None
    _searched(_finding(), message="m1")
    assert facts.build("s1", "m2", None) is None and not facts.searched("s1", "m2")


def test_a_staged_range_the_finder_never_reported_holds() -> None:
    _searched(_finding())
    block = facts.build("s1", "m1", _staging(("f0", "1-5", 5)))
    assert (
        block.text == ""
        and "1-5" in block.hold
        and "not a range the page finder reported" in block.hold
    )


def test_a_whole_file_or_a_file_the_finder_never_saw_is_not_range_checked() -> None:
    _searched(_finding())
    block = facts.build("s1", "m1", _staging(("f9", "3-11", 9), ("f0", "", 223)))
    assert block.hold == "" and "f9.pdf, pages 3-11 (9 pages)" in block.text
    assert "f0.pdf, the whole file (223 pages)" in block.text


# ---- version handshake: no partial block, either way ------------------------


@pytest.mark.parametrize(
    ("version", "pair"),
    [(None, "new overlay, old connector bundle"), (3, "old overlay, newer connector bundle")],
)
def test_a_result_of_another_schema_version_renders_nothing_and_holds(version, pair) -> None:
    _searched(_finding(version=version))
    block = facts.build("s1", "m1", _staging(("f0", "1-4", 4)))
    assert block.text == "" and block.html == "", pair
    assert "not the version this seat renders" in block.hold


def test_the_marker_holds_a_reply_on_an_overlay_that_renders_no_facts() -> None:
    """An overlay without this module sends the model's words as written; the
    skill's marker is capitals, so that overlay's checklist holds it."""
    found = output_checklist.check(f"Here you go.\n\n{facts.MARKER}\n", output_checklist.STAFF_SEND)
    assert any(v.rule == "caps_emphasis" for v in output_checklist.refusing(found))


# ---- the guard on the model's own words --------------------------------------


@pytest.mark.parametrize(
    ("words", "flagged"),
    [
        ("Attached are pp 48-59 of the set.", "p48-59"),
        ("The visit is on page 5.", "p5"),
        ("The set has 12 pages.", "12 page"),
        ("See pages 1-4 for the visit.", ""),
        ("The 3/3/2021 visit on matter 123456; call 602-555-0142.", ""),
    ],
)
def test_the_models_words_may_not_state_a_page_or_count_the_facts_do_not(words, flagged) -> None:
    _searched(_finding())
    block = facts.build("s1", "m1", _staging(("f0", "1-4", 4)))
    assert facts.unstated(block, words) == flagged


def test_a_page_in_the_match_models_asked_words_is_guarded_too() -> None:
    result = _finding()
    result["items"][1]["asked"] = "pages 300-310 of the discovery"
    _searched(result)
    block = facts.build("s1", "m1", None)
    assert facts.unstated(block, "Hello.") == "p300-310"


# ---- putting it in the body ---------------------------------------------------


def test_the_block_goes_at_the_marker_else_after_the_opening() -> None:
    block = facts.Facts("Attached:\n- A.pdf", "<p>Attached:</p>", "", frozenset())
    at = facts.insert({"body_text": f"Hi.\n\n{facts.MARKER}\n\nOne question."}, block)
    assert at["body_text"] == "Hi.\n\nAttached:\n- A.pdf\n\nOne question."
    after = facts.insert({"text": "Hi.\n\nOne question."}, block)
    assert after["text"] == "Hi.\n\nAttached:\n- A.pdf\n\nOne question."
    html = facts.insert({"text": "", "html": "<p>Hi.</p><p>Q?</p>"}, block)
    assert html["html"] == "<p>Hi.</p><p>Attached:</p><p>Q?</p>"
    empty = facts.insert({"text": f"Hi. {facts.MARKER}"}, facts.Facts("", "", "", frozenset()))
    assert facts.MARKER not in empty["text"]


# ---- the lane -----------------------------------------------------------------


def _review_first(mod, n: int = 10) -> None:
    mod._YAML_PATH.write_text(
        mod._YAML_PATH.read_text().replace(
            "matter_file_delivery:\n", f"matter_file_delivery:\n  review_first: {n}\n"
        )
    )


def _find_call(mod, result: dict, *, call="fp1", session="s1") -> None:
    assert (
        mod.on_transform_tool_result(
            tool_name=facts.FIND_TOOL,
            args={"matter_id": MATTER, "file_id": result.get("fileId"), "what": "x"},
            result=json.dumps(result),
            session_id=session,
            tool_call_id=call,
        )
        is None
    )
    mod.on_post_tool_call(
        tool_name=facts.FIND_TOOL,
        args={},
        result=json.dumps(result),
        session_id=session,
        tool_call_id=call,
    )


def _stage_pages(mod, spool, ranges: list[tuple[str, int]], *, session="s1") -> None:
    from shared import matter_binding

    rows = [
        {
            "fileId": "f0",
            "attachmentName": f"Records p{p}.pdf",
            "pages": p,
            **_spooled(spool, _pdf(n)),
        }
        for p, n in ranges
    ]
    matter_binding.membership_for(session).note_content_read(MATTER)
    mod.on_transform_tool_result(
        tool_name=mfr.STAGE_TOOL,
        args={"matter_id": MATTER, "files": []},
        result=_result(rows),
        session_id=session,
        tool_call_id="st1",
    )


def _reply(mod, body: str, call: str = "d1") -> str | None:
    return mod.on_transform_tool_result(
        tool_name="mcp_msgraph_mail_create_draft",
        args={"to": [STAFF], "subject": "Re: records", "body_text": body},
        result='{"id": "draft-1"}',
        session_id="s1",
        tool_call_id=call,
    )


def test_lane_a_searched_turn_with_no_files_still_waits_for_review(lane, monkeypatch) -> None:
    """The hole of 2026-11-02: review held only replies that carried files."""
    mod, d1, sent, _spool, _b = lane
    _review_first(mod)
    _email()
    _find_call(mod, _finding(unreadable=24, not_reached=57))
    notice = _reply(mod, f"Here is what we found.\n\n{facts.MARKER}\n\nWhich visit did you mean?")
    assert sent == [] and "matter_files_review" in notice
    (row,) = rev.rows()
    assert row["batches"] == [] and row["matter_id"] == MATTER
    assert "Not established, 24 pages of Hospital records could not be read" in row["text"]
    assert facts.MARKER not in row["text"] and "files:    none" in rev.describe(row)
    monkeypatch.setattr(rev, "decision", lambda r, **k: "approve")
    mod._review_once()
    assert len(sent) == 1 and "attachments" not in sent[0] and sent[0]["comment"] == row["text"]


def test_lane_without_review_a_searched_no_files_reply_sends_with_its_facts(lane) -> None:
    mod, _d1, sent, _spool, _b = lane
    _email()
    _find_call(mod, _finding())
    assert _reply(mod, "Here is what we found.\n\nWhich visit did you mean?") is None
    (out,) = sent
    assert "Not found:\n- the discharge summary: not found in Hospital records" in out["comment"]


def test_lane_a_files_reply_carries_the_code_written_facts(lane) -> None:
    mod, _d1, sent, spool, _b = lane
    _email()
    _find_call(mod, _finding())
    _stage_pages(mod, spool, [("1-4", 4), ("48-49", 2)])
    assert _reply(mod, "Here are the records you asked for.") is None
    (out,) = sent
    assert len(out["attachments"]) == 2
    assert "- Records p1-4.pdf, pages 1-4 (4 pages): Office Visit, 3/3/2021" in out["comment"]
    assert "- Records p48-49.pdf, pages 48-49 (2 pages)" in out["comment"]


def test_lane_the_models_own_page_numbers_send_the_reply_to_smd(lane) -> None:
    mod, d1, sent, spool, _b = lane
    _email()
    _find_call(mod, _finding())
    _stage_pages(mod, spool, [("1-4", 4)])
    notice = _reply(mod, "Attached are pp 1-4 and pp 48-59.")
    assert sent == [] and "matter_files_review" in notice
    (row,) = rev.rows()
    assert "p48-59" in row["reason"] and "held because" in rev.describe(row)
    held = [m for kind, m in d1.events() if kind == "REPLY_HELD"]
    assert "p48-59" in held[-1]["held_because"]


@pytest.mark.parametrize("version", [None, 3])
def test_lane_a_version_mismatch_holds_and_sends_no_partial_block(lane, version) -> None:
    mod, _d1, sent, spool, _b = lane
    _email()
    _find_call(mod, _finding(version=version))
    _stage_pages(mod, spool, [("1-4", 4)])
    notice = _reply(mod, "Here are the records.")
    assert sent == [] and "matter_files_review" in notice
    (row,) = rev.rows()
    assert "Attached:" not in row["text"] and "not the version" in row["reason"]


def test_lane_a_reply_that_never_searched_or_staged_is_untouched(lane) -> None:
    mod, _d1, sent, _spool, _b = lane
    _review_first(mod)
    _email()
    assert _reply(mod, "Thanks, on it.") is None
    assert sent[0]["comment"] == "Thanks, on it." and rev.rows() == []
