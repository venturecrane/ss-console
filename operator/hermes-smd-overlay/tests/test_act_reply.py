"""Code writes the reply to a request for work (``shared.turn_facts``,
``plugins/hermes-smd-reply/act_reply.py``, 2026-11-24).

THE CAUSE. A replay of every request the firm had sent found reply failures the
largest cause: 41 of 160, 32 in one week (17 asked the attorney a question, 17
stated a product limit, 11 contradicted the thread, 8 gave a false status, 6
said "paste it yourself", 3 sent nothing). On each, the tool results held the
truth and the model's words did not.

Each test below replays one of those shapes with invented values, through the
reply plugin's own hooks: the tool calls reach ``on_post_tool_call`` with
results shaped as the connector returns them, then the model's draft reaches the
relay, and what the transport is handed is read back.

FALSIFIER: on the parent commit there is no ``turn_facts`` and no step 5c, so
the model's draft is what the transport gets, and every act-turn test fails on
its first assertion.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from shared import inbound, smd_owed, turn_facts
from tests.conftest import load_plugin
from tests.test_reply_binding import _SEAT_YAML, ADMIN, _FakeD1

#: The session the current email opened (each email in a test gets its own turn).
_TURN = {"session": "mail-turn-act"}
GRAPH_ID = "AAMkACT1="
IMID = "<CAact1@mail.firm.example>"
THREAD = "conv-act-1"
MATTER = "5b2c-matter"
LOOK = turn_facts.LOOK_INTO


class _Mail:
    def __init__(self) -> None:
        self.sends: list[dict] = []

    def send_reply(self, message_id, comment, **kw):
        self.sends.append(
            {"message_id": message_id, "comment": comment, "html": kw.get("html", "")}
        )
        return "<sent@firm.example>"


class _Owed:
    def __init__(self) -> None:
        self.records: list[tuple] = []
        self.open: list[dict] = []
        self.ok = True

    def record(self, kind, ref, detail="", send=None):
        self.records.append((kind, ref, detail))
        if self.ok:
            self.open.append({"request_ref": ref, "kind": kind})
        return self.ok

    def open_page(self, send=None):
        return {"entries": list(self.open), "owed_jobs": [], "cursor": None}


@pytest.fixture
def seat(monkeypatch, tmp_path):
    mod = load_plugin("hermes-smd-reply")
    d1, mail, owed = _FakeD1(), _Mail(), _Owed()
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(_SEAT_YAML.format(adapter="msgraph"))
    monkeypatch.setattr(mod, "_INFRA_READY", True, raising=False)
    monkeypatch.setattr(mod, "_CUSTOMER_SLUG", "acme", raising=False)
    monkeypatch.setattr(mod, "_D1_CLIENT", d1, raising=False)
    monkeypatch.setattr(mod, "_LIMITER", mod.relay.RateLimiter(), raising=False)
    monkeypatch.setattr(mod, "_REPLIED", mod.relay.RepliedOnce(), raising=False)
    monkeypatch.setattr(mod, "_DECIDED", mod.relay.DecidedOnce(), raising=False)
    monkeypatch.setattr(mod, "_YAML_PATH", yaml_path, raising=False)
    monkeypatch.setattr(
        mod, "_HELD_STORE", mod.held_store.HeldReplyStore(str(tmp_path / "held.db")), raising=False
    )
    monkeypatch.setattr(mod.msgraph_broker, "send_reply", mail.send_reply)
    monkeypatch.setattr(smd_owed, "record", owed.record)
    monkeypatch.setattr(smd_owed, "open_page", owed.open_page)
    # Checklist and advertising rules are their own tests; here the body is the subject.
    monkeypatch.setattr(mod, "_checklist_violations", lambda *a, **k: None)
    mod.relay_checks.act_reply.LOOK_INTOS.clear()
    for reg in (
        inbound.SESSION_INBOUND_ORIGIN._origins,
        inbound.SESSION_INBOUND_ORIGIN._by_address,
        inbound.SESSION_INBOUND_ORIGIN._by_message,
    ):
        reg.clear()
    yield mod, d1, mail, owed
    mod.relay_checks.act_reply.LOOK_INTOS.clear()


def _email(
    mod,
    words: str = "Please file it.",
    message_id: str = GRAPH_ID,
    imid: str = IMID,
    session: str = "",
) -> None:
    _TURN["session"] = session or "mail-turn-act"
    inbound.SESSION_INBOUND_ORIGIN.record(
        _TURN["session"],
        inbound.InboundOrigin(
            sender_address=ADMIN,
            message_id=message_id,
            inbox_id="op@x",
            internet_message_id=imid,
            conversation_id=THREAD,
            reply_text=words,
        ),
    )
    inbound.SESSION_INBOUND_ORIGIN.note_turn_prompt(_TURN["session"], message_id)
    mod.on_pre_llm_call(session_id=_TURN["session"], sender_id=ADMIN, user_message=words)


def _tool(mod, tool: str, args: dict, result: dict | str, **extra: Any) -> None:
    mod.on_post_tool_call(
        tool_name=tool,
        args=args,
        result=result if isinstance(result, str) else json.dumps(result),
        session_id=_TURN["session"],
        tool_call_id=f"t-{tool}-{len(json.dumps(args))}",
        **extra,
    )


def _draft(mod, body: str, call: str = "d1") -> None:
    mod.on_post_tool_call(
        tool_name="mcp_msgraph_mail_create_draft",
        args={"to": [ADMIN], "subject": "Re: work", "body_text": body},
        session_id=_TURN["session"],
        tool_call_id=call,
    )


def _held(d1) -> list[str]:
    return [m["reason"] for a, m in d1.events() if a == "REPLY_HELD"]


def _filed(name: str) -> dict:
    return {"matterId": MATTER, "fileName": name, "fileId": f"f-{name}", "readback": "confirmed"}


def _render(mod, name: str, result: dict) -> None:
    _tool(mod, "mcp_smokeball_render_docx_draft", {"matter_id": MATTER, "file_name": name}, result)


OUT_OF_TIME = {
    "status": "refused",
    "reason": "out_of_time",
    "fileId": None,
    "matterId": MATTER,
    "fileName": "Responses to RFP Set One.docx",
    "outOfTime": "rendering",
}


# -- the act shapes ---------------------------------------------------------


def test_a_failed_render_then_paste_it_yourself_sends_the_look_into_line(seat) -> None:
    mod, d1, mail, owed = seat
    _email(mod, "Prepare responses to the RFPs and file the Word version.")
    _render(mod, "Responses to RFP Set One", OUT_OF_TIME)
    _render(mod, "Responses to RFP Set One", OUT_OF_TIME)
    _draft(mod, "The Word version timed out twice. Please paste the text into your template.")
    assert [s["comment"] for s in mail.sends] == [LOOK]
    assert owed.records == [("look_into", IMID, "mcp_smokeball_render_docx_draft out_of_time")]
    assert "paste" not in mail.sends[0]["html"]


def test_not_filed_said_of_a_file_that_is_filed_sends_the_filed_line(seat) -> None:
    mod, d1, mail, owed = seat
    _email(mod)
    _tool(
        mod,
        "mcp_smokeball_add_file",
        {"matter_id": MATTER, "file_name": "Settlement Statement.docx"},
        _filed("Settlement Statement.docx"),
    )
    _draft(mod, "The settlement statement was not filed; the upload did not go through.")
    assert [s["comment"] for s in mail.sends] == [
        "We filed Settlement Statement.docx in the matter."
    ]
    assert owed.records == []


def test_a_question_as_the_closing_is_never_sent_on_an_act_turn(seat) -> None:
    mod, d1, mail, owed = seat
    _email(mod)
    _tool(
        mod,
        "mcp_smokeball_create_folder",
        {"matter_id": MATTER, "name": "Discovery"},
        {"readback": "confirmed", "id": "fold-1"},
    )
    _draft(mod, "I created the folder. Would you like me to move the documents into it?")
    assert [s["comment"] for s in mail.sends] == ["We created the Discovery folder."]


def test_a_product_limit_sentence_is_never_sent(seat) -> None:
    mod, d1, mail, owed = seat
    _email(mod, "Put the six documents in the new folder.")
    _tool(
        mod,
        "mcp_smokeball_move_files_to_folder",
        {"matter_id": MATTER, "folder_id": "fold-1", "file_ids": ["a"]},
        {"status": "refused", "reason": "folder_not_on_matter"},
    )
    _draft(
        mod,
        "My connection cannot move documents between folders. This is a limit of how I work in Smokeball.",
    )
    assert [s["comment"] for s in mail.sends] == [LOOK]


def test_a_mixed_turn_reports_five_copies_and_looks_into_the_sixth(seat) -> None:
    mod, d1, mail, owed = seat
    _email(mod, "Copy the six exhibits into Trial Exhibits.")
    rows = [
        {"fileId": f"x{i}", "name": f"Exhibit {i}.pdf", "result": "copied", "readback": "confirmed"}
        for i in range(1, 6)
    ] + [
        {
            "fileId": "x6",
            "name": "Exhibit 6.pdf",
            "result": "over_copy_limit",
            "status": "readback_mismatch",
        }
    ]
    _tool(
        mod,
        "mcp_smokeball_copy_files_to_folder",
        {"matter_id": MATTER, "folder_id": "fold-9"},
        {
            "action": "copy",
            "matterId": MATTER,
            "folderId": "fold-9",
            "folderName": "Trial Exhibits",
            "files": rows,
            "status": "readback_mismatch",
        },
    )
    _draft(mod, "I could not copy any of the exhibits.")
    (sent,) = [s["comment"] for s in mail.sends]
    lines = sent.split("\n\n")
    assert lines[0].splitlines() == [
        f"We copied Exhibit {i}.pdf into the Trial Exhibits folder." for i in range(1, 6)
    ]
    assert lines[1] == LOOK
    assert owed.records[0][2] == "mcp_smokeball_copy_files_to_folder failed"


def test_a_retry_of_the_same_target_that_succeeds_counts_as_success(seat) -> None:
    mod, d1, mail, owed = seat
    _email(mod)
    _render(mod, "Responses to RFP Set One", OUT_OF_TIME)
    _render(mod, "Responses to RFP Set One", _filed("Responses to RFP Set One.docx"))
    _draft(mod, "It timed out the first time but should be there now, can you check?")
    assert [s["comment"] for s in mail.sends] == [
        "We filed Responses to RFP Set One.docx in the matter."
    ]
    assert owed.records == []


def test_the_lane_sentence_goes_with_the_filed_line_and_nothing_the_model_wrote(seat) -> None:
    mod, d1, mail, owed = seat
    ack = "Received. The discovery responses for matter 700451 are with us, and you'll get a reply in this thread when the draft is filed in the matter."
    _email(mod)
    _tool(
        mod,
        "drafting_job_submit",
        {"matter_number": "700451"},
        {"accepted": True, "job_id": "01JX", "say": ack},
    )
    _tool(
        mod,
        "mcp_smokeball_add_file",
        {"matter_id": MATTER, "file_name": "Cover Letter.docx"},
        _filed("Cover Letter.docx"),
    )
    _draft(mod, f"{ack}\n\nAlso, which set should go first?")
    assert [s["comment"] for s in mail.sends] == [
        f"{ack}\n\nWe filed Cover Letter.docx in the matter."
    ]


def test_one_look_into_line_per_thread_until_smd_answers(seat) -> None:
    mod, d1, mail, owed = seat
    _email(mod)
    _render(mod, "Brief", OUT_OF_TIME)
    _draft(mod, "It failed.")
    assert [s["comment"] for s in mail.sends] == [LOOK]
    # A second ask in the same thread while SMD still owes the first: nothing goes.
    _email(
        mod, "Any update?", message_id="AAMkACT2=", imid="<CAact2@mail.firm.example>", session="t2"
    )
    _render(mod, "Brief", OUT_OF_TIME)
    _draft(mod, "It failed again.", call="d2")
    assert len(mail.sends) == 1 and _held(d1)[-1] == "look_into_owed"
    assert [r[1] for r in owed.records] == [IMID, "<CAact2@mail.firm.example>"]
    # SMD closes the first item: the thread may hear the line again.
    owed.open.clear()
    _email(mod, "And now?", message_id="AAMkACT3=", imid="<CAact3@mail.firm.example>", session="t3")
    _render(mod, "Brief", OUT_OF_TIME)
    _draft(mod, "Still failing.", call="d3")
    assert [s["comment"] for s in mail.sends] == [LOOK, LOOK]


def test_an_owed_row_that_cannot_be_written_still_sends_the_line(seat) -> None:
    mod, d1, mail, owed = seat
    owed.ok = False
    _email(mod)
    _render(mod, "Brief", OUT_OF_TIME)
    _draft(mod, "It failed.")
    assert [s["comment"] for s in mail.sends] == [LOOK]


# -- a turn with no act -----------------------------------------------------


def test_a_question_turn_with_no_act_sends_the_models_words_unchanged(seat) -> None:
    mod, d1, mail, owed = seat
    _email(mod, "When is the trial date on this one?")
    _tool(
        mod,
        "mcp_smokeball_get_files_on_matter",
        {"matter_id": MATTER},
        {"value": [{"id": "f1", "name": "Trial Notice", "fileExtension": ".pdf"}]},
    )
    words = "The trial is set for the spring calendar, per the notice in the file."
    _draft(mod, words)
    assert [s["comment"] for s in mail.sends] == [words]


def test_the_conditional_act_is_held_until_the_work_is_done(seat) -> None:
    mod, d1, mail, owed = seat
    _email(mod, "Was the revised set filed? If not, please do so.")
    _draft(mod, "The revised set has not been filed yet.")
    assert mail.sends == [] and _held(d1) == ["act_owed"]
    # The redraft after doing it: an act turn, written by code.
    _tool(
        mod,
        "mcp_smokeball_add_file",
        {"matter_id": MATTER, "file_name": "Revised Set.docx"},
        _filed("Revised Set.docx"),
    )
    _draft(mod, "Done.", call="d2")
    assert [s["comment"] for s in mail.sends] == ["We filed Revised Set.docx in the matter."]


def test_the_conditional_act_past_the_cap_sends_the_look_into_line(seat) -> None:
    mod, d1, mail, owed = seat
    _email(mod, "Was the revised set filed? If not, please do so.")
    for i in range(2):
        _draft(mod, "The revised set has not been filed.", call=f"d{i}")
    assert mail.sends == [] and _held(d1) == ["act_owed", "act_owed"]
    _draft(mod, "The revised set has not been filed.", call="d9")
    assert [s["comment"] for s in mail.sends] == [LOOK]
    assert owed.records and owed.records[0][0] == "look_into"


def test_a_status_answer_that_contradicts_the_file_list_is_held(seat) -> None:
    mod, d1, mail, owed = seat
    _email(mod, "Is the Word settlement statement filed?")
    _tool(
        mod,
        "mcp_smokeball_get_files_on_matter",
        {"matter_id": MATTER},
        {"value": [{"id": "f9", "name": "Settlement Statement", "fileExtension": ".docx"}]},
    )
    _draft(mod, "The settlement statement has not been filed yet.")
    assert mail.sends == [] and _held(d1) == ["status_contradicted"]
    _draft(mod, "Yes. Settlement Statement.docx is in the matter.", call="d2")
    assert [s["comment"] for s in mail.sends] == [
        "Yes. Settlement Statement.docx is in the matter."
    ]


def test_after_a_gateway_restart_the_models_reply_goes_as_today(seat) -> None:
    """The record is in-process: a restart between the act and the draft leaves
    none, and the reply is the model's, through every check, as before."""
    mod, d1, mail, owed = seat
    _email(mod)
    _render(mod, "Brief", OUT_OF_TIME)
    turn_facts.TURN_FACTS.clear()  # the restart
    _draft(mod, "We are working on the brief.")
    assert [s["comment"] for s in mail.sends] == ["We are working on the brief."]


def test_another_vertical_is_untouched(seat, tmp_path) -> None:
    mod, d1, mail, owed = seat
    yaml_path = tmp_path / "other.yaml"
    yaml_path.write_text(
        _SEAT_YAML.format(adapter="msgraph").replace("vertical: law-firm", "vertical: hosted")
    )
    mod._YAML_PATH = yaml_path
    _email(mod)
    _render(mod, "Brief", OUT_OF_TIME)
    _draft(mod, "Working on it.")
    assert [s["comment"] for s in mail.sends] == ["Working on it."]


def test_the_checklist_never_bounces_code_words_back_to_the_model(seat, monkeypatch) -> None:
    """The checklist judges the model's prose; a reply code wrote has none, so
    a finding on it (an id in a matter's own file name) cannot hold it for a
    redraft that could not change it. FALSIFIER: drop model_words_only and the
    reply is held as output_checklist and never sent."""
    mod, d1, mail, owed = seat
    monkeypatch.setattr(mod, "_checklist_violations", lambda *a, **k: ("staff_ids", "x"))
    _email(mod)
    name = "Claim 0012345678 Letter.docx"
    _tool(mod, "mcp_smokeball_add_file", {"matter_id": MATTER, "file_name": name}, _filed(name))
    _draft(mod, "Filed.")
    assert [s["comment"] for s in mail.sends] == [f"We filed {name} in the matter."]
    # The model's own words are still held by it.
    _email(
        mod, "When is it?", message_id="AAMkACT4=", imid="<CAact4@mail.firm.example>", session="t4"
    )
    _draft(mod, "The hearing is on calendar.", call="d4")
    assert len(mail.sends) == 1 and _held(d1)[-1] == "output_checklist"


# -- reading results --------------------------------------------------------


@pytest.mark.parametrize(
    ("result", "outcome"),
    [
        (_filed("A.docx"), turn_facts.OK),
        ({"status": "link_not_visible", "fileId": "f1"}, turn_facts.FAILED),
        ({"status": "revise", "fileId": None, "refusals": ["x"]}, turn_facts.REFUSED),
        (OUT_OF_TIME, turn_facts.OUT_OF_TIME),
        ({"error": "boom"}, turn_facts.FAILED),
        ({"unexpected": True}, turn_facts.FAILED),
    ],
)
def test_a_filing_result_reads_as_its_outcome(result: dict, outcome: str) -> None:
    (entry,) = turn_facts.read_call(
        "mcp_smokeball_add_file", {"matter_id": "m", "file_name": "A.docx"}, json.dumps(result)
    )
    assert entry.outcome == outcome
    assert bool(entry.line) is (outcome == turn_facts.OK)


def test_a_lane_say_none_composes_nothing_and_a_tool_off_the_table_is_ignored() -> None:
    silent = turn_facts.read_call(
        "records_job_submit", {}, json.dumps({"accepted": False, "say": "none"})
    )
    assert turn_facts.compose(tuple(silent), []).text == ""
    assert turn_facts.read_call("mcp_smokeball_get_matter", {}, "{}") == []


def test_listed_names_and_the_contradiction_read() -> None:
    names = turn_facts.listed_names(
        json.dumps({"value": [{"id": "1", "name": "Demand Letter", "fileExtension": ".docx"}]})
    )
    assert names == ("Demand Letter.docx",)
    assert (
        turn_facts.listing_contradiction(names, "The demand letter was not filed.")
        == "Demand Letter.docx"
    )
    assert turn_facts.listing_contradiction(names, "The demand letter is filed.") == ""
