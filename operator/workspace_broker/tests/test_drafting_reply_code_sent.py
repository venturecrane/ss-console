"""A drafting job's reply is the broker's words on every seat, whatever its
``logic_in_code.fixed_wording`` switch says (2026-11-05).

THE INCIDENT. A&P, discovery_set: the job filed its documents, and the
completion turn wrote its own reply instead of the delivery wording the broker
renders. The seat was in ``observe``, so the bind handed the model no words and
the send would have taken any. The model's text was refused twice by the
identifier check, then held three times as content_sensitive, and the attorney
heard nothing until SMD sent a reply by hand.

So for a drafting job the switch no longer decides: the bind always carries the
rendered text (the seat sends it in code, overlay ``job_reply``), and the send
refuses any other words. A bind refusal carries a closed ``code``, so the seat
can tell "already answered" and "failed on our side" from a fault.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_reply_binding import FakeMailbox, _ack_in_thread, _broker, _call, _drafting_job, _rows
from workspace_broker import reply_text
from workspace_broker.reply_binding import BindingRefused

OWN_WORDS = "The brief is filed in matter 900201, served 11/04/2026. Let me know."


def _box() -> FakeMailbox:
    box = FakeMailbox()
    _ack_in_thread(box)
    return box


@pytest.mark.parametrize("ending", ["delivered", "held"])
def test_an_observe_seat_binds_a_drafting_job_with_its_words(tmp_path: Path, ending: str) -> None:
    """FALSIFIER: on the parent commit an observe seat's bind carries no
    ``fixed_wording`` for a drafting job, so nothing could send the words."""
    job = _drafting_job(tmp_path, ["running", ending])
    broker = _broker(tmp_path, _box())
    assert reply_text.enforcing(broker) is False
    out = _call(broker, "msgraph_reply_bind", {"kind": "drafting_job", "job_id": job})
    assert out["bound"] is True
    (text,) = reply_text.texts_for(broker, "drafting_job", job)
    assert out["fixed_wording"] == [text] and text.strip()


def test_an_observe_seat_refuses_a_drafting_reply_in_other_words(tmp_path: Path) -> None:
    """FALSIFIER: on the parent commit observe sends the model's words."""
    job = _drafting_job(tmp_path, ["running", "delivered"])
    box = _box()
    binding = {"kind": "drafting_job", "job_id": job}
    with pytest.raises(BindingRefused, match="fixed_wording"):
        _call(_broker(tmp_path, box), "msgraph_reply_bound", binding, payload={"comment": OWN_WORDS}, session_id="s")
    assert box.replies() == []
    # The one reply is still unspent: the words go on the next call.
    (text,) = reply_text.texts_for(_broker(tmp_path, box), "drafting_job", job)
    out = _call(_broker(tmp_path, box), "msgraph_reply_bound", binding, payload={"comment": text}, session_id="s")
    assert out["recipients"] and len(box.replies()) == 1


def test_other_lanes_on_an_observe_seat_are_unchanged(tmp_path: Path) -> None:
    """Only the drafting lane is code-sent: a demand bind on observe carries no words."""
    from test_fixed_wording_switch import _demand

    job = _demand(tmp_path)
    out = _call(_broker(tmp_path, _box()), "msgraph_reply_bind", {"kind": "demand_job", "job_id": job})
    assert out["bound"] is True and "fixed_wording" not in out


def test_a_refused_bind_names_its_code(tmp_path: Path) -> None:
    """The seat acts on the code, never on the sentence: already answered is
    done, anything else is an alert. A failed drafting job is no longer
    refused (2026-11-06): it binds to its one fixed note."""
    (tmp_path / "f").mkdir()
    failed = _drafting_job(tmp_path / "f", ["failed"])
    out = _call(
        _broker(tmp_path / "f", FakeMailbox()), "msgraph_reply_bind", {"kind": "drafting_job", "job_id": failed}
    )
    assert out["bound"] is True and "The drafting stopped on our side" in out["fixed_wording"][0]

    (tmp_path / "d").mkdir()
    job = _drafting_job(tmp_path / "d", ["running", "delivered"])
    box = _box()
    binding = {"kind": "drafting_job", "job_id": job}
    (text,) = reply_text.texts_for(_broker(tmp_path / "d", box), "drafting_job", job)
    _call(_broker(tmp_path / "d", box), "msgraph_reply_bound", binding, payload={"comment": text}, session_id="s")
    again = _call(_broker(tmp_path / "d", box), "msgraph_reply_bind", binding)
    assert again["bound"] is False and again["code"] == "already_replied"

    (tmp_path / "r").mkdir()
    running = _drafting_job(tmp_path / "r", ["running"])
    out = _call(
        _broker(tmp_path / "r", FakeMailbox()), "msgraph_reply_bind", {"kind": "drafting_job", "job_id": running}
    )
    assert out["bound"] is False and out["code"] == "not_ended"
    assert _rows(_broker(tmp_path / "r", FakeMailbox()), "REPLY_BINDING")
