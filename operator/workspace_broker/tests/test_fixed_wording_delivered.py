"""A delivered job's reply is rendered by the broker from the job's record and
is the only body the bound send accepts (mechanism 4, ADR 0104, 2026-11-04).

Until this, every delivered reply was the model's own composition from counts
under exact-label rules in skill prose, and nothing checked the words: a label
the model shortened read as a different measure, and a count it restated was a
count it could restate wrong. Now:

* every lane renders its delivered row (and, for the demand and the drafted
  document, its held row) from the row's own columns, and the chronology's
  allowance from the broker's own read at bind time;
* a value the row does not carry omits its sentence: nothing is rendered with a
  gap, nothing is guessed;
* the send refuses any other words BEFORE the claim, so the retry with the
  quoted text still has its one reply (the 2026-10-24 falsifier's shape);
* each lane's status read carries the same text as ``reply_text``.

All of this is a seat in ``enforce``. A seat in ``observe`` is
``test_fixed_wording_switch``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_reply_binding import (
    ADMIN,
    IMID,
    LIT_YAML,
    MATTER,
    NEG_YAML,
    YAML,
    FakeMailbox,
    _ack_in_thread,
    _call,
    _job,
    _litigation_job,
    _negotiation_notice,
    _note,
    _note_box,
    _rows,
)
from test_reply_binding import _broker as _plain_broker
from workspace_broker import delivered_matter, delivered_wording, fixed_wording
from workspace_broker.reply_binding import BindingRefused

ENFORCE = "logic_in_code:\n  fixed_wording: enforce\n"


def _broker(tmp_path: Path, box: FakeMailbox, yaml_text: str = YAML):
    """Every test here is a seat switched to ``enforce``; ``test_fixed_wording_switch``
    is what a seat does with the switch off."""
    return _plain_broker(tmp_path, box, yaml_text + ENFORCE)


OTHER_WORDS = "Here is your update: everything is filed and ready for you."


def _files(*pairs: tuple[str, str]) -> str:
    return json.dumps({"files": [{"role": r, "name": n, "size": 10} for r, n in pairs]})


# -- each lane renders its delivered row ------------------------------------------


def test_the_status_list_names_its_folder_files_and_counts_exactly() -> None:
    row = {
        "state": "delivered",
        "report": "status",
        "folder_name": "Litigation Status",
        "matters_total": 64,
        "matters_reread": 7,
        "flags_new": 3,
        "delivery_json": _files(
            ("workbook", "Litigation status 11-04-26.xlsx"),
            ("per_case_workbook", "Litigation status by case 11-04-26.xlsx"),
            ("printout_pdf", "Court dates 11-04-26.pdf"),
        ),
    }
    assert fixed_wording.render("litigation_job", row) == [
        'The litigation status list is filed in the firm\'s Operator Library, folder "Litigation Status", as '
        '"Litigation status 11-04-26.xlsx". The same list, one sheet per case for printing, is filed beside it as '
        '"Litigation status by case 11-04-26.xlsx". A printable PDF of every case\'s court dates, with the source '
        'of each date, is filed beside it as "Court dates 11-04-26.pdf".\n\n'
        "Matters covered: 64. Re-read because their files changed since the last list: 7. New flags for review: 3."
        "\n\nNothing has been sent to anyone outside the firm."
    ]


def test_a_value_the_row_lacks_omits_its_sentence_never_a_gap() -> None:
    """A row from before ``folder_name`` was recorded, with no re-reads and no
    flags: the folder is not named, the empty counts are left out, and no
    ``{field}`` or ``<slot>`` survives."""
    row = {
        "state": "delivered",
        "matters_total": 12,
        "matters_reread": 0,
        "flags_new": None,
        "delivery_json": _files(("workbook", "List.xlsx")),
    }
    (text,) = fixed_wording.render("litigation_job", row)
    assert text == (
        'The litigation status list is filed in the firm\'s Operator Library as "List.xlsx".\n\n'
        "Matters covered: 12.\n\nNothing has been sent to anyone outside the firm."
    )
    assert "{" not in text and "<" not in text and "folder" not in text and "none" not in text.lower()


def test_the_deadlines_report_carries_each_count_under_its_exact_label_and_both_closers() -> None:
    row = {
        "state": "delivered",
        "report": "deadlines",
        "folder_name": "Deadlines",
        "counts_json": json.dumps({"matters_read": 601, "litigation": 176, "dates_ahead": 9, "tasks_overdue": 0}),
        "delivery_json": _files(("workbook", "Deadlines 11-04-26.xlsx"), ("statute", "Statute 11-04-26.xlsx")),
    }
    (text,) = fixed_wording.render("litigation_job", row)
    assert text == (
        'Filed in the firm\'s Operator Library, folder "Deadlines": "Deadlines 11-04-26.xlsx" and '
        '"Statute 11-04-26.xlsx".\n\nOpen and Pending matters read: 601.\nIn litigation: 176.\n\n'
        "A case can hold filed papers that were never entered as filed, so the statute report shows each case's "
        "count of court-named documents to check before treating it as unfiled. Nothing has been sent to anyone "
        "outside the firm."
    )
    # dates_ahead has no label in the skill, so it is never stated.
    assert "9" not in text


def test_a_staff_reassignment_names_the_requests_people_and_its_scope() -> None:
    row = {
        "state": "delivered",
        "report": "reassign",
        "reassign_json": json.dumps(
            {
                "from_names": ["Pat Doe", "Sam"],
                "to_name": "Dana Roe",
                "attorney_names": ["Lee Example"],
                "litigation_only": True,
                "statuses": ["Open", "Pending"],
            }
        ),
        "counts_json": json.dumps({"cases_moved": 41, "tasks_moved": 109, "cases_outside_scope": 12}),
    }
    assert fixed_wording.render("litigation_job", row) == [
        "Cases moved from Pat Doe and Sam to Dana Roe: 41. Scope: Open and Pending litigation cases, responsible "
        "attorney Lee Example. Open tasks moved with them: 109. Cases left as they were because they are outside "
        "this request: 12."
    ]
    nothing = {**row, "counts_json": json.dumps({"cases_moved": 0, "cases_skipped": 2})}
    assert fixed_wording.render("litigation_job", nothing)[0].startswith("No case was changed. Scope:")


def test_the_discovery_breakdown_names_its_two_files_and_the_declined_count() -> None:
    row = {
        "state": "delivered",
        "report": "discovery12",
        "folder_name": "Litigation Status",
        "counts_json": json.dumps({"d12_declined": 2, "d12_read": 30}),
        "delivery_json": _files(("discovery_workbook", "Discovery.xlsx"), ("printout_pdf", "Discovery.pdf")),
    }
    (text,) = fixed_wording.render("litigation_job", row)
    assert 'breakdown is filed in the firm\'s Operator Library, folder "Litigation Status", as "Discovery.xlsx"' in text
    assert 'Its print-out is filed beside it as "Discovery.pdf".' in text
    assert "because the reading service declined them: 2." in text and "30" not in text


def test_a_case_file_review_leads_with_the_count_and_offers_the_numbered_changes() -> None:
    row = {
        "state": "delivered",
        "mode": "full",
        "matters_total": 40,
        "matters_read": 9,
        "matters_current": 31,
        "matters_held": 0,
        "lines_proposed": 4,
        "metrics_json": json.dumps({"billing_count": {"Open": 38, "Pending": 2}}),
        "delivery_json": _files(("workbook", "Case files 11-04-26.xlsx"), ("file_status", "File Status.docx")),
    }
    (text,) = fixed_wording.render("casefile_job", row)
    assert text.startswith("Count on the 1st: 38 Open, 2 Pending.\n\nThe ranked list is filed")
    assert "Files covered: 40. Read this time: 9. Already current, with nothing new: 31." in text
    assert "could not be reviewed" not in text
    assert 'Changes proposed, numbered on the list\'s "Proposed changes" sheet: 4. To make them' in text


def test_a_case_file_apply_states_the_numbers_made_and_left() -> None:
    row = {
        "state": "delivered",
        "mode": "apply",
        "outcomes_json": json.dumps(
            [{"n": 1, "outcome": "completed"}, {"n": 3, "outcome": "already_present"}, {"n": 4, "outcome": "changed"}]
        ),
    }
    assert fixed_wording.render("casefile_job", row) == [
        "Changes made and checked in Smokeball: 1 and 3. Changes left as they are, because the file changed since "
        "they were proposed or the change did not check out, so nothing was guessed: 4."
    ]


ALLOWANCE = {"authored": True, "unit": "pages", "remaining": 4210, "month": "the cycle ending Dec 14"}


def test_a_chronology_states_its_counts_and_the_allowance_the_broker_read() -> None:
    row = {"state": "delivered", "matter_number": "900201", "documents": 48, "pages": 790, "delivery_json": "{}"}
    reads = {"allowance": ALLOWANCE, "job_pages_credited": 40}
    assert fixed_wording.render("medchron_job", row, reads) == [
        "The medical chronology for matter 900201 is filed on the matter, in its own folder. Documents read: 48. "
        "Pages: 790.\n\nThis delivery used 750 pages; 4210 pages remain for the cycle ending Dec 14."
    ]
    # No allowance read (unauthored, unreadable): its sentence is left out.
    assert "remain" not in fixed_wording.render("medchron_job", row)[0]
    unauthored = {"allowance": {"authored": False}, "job_pages_credited": 0}
    assert "remain" not in fixed_wording.render("medchron_job", row, unauthored)[0]


def test_a_joint_chronology_and_an_update_lead_with_what_is_true() -> None:
    base = {"state": "delivered", "matter_number": "900201", "documents": 3, "pages": 9}
    joint = {**base, "delivery_json": json.dumps({"folders": ["f1", "f2"]})}
    update = {**base, "delivery_json": json.dumps({"replaced": {"old": "x"}})}
    assert "one per client, each in its own folder: 2 in all." in fixed_wording.render("medchron_job", joint)[0]
    assert "earlier version of the document was replaced" in fixed_wording.render("medchron_job", update)[0]


def test_an_already_current_update_lists_each_kind_in_the_skills_words() -> None:
    summary = {
        "requested": 5,
        "files": 6,
        "by_reason": {"billing_only": {"files": 4, "pages": 9}, "duplicate": {"files": 2, "pages": 3}, "new": {}},
        "review_file": "Records review.docx",
    }
    row = {"state": "delivered", "matter_number": "900201", "delivery_json": json.dumps({"already_current": summary})}
    (text,) = fixed_wording.render("medchron_job", row, {"allowance": ALLOWANCE, "job_pages_credited": 0})
    assert text.startswith(
        "The chronology for matter 900201 already includes these records. Of the 5 files named (and the 1 found "
        "inside them), none adds a visit:\n\nBilling pages only: 4.\nExact copies of files already read: 2.\n\n"
        "The records review filed beside"
    )


def test_a_demand_names_where_it_is_and_asks_the_one_question_only_with_a_list() -> None:
    files = _files(("demand", "Demand.Doe.docx"), ("gap_audit", "Gap Audit.docx"))
    row = {"state": "delivered", "matter_number": "900201", "matter_id": MATTER, "file_to_matter_id": MATTER}
    (text,) = fixed_wording.render("demand_job", {**row, "delivery_json": files})
    assert "filed in its dated folder in the client matter" in text and '"Demand.Doe.docx"' in text
    assert text.endswith("Reply yes and I'll prepare the orders for your OK.")
    library = {**row, "file_to_matter_id": "library-id", "delivery_json": _files(("demand", "Demand.Doe.docx"))}
    (text,) = fixed_wording.render("demand_job", library)
    assert "the firm's library matter, not the client matter" in text and "order" not in text
    covered = {**row, "delivery_json": json.dumps({"files": [], "coverage_report": True})}
    assert fixed_wording.render("demand_job", covered) == [
        "No demand was drafted for matter 900201: the file cannot carry a demand yet. Nothing has been sent to "
        "anyone outside the firm."
    ]


def test_a_held_demand_or_document_never_relays_the_reason() -> None:
    held = {"state": "held", "matter_number": "900201", "reason": "the job files to matter X; refused"}
    assert fixed_wording.render("demand_job", held) == [
        "The demand for matter 900201 is not drafted yet. Nothing is needed from you."
    ]
    needs = {**held, "reason": "request_incomplete: the request is missing what the draft needs: the deponent's name"}
    assert fixed_wording.render("drafting_job", needs) == [
        "The document for matter 900201 is not drafted yet. The request is missing what the draft needs: the "
        "deponent's name."
    ]
    # A hold on our side (2026-11-06): the checking note, never the reason.
    ours = {**held, "reason": "destination_mismatch: the matter id does not carry matter number 900201"}
    assert fixed_wording.render("drafting_job", ours) == [
        "The document for matter 900201 is not ready yet. We are checking it before it is filed. Nothing is "
        "needed from you."
    ]


def test_a_drafted_document_lists_its_markers_and_caption_findings_as_recorded() -> None:
    delivery = {
        "files": [{"role": "draft", "name": "Brief.docx"}, {"role": "attorney_notes", "name": "Notes.docx"}],
        "markers": [
            {"kind": "ATTORNEY", "text": "settlement authority"},
            {"kind": "ATTORNEY", "text": "settlement authority"},
            {"kind": "CLIENT", "text": "date of the first visit"},
        ],
        "caption_discrepancies": [{"field": "case_number", "document_value": "23CV1", "record_value": "23CV10"}],
    }
    row = {
        "state": "delivered",
        "matter_number": "900201",
        "matter_id": MATTER,
        "file_to_matter_id": MATTER,
        "delivery_json": json.dumps(delivery),
    }
    (text,) = fixed_wording.render("drafting_job", row)
    assert 'ready for attorney review: "Brief.docx". The attorney notes are filed beside it as "Notes.docx".' in text
    assert 'For the attorney, marked in the draft: "settlement authority".\n' in text
    assert "Not in the file" not in text
    assert "verification" not in text
    responses = {**row, "document_class": "discovery_response"}
    assert "The verification is left for the client to sign." in fixed_wording.render("drafting_job", responses)[0]
    assert 'Case number: the court paper says "23CV1"; the practice-management record says "23CV10".' in text


def test_a_notice_and_a_note_are_their_recorded_words_and_the_sign_off() -> None:
    notice = {"state": "delivered", "notice": True, "text": "New offer on matter 200123: $25,000."}
    assert fixed_wording.render("negotiation_job", notice) == ["New offer on matter 200123: $25,000.\n\nThanks,"]
    assert fixed_wording.render("negotiation_job", {**notice, "notice": False}) == []
    assert fixed_wording.render("note_job", {"state": "delivered", "text": "Hi,\n\nFiled.\n"}) == [
        "Hi,\n\nFiled.\n\nThanks,"
    ]


def test_a_failed_or_unfinished_row_renders_nothing() -> None:
    for kind in fixed_wording.KIND_LANE:
        for state in ("failed", "running", "queued"):
            if kind == "drafting_job" and state == "failed":
                # The one exception (2026-11-06): its note, fixed_wording.STOPPED.
                assert fixed_wording.render(kind, {"state": state}) == [fixed_wording.STOPPED["drafting"]["failed"][1]]
            elif kind not in ("note_job",):
                assert fixed_wording.render(kind, {"state": state, "text": "x"}) == [], (kind, state)


# -- the send refuses other words; the retry with the exact text goes -------------


def _deliver_demand(tmp_path: Path) -> str:
    from workspace_broker.demand_ledger import DemandLedger

    job = _job(tmp_path, ["running"])
    files = [
        {"role": "demand", "name": "Demand.Doe.docx", "size": 10},
        {"role": "gap_audit", "name": "Gap Audit.docx", "size": 10},
    ]
    DemandLedger(str(tmp_path / "audit.db"), tmp_path / "q").record(
        job, "delivered", {"folder_id": "f-1", "delivery": {"files": files, "coverage_report": False}}
    )
    return job


def test_a_delivered_demand_in_other_words_is_refused_and_the_exact_text_sends_once(tmp_path: Path) -> None:
    """FALSIFIER (the 126-139 shape): on the code before this the model's own
    words went out on a delivered job. Now they are refused with nothing spent
    and nothing sent, the refusal quotes the text, and that text sends once."""
    job = _deliver_demand(tmp_path)
    box = FakeMailbox()
    _ack_in_thread(box)
    binding = {"kind": "demand_job", "job_id": job}
    bound = _call(_broker(tmp_path, box), "msgraph_reply_bind", binding)
    (text,) = bound["fixed_wording"]
    assert '"Demand.Doe.docx"' in text and "Gap Audit" in text
    with pytest.raises(BindingRefused) as refused:
        _call(_broker(tmp_path, box), "msgraph_reply_bound", binding, payload={"comment": OTHER_WORDS})
    assert box.replies() == [] and text in str(refused.value)
    refusals = [r for r in _rows(_broker(tmp_path, box), "REPLY_BINDING") if r.get("outcome") == "send_refused"]
    assert refusals and "demand delivered" in refusals[-1]["reason"] and OTHER_WORDS not in json.dumps(refusals)
    out = _call(_broker(tmp_path, box), "msgraph_reply_bound", binding, payload={"comment": text}, session_id="s")
    assert out["recipients"] == [ADMIN] and len(box.replies()) == 1
    with pytest.raises(BindingRefused):
        _call(_broker(tmp_path, box), "msgraph_reply_bound", binding, payload={"comment": text}, session_id="s")
    assert len(box.replies()) == 1


def test_a_notice_reworded_or_rounded_is_refused(tmp_path: Path) -> None:
    _job_id, notice = _negotiation_notice(tmp_path)
    box = FakeMailbox()
    binding = {"kind": "negotiation_job", "job_id": notice}
    bound = _call(_broker(tmp_path, box, NEG_YAML), "msgraph_reply_bind", binding)
    assert bound["fixed_wording"] == ["New offer on matter 200123.\n\nThanks,"]
    with pytest.raises(BindingRefused):
        _call(_broker(tmp_path, box, NEG_YAML), "msgraph_reply_bound", binding, payload={"comment": "New offer."})
    out = _call(
        _broker(tmp_path, box, NEG_YAML),
        "msgraph_reply_bound",
        binding,
        payload={"comment": bound["fixed_wording"][0]},
        session_id="s",
    )
    assert out["recipients"] == [ADMIN]


def test_a_note_with_a_word_added_is_refused(tmp_path: Path) -> None:
    note = _note(tmp_path)
    box = _note_box()
    binding = {"kind": "note_job", "job_id": note}
    with pytest.raises(BindingRefused):
        _call(_broker(tmp_path, box), "msgraph_reply_bound", binding, payload={"comment": "Filed.\n\nThanks, SMD"})
    assert box.replies() == []
    _call(_broker(tmp_path, box), "msgraph_reply_bound", binding, payload={"comment": "Filed.\n\nThanks,"})
    assert len(box.replies()) == 1


# -- the chronology's allowance is the broker's read at bind time -------------------

MEDCHRON_YAML = YAML + (
    "personas:\n"
    "  - slug: operator\n"
    "    skills:\n"
    "      - name: medical-chronology-maintainer\n"
    "        enabled: true\n"
    "        settings:\n"
    "          chronology_package_page_allowance_per_month: 1000\n"
)


def _medchron_broker(tmp_path: Path, box: FakeMailbox):
    from workspace_broker.medchron_ledger import MedchronLedger
    from workspace_broker.medchron_verbs import MedchronVerbs

    broker = _broker(tmp_path, box, MEDCHRON_YAML)
    broker.medchron = MedchronVerbs(
        MedchronLedger(broker.audit_db_path, tmp_path / "mq"),
        customer_yaml=str(broker.customer_path),
        customer_slug="firm",
        audit_append=broker.ledger.append,
        gateway_pid=broker.gateway_pid,
        resolve_agent_uid=lambda: broker.agent_uid,
    )
    return broker


def test_the_chronology_reply_states_what_medchron_allowance_answers(tmp_path: Path) -> None:
    from workspace_broker.medchron_ledger import MedchronLedger

    ledger = MedchronLedger(str(tmp_path / "audit.db"), tmp_path / "mq")
    job = ledger.submit(
        {
            "matter": {"id": MATTER, "number": "900201", "title": "Doe v. Roe"},
            "units": [{"client_name": "Jane Doe", "surname": "Doe", "dob": "01/01/1980"}],
            "incident": {"date": "2025-01-01", "source": "matter_layout"},
            "requested_by": ADMIN,
            "request_ref": IMID,
        },
        remaining=1000,
    )
    ledger.record(job, "running", {})
    ledger.record(job, "delivered", {"documents": 12, "pages": 300, "cents": 100, "folder_id": "f-1"})
    box = FakeMailbox()
    _ack_in_thread(box)
    broker = _medchron_broker(tmp_path, box)
    state = broker.medchron.allowance_state()
    status = _call(broker, "medchron_job_status", {}, job_id=job)
    bound = _call(broker, "msgraph_reply_bind", {"kind": "medchron_job", "job_id": job})
    expected = f"This delivery used 300 pages; {state['remaining']} pages remain for {state['month']}."
    assert bound["fixed_wording"][0].endswith(expected) and state["remaining"] == 700
    assert status["job"]["reply_text"] == bound["fixed_wording"][0]


# -- each status read carries the same text the bind returns ------------------------


def _litigation_broker(tmp_path: Path):
    from workspace_broker.litigation_ledger import LitigationLedger
    from workspace_broker.litigation_verbs import LitigationVerbs

    broker = _broker(tmp_path, FakeMailbox(), LIT_YAML)
    broker.litigation = LitigationVerbs(
        LitigationLedger(broker.audit_db_path, tmp_path / "lq"),
        customer_yaml=str(broker.customer_path),
        audit_append=broker.ledger.append,
        firm_config_path=str(tmp_path / "firm.yaml"),
    )
    return broker


def test_the_litigation_status_read_carries_the_bind_text(tmp_path: Path) -> None:
    job = _litigation_job(tmp_path, ["running", "delivered"])
    broker = _litigation_broker(tmp_path)
    status = _call(broker, "litigation_job_status", {}, job_id=job)
    bound = _call(broker, "msgraph_reply_bind", {"kind": "litigation_job", "job_id": job})
    assert status["job"]["reply_text"] == bound["fixed_wording"][0]
    assert 'folder "Litigation Status"' in status["job"]["reply_text"]


def test_a_failed_job_status_carries_no_reply_text(tmp_path: Path) -> None:
    job = _litigation_job(tmp_path, ["failed"])
    status = _call(_litigation_broker(tmp_path), "litigation_job_status", {}, job_id=job)
    assert "reply_text" not in status["job"] and "reply_texts" not in status["job"]


# -- every sentence reads as the firm's words, never the system's --------------------


def _samples() -> list[str]:
    return [t for _lane, _key, t in fixed_wording.delivered_templates()]


def test_no_sentence_names_the_work_as_a_job_or_carries_a_dollar_figure() -> None:
    for sentence in _samples():
        lowered = sentence.lower()
        assert " job" not in lowered and "pipeline" not in lowered and " model" not in lowered, sentence
        assert "$" not in sentence and "\N{EM DASH}" not in sentence, sentence


def test_the_renderer_modules_hold_no_mutable_state_between_calls() -> None:
    row = {"state": "delivered", "matters_total": 1, "delivery_json": _files(("workbook", "A.xlsx"))}
    first = delivered_wording.litigation(row)
    delivered_wording.litigation({**row, "matters_total": 2})
    assert delivered_wording.litigation(row) == first
    assert delivered_matter.stored({"text": "x"}) == "x\n\nThanks,"


def test_a_broker_with_no_audit_ledger_renders_nothing_and_opens_no_ledger(tmp_path: Path, monkeypatch) -> None:
    """Found building this: a status read on a broker with no audit ledger
    opened a job ledger at the path ``str(None)``, a file named "None" in the
    working directory. FALSIFIER: drop the guard in ``reply_text.texts_for``."""
    from workspace_broker import reply_text

    monkeypatch.chdir(tmp_path)
    broker = _broker(tmp_path, FakeMailbox())
    broker.audit_db_path = None
    assert reply_text.texts_for(broker, "litigation_job", "01M5ABCDEFGHJKMNPQRSTVWXYZ") == []
    assert not (tmp_path / "None").exists()
