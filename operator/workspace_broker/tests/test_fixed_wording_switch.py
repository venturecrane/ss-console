"""What a seat does with ``logic_in_code.fixed_wording`` off and on.

A merge reaches a client seat without a person (ring 1, before the pilot), so
mechanism 4 ships switched off. Absent means ``observe``: the firm's reply is what
it is today, and one audit row says whether the model's words were the fixed
wording. ``enforce`` is ``test_fixed_wording_delivered``. Held wording that refuses
today keeps refusing in ``observe``: that is live behaviour from the 2026-10-24
incident, and a switch added later must not reopen it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_held_wording import STATUS_SMD, _lit
from test_reply_binding import (
    ADMIN,
    LIT_YAML,
    NEG_YAML,
    YAML,
    FakeMailbox,
    _ack_in_thread,
    _broker,
    _call,
    _job,
    _negotiation_notice,
    _note,
    _note_box,
    _rows,
)
from workspace_broker import fixed_wording, reply_text
from workspace_broker.demand_ledger import DemandLedger
from workspace_broker.reply_binding import BindingRefused

OTHER_WORDS = "Here is your update: everything is filed and ready for you."
ENFORCE = "logic_in_code:\n  fixed_wording: enforce\n"


def _demand(tmp_path: Path) -> str:
    job = _job(tmp_path, ["running"])
    files = [{"role": "demand", "name": "Demand.Doe.docx", "size": 10}]
    DemandLedger(str(tmp_path / "audit.db"), tmp_path / "q").record(
        job, "delivered", {"folder_id": "f-1", "delivery": {"files": files, "coverage_report": False}}
    )
    return job


def _box() -> FakeMailbox:
    box = FakeMailbox()
    _ack_in_thread(box)
    return box


def _outcomes(broker, *names: str) -> list[dict]:
    return [r for r in _rows(broker, "REPLY_BINDING") if r.get("outcome") in names]


# -- the switch's own rule -----------------------------------------------------------


@pytest.mark.parametrize(
    ("authored", "enforcing"),
    [
        ("", False),
        ("logic_in_code:\n  printed_source_check: enforce\n", False),
        ("logic_in_code:\n  fixed_wording: observe\n", False),
        ("logic_in_code:\n  fixed_wording: enforce\n", True),
        # A present value that is not one of the two must not hold a door open.
        ("logic_in_code:\n  fixed_wording: enforced\n", True),
    ],
)
def test_the_broker_reads_this_mechanisms_own_key_off_its_own_config(
    tmp_path: Path, authored: str, enforcing: bool
) -> None:
    """The reader's rule is pinned where it lives (``test_logic_in_code_sync``);
    this pins the key and the file the broker asks it about."""
    assert reply_text.enforcing(_broker(tmp_path, FakeMailbox(), YAML + authored)) is enforcing


def test_the_switch_is_read_fresh_on_each_call(tmp_path: Path) -> None:
    broker = _broker(tmp_path, FakeMailbox())
    assert reply_text.enforcing(broker) is False
    broker.customer_path.write_text(YAML + ENFORCE)
    assert reply_text.enforcing(broker) is True


# -- observe: the firm's reply is what it is today ------------------------------------


def _delivered_rows(tmp_path: Path) -> list[tuple[str, str, str, FakeMailbox]]:
    """(kind, job id, the seat's config, its mailbox) for three lanes that had no
    wording check before the switch: a demand, a notice and a note."""
    a = tmp_path / "demand"
    b = tmp_path / "notice"
    c = tmp_path / "note"
    for d in (a, b, c):
        d.mkdir()
    return [
        ("demand_job", _demand(a), YAML, _box()),
        ("negotiation_job", _negotiation_notice(b)[1], NEG_YAML, FakeMailbox()),
        ("note_job", _note(c), YAML, _note_box()),
    ]


def test_every_lane_sends_the_models_words_and_records_the_difference(tmp_path: Path) -> None:
    for (kind, job, yaml_text, box), home in zip(
        _delivered_rows(tmp_path), (tmp_path / "demand", tmp_path / "notice", tmp_path / "note")
    ):
        binding = {"kind": kind, "job_id": job}
        out = _call(
            _broker(home, box, yaml_text),
            "msgraph_reply_bound",
            binding,
            payload={"comment": OTHER_WORDS},
            session_id="s",
        )
        assert out["recipients"], kind
        rows = _outcomes(_broker(home, box, yaml_text), "wording_differs")
        assert len(rows) == 1, kind
        assert "wording" in rows[0]["reason"] and rows[0]["job_id"] == job
        # The row names the lane and state, never the body.
        assert OTHER_WORDS not in json.dumps(rows)
        assert not _outcomes(_broker(home, box, yaml_text), "send_refused"), kind


def test_a_reply_in_the_fixed_words_is_recorded_as_matching(tmp_path: Path) -> None:
    """Without this row, "no differences" could not be told from "never checked"."""
    job = _demand(tmp_path)
    box = _box()
    binding = {"kind": "demand_job", "job_id": job}
    (text,) = reply_text.texts_for(_broker(tmp_path, box), "demand_job", job)
    _call(_broker(tmp_path, box), "msgraph_reply_bound", binding, payload={"comment": text}, session_id="s")
    broker = _broker(tmp_path, box)
    assert [r["reason"] for r in _outcomes(broker, "wording_matches")] == [
        "fixed_wording: the body is the demand delivered wording"
    ]
    assert not _outcomes(broker, "wording_differs")


def test_the_model_is_handed_no_words_on_a_delivered_row_in_observe(tmp_path: Path) -> None:
    """The model reads tool results, so a ``fixed_wording`` or ``reply_text`` key
    could change what it writes with no switch turned on and no skill changed.
    FALSIFIER: return the rendered texts from ``reply_text.bind_keys`` on a
    row ``reply_text.refuses`` says no to."""
    rows = zip(_delivered_rows(tmp_path), (tmp_path / "demand", tmp_path / "notice", tmp_path / "note"))
    for (kind, job, yaml_text, box), home in rows:
        bound = _call(_broker(home, box, yaml_text), "msgraph_reply_bind", {"kind": kind, "job_id": job})
        assert bound["bound"] is True, kind
        leaked = {k for k in bound if "wording" in k or "reply_text" in k}
        assert not leaked, (kind, leaked)
        # The texts are not anywhere else in the verdict either.
        rendered = reply_text.texts_for(_broker(home, box, yaml_text), kind, job)
        assert rendered and not any(t in json.dumps(bound) for t in rendered), kind


def test_a_second_send_is_still_refused_by_the_one_reply_rule(tmp_path: Path) -> None:
    """Observing changes the wording check and nothing else."""
    job = _demand(tmp_path)
    box = _box()
    binding = {"kind": "demand_job", "job_id": job}
    _call(_broker(tmp_path, box), "msgraph_reply_bound", binding, payload={"comment": OTHER_WORDS}, session_id="s")
    with pytest.raises(BindingRefused):
        _call(_broker(tmp_path, box), "msgraph_reply_bound", binding, payload={"comment": OTHER_WORDS}, session_id="s")
    assert len(box.replies()) == 1


def test_a_renderer_fault_never_stops_a_reply_in_observe(tmp_path: Path, monkeypatch) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("renderer fault")

    job = _demand(tmp_path)
    box = _box()
    binding = {"kind": "demand_job", "job_id": job}
    monkeypatch.setattr(reply_text, "fixed_texts", boom)
    bound = _call(_broker(tmp_path, box), "msgraph_reply_bind", binding)
    assert bound["bound"] is True and "fixed_wording" not in bound
    out = _call(
        _broker(tmp_path, box), "msgraph_reply_bound", binding, payload={"comment": OTHER_WORDS}, session_id="s"
    )
    assert out["recipients"] == [ADMIN] and len(box.replies()) == 1


# -- what refuses today still refuses -------------------------------------------------


@pytest.mark.parametrize("yaml_extra", ["", ENFORCE], ids=["observe", "enforce"])
def test_a_held_litigation_reply_refuses_other_words_either_way(tmp_path: Path, yaml_extra: str) -> None:
    """The 2026-10-24 incident: observing must not reopen it. FALSIFIER: make
    ``reply_text.refused_today`` answer False."""
    job = _lit(tmp_path, "held", "gate_refused: x")
    box = _box()
    binding = {"kind": "litigation_job", "job_id": job}
    yaml_text = LIT_YAML + yaml_extra
    bound = _call(_broker(tmp_path, FakeMailbox(), yaml_text), "msgraph_reply_bind", binding)
    assert bound["held_wording"] == [STATUS_SMD]
    # In observe the bind is byte for byte what it was: the old key alone.
    assert bound.get("fixed_wording") == ([STATUS_SMD] if yaml_extra else None)
    with pytest.raises(fixed_wording.FixedWordingRefused):
        _call(_broker(tmp_path, box, yaml_text), "msgraph_reply_bound", binding, payload={"comment": OTHER_WORDS})
    assert box.replies() == []
    out = _call(
        _broker(tmp_path, box, yaml_text),
        "msgraph_reply_bound",
        binding,
        payload={"comment": STATUS_SMD},
        session_id="s",
    )
    assert out["recipients"] == [ADMIN] and len(box.replies()) == 1


def test_a_held_demand_refuses_nothing_in_observe_because_it_refused_nothing_before(tmp_path: Path) -> None:
    job = _job(tmp_path, ["running", "held"])
    box = _box()
    binding = {"kind": "demand_job", "job_id": job}
    out = _call(
        _broker(tmp_path, box), "msgraph_reply_bound", binding, payload={"comment": OTHER_WORDS}, session_id="s"
    )
    assert out["recipients"] == [ADMIN]
    assert _outcomes(_broker(tmp_path, box), "wording_differs")


def test_enforce_refuses_before_the_claim_and_records_the_refusal_not_a_difference(tmp_path: Path) -> None:
    job = _demand(tmp_path)
    box = _box()
    binding = {"kind": "demand_job", "job_id": job}
    with pytest.raises(fixed_wording.FixedWordingRefused):
        _call(_broker(tmp_path, box, YAML + ENFORCE), "msgraph_reply_bound", binding, payload={"comment": OTHER_WORDS})
    broker = _broker(tmp_path, box, YAML + ENFORCE)
    assert box.replies() == [] and _outcomes(broker, "send_refused") and not _outcomes(broker, "wording_differs")
    text = _call(broker, "msgraph_reply_bind", binding)["fixed_wording"][0]
    out = _call(broker, "msgraph_reply_bound", binding, payload={"comment": text}, session_id="s")
    assert out["recipients"] == [ADMIN] and len(box.replies()) == 1


# -- the status read carries the text only where the send holds to it -----------------


def _with_text(tmp_path: Path, yaml_text: str, job: str) -> dict:
    handler = reply_text.with_reply_text("demand_job", lambda *_a: {"ok": True, "job": {"job_id": job}})
    return handler(_broker(tmp_path, FakeMailbox(), yaml_text), "demand_job_status", {"job_id": job}, 1, None)["job"]


def test_a_status_read_carries_the_text_only_when_enforced(tmp_path: Path) -> None:
    job = _demand(tmp_path)
    assert "reply_text" not in _with_text(tmp_path, YAML, job)
    assert "Demand.Doe.docx" in _with_text(tmp_path, YAML + ENFORCE, job)["reply_text"]


# -- a revision of a filed draft (main's revise mode, 2026-11-04) -----------------------


def _revision(**row) -> dict:
    return {"matter_number": "900201", "revise_of": "01M5ABCDEFGHJKMNPQRSTVWXYZ", **row}


def test_a_delivered_revision_says_new_versions_and_never_that_every_change_was_made() -> None:
    delivery = {
        "files": [{"role": "draft", "name": "Brief.docx"}, {"role": "attorney_notes", "name": "Notes.docx"}],
        "markers": [{"kind": "ATTORNEY", "text": "settlement authority"}],
    }
    (text,) = fixed_wording.render("drafting_job", _revision(state="delivered", delivery_json=json.dumps(delivery)))
    assert text.startswith(
        "The document for matter 900201 and its attorney notes are filed as new versions of the same files in the "
        'same folder, each named exactly as filed: "Brief.docx" and "Notes.docx".'
    )
    assert "The attorney notes list each change asked for, whether it was made, and why any was not." in text
    assert 'For the attorney, marked in the draft: "settlement authority".' in text
    assert text.endswith("Nothing has been sent to anyone outside the firm.")
    assert "ready for attorney review" not in text


def test_a_revision_that_would_change_nothing_says_why_and_never_not_drafted() -> None:
    reason = (
        "revise_unchanged: no new version was filed, because nothing in the brief would change: "
        '"take out the cites" is already how the filed brief reads'
    )
    (text,) = fixed_wording.render("drafting_job", _revision(state="held", reason=reason))
    assert text == (
        "No new version of the document for matter 900201 was filed, because nothing in the brief would change: "
        '"take out the cites" is already how the filed brief reads.'
    )
    assert "revise_unchanged" not in text and "not drafted" not in text


def test_any_other_held_revision_is_not_called_undrafted_and_relays_no_reason() -> None:
    (text,) = fixed_wording.render("drafting_job", _revision(state="held", reason="destination_mismatch: wrong matter"))
    assert text == "The changes to the document for matter 900201 are not all made yet. Nothing is needed from you."
