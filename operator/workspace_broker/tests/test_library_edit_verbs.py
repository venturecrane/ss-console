"""A firm's own save of its accepted library file, handed to the root writer
(library_edit_verbs.py), and the one sentence the firm could read about it,
behind ``logic_in_code.library_edit_notice`` (off on every seat). Made-up firm."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import delivered_matter, library_setup_wording as W
from workspace_broker.tests.test_library_setup_choose import _intake_answers, _store
from workspace_broker.tests.test_library_setup_verbs import call, seat  # noqa: F401 - the fixture, used by name

DATA = b"the cleaned bytes the job kept"


def _acc(**over):
    acc = {
        "kind": "mediation_brief",
        "attorney": None,
        "file_id": "lib-1",
        "version": "lib-1-v2",
        "contract": "marked",
        "accepted_by": "firm_made",
        "accepted_at": "2026-11-07T17:00:00Z",
        "sha256": hashlib.sha256(DATA).hexdigest(),
        "fixed_strings": ["Plaintiff's Mediation Brief"],
        "replaces": {"file_id": "lib-1", "version": "lib-1-v1", "sha256": "a" * 64},
        "chart_source": None,
    }
    return {**acc, **over}


def _submit(broker, acc=None, data=DATA, **over):
    req = {"lane": "drafting", "job_id": "j1", "acceptance": acc or _acc(), "data_b64": base64.b64encode(data).decode()}
    return call(broker, "library_edit_submit", pid=1, uid=0, **{**req, **over})


def test_a_firm_save_is_spooled_in_the_writers_shape_and_its_answer_read(seat, tmp_path):
    broker, *_ = seat
    broker.establishment = _store(tmp_path, broker)
    out = _submit(broker)
    run = broker.establishment.runs_dir / out["run_id"]
    assert (run / "file.docx").read_bytes() == DATA
    sub = json.loads((run / "submission.json").read_text())
    assert (sub["scope"], sub["phase"], sub["run_id"]) == ("library_accept", "install", out["run_id"])
    assert sub["acceptance"] == _acc()
    assert call(broker, "library_edit_status", pid=1, uid=0, run_id=out["run_id"])["status"] == "pending"
    _intake_answers(broker.establishment, out["run_id"], "installed")
    done = call(broker, "library_edit_status", pid=1, uid=0, run_id=out["run_id"])
    assert done["status"] == "complete" and done["result"]["status"] == "installed"


@pytest.mark.parametrize(
    "acc, data, over",
    [
        (_acc(accepted_by="admin@firm.example"), DATA, {}),
        (_acc(accepted_by="layout_rule:"), DATA, {}),
        (_acc(accepted_by="layout_rule:a b"), DATA, {}),
        (_acc(contract="prototypes"), DATA, {}),
        (_acc(replaces={"file_id": "lib-9", "version": "v1", "sha256": "a" * 64}), DATA, {}),
        (_acc(version="lib-1-v1"), DATA, {}),
        (_acc(), b"other bytes", {}),
        ({k: v for k, v in _acc().items() if k != "fixed_strings"}, DATA, {}),
        (_acc(), DATA, {"lane": "litigation"}),
    ],
)
def test_each_refusal_spools_nothing(seat, tmp_path, acc, data, over):
    broker, *_ = seat
    broker.establishment = _store(tmp_path, broker)
    with pytest.raises(Exception):
        _submit(broker, acc, data, **over)
    assert not list(broker.establishment.runs_dir.iterdir())


def test_a_version_code_wrote_with_the_firms_layout_rules_is_spooled_the_same_way(seat, tmp_path):
    """Design piece 5 (``jobs/drafting/layout_into_library``, behind
    ``logic_in_code.layout_into_library``): the ledger names the version code
    wrote with the rules in it, which is how a later firm edit is placed."""
    broker, *_ = seat
    broker.establishment = _store(tmp_path, broker)
    acc = _acc(accepted_by="layout_rule:0000000a,0000000b")
    out = _submit(broker, acc)
    sub = json.loads((broker.establishment.runs_dir / out["run_id"] / "submission.json").read_text())
    assert sub["acceptance"]["accepted_by"] == "layout_rule:0000000a,0000000b"


def test_only_root_may_hand_a_save_over(seat, tmp_path):
    broker, *_ = seat
    broker.establishment = _store(tmp_path, broker)
    with pytest.raises(Exception):
        call(broker, "library_edit_submit", lane="drafting", job_id="j1", acceptance=_acc(), data_b64="")
    assert not list(broker.establishment.runs_dir.iterdir())


# ---- the sentence, behind its switch ------------------------------------------------------------


def _row(used: bool | None, **over):
    delivery = {"files": [{"role": "draft", "name": "Brief.docx"}]}
    if used is not None:
        delivery["library_edit"] = {"used": used, "code": "" if used else "words_changed"}
    return {"state": "delivered", "document_class": "mediation_brief", "matter_number": "1001",
            "delivery_json": json.dumps(delivery), **over}  # fmt: skip


SENTENCE = W.say("edit_not_used", one="mediation brief", many="mediation briefs")


@pytest.fixture
def notice(tmp_path, monkeypatch):
    path = tmp_path / "customer.yaml"
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))

    def set_(mode: str | None) -> None:
        path.write_text(f"logic_in_code:\n  library_edit_notice: {mode}\n" if mode else "client: x\n")

    return set_


@pytest.mark.parametrize("mode", [None, "observe"])
def test_off_the_firm_is_told_nothing_about_its_save(notice, mode):
    notice(mode)
    assert not W.edit_notice_on()
    assert W.edit_not_used(_row(False), notice=W.edit_notice_on()) == ""
    assert SENTENCE not in delivered_matter.drafting(_row(False))


def test_on_a_save_not_used_is_said_in_one_sentence_and_a_used_one_says_nothing(notice):
    notice("enforce")
    assert W.edit_notice_on()
    assert SENTENCE in delivered_matter.drafting(_row(False))
    assert SENTENCE not in delivered_matter.drafting(_row(True))
    assert SENTENCE not in delivered_matter.drafting(_row(None))
    demand = W.edit_not_used(_row(False), notice=True, kind="demand_letter")
    assert demand == W.say("edit_not_used", one="demand letter", many="demand letters")


def test_a_reply_with_no_save_considered_is_the_reply_it_was(notice):
    """With nothing accepted (every seat) no delivery carries ``library_edit``,
    and the reply is byte for byte what it was, whatever the switch says."""
    notice("enforce")
    on = delivered_matter.drafting(_row(None))
    notice(None)
    assert on == delivered_matter.drafting(_row(None))
    assert delivered_matter.library_edit_line(_row(None)) == ""


def test_an_unreadable_switch_says_nothing(notice, tmp_path):
    (tmp_path / "customer.yaml").write_text("logic_in_code: [not, a, mapping]\n")
    assert not W.edit_notice_on()
