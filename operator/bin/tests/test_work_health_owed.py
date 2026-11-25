"""Work SMD owes a firm person, listed until SMD closes it (owner's ruling,
2026-11-19; ``bin/lib/work_health_owed.py``). Every value is invented."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from bin.lib import work_health as wh
from bin.lib import work_health_mail as mail
from bin.lib import work_health_owed as owed_wh
from bin.lib import work_health_proof as proof
from bin.lib import work_health_sources as src

UTC = timezone.utc
NOW = datetime(2026, 11, 19, 18, 0, tzinfo=UTC)
CFG = wh.load_config()
SEAT = "pilot-example"
OWED = "01JOWEDAAAAAAAAAAAAAAAAAAA"
REF = "<CAreq1@mail.firm.example>"


def _row(action: str, at: datetime, **md) -> dict:
    return {
        "id": f"{action}-{at.isoformat()}-{len(md)}",
        "ts": at.isoformat().replace("+00:00", "Z"),
        "action_type": action,
        "metadata": json.dumps(md),
    }


def _owed(at: datetime, kind: str = "drafting_refused") -> dict:
    return _row(
        "DELIVERY_PROOF_FAILED",
        at,
        lane="drafting",
        job_id=OWED,
        attempt=1,
        code="smd_owed",
        kind=kind,
        request_ref=REF,
        detail="memo for matter 4417 refused on our side: filing_target",
    )


def _all(rows: list[dict], closed=None) -> list[wh.Item]:
    """Every reader the hourly run applies that could see these rows."""
    return (
        wh.request_items(SEAT, rows, CFG, NOW, closed)
        + src.could_not_items(SEAT, rows, CFG, NOW, closed)
        + proof.delivery_proof_items(SEAT, rows, CFG, NOW, closed)
        + owed_wh.smd_owed_items(SEAT, rows, CFG, NOW, closed)
    )


def test_an_owed_item_survives_the_acknowledgment_a_later_reply_and_a_later_ok_call() -> None:
    """FALSIFIER: clear it on answered_after or a retried ok call and the
    attorney, told it is with us, waits on work nobody owns."""
    at = NOW - timedelta(days=3)
    rows = [
        _row("INBOUND_RECEIVED", at - timedelta(seconds=5), vendor_message_id="v-1", trust_class="internal"),
        _owed(at),
        _row("REPLY_SENT", at + timedelta(seconds=20), in_reply_to="v-1"),
        _row("INBOUND_RECEIVED", at + timedelta(hours=2), vendor_message_id="v-2", trust_class="internal"),
        _row("REPLY_SENT", at + timedelta(hours=2, seconds=30), in_reply_to="v-2"),
        _row("TOOL_CALL", at + timedelta(hours=3), tool="drafting_job_submit", status="ok"),
    ]
    items = _all(rows)
    (owed,) = [i for i in items if i.job == OWED]
    assert "refused on our side" in owed.what and REF in owed.what and "open-job" in owed.todo
    # No second line for the same row (the delivery-proof reader skips it).
    assert len([i for i in items if OWED in i.key]) == 1
    # Emailed once: listed again next hour, as still open, never new.
    previous = {mail.state_key(owed.key): (NOW - timedelta(hours=1)).isoformat()}
    plan = mail.decide(
        items=[owed],
        seats=[SEAT],
        gaps={},
        previous=previous,
        since=NOW - timedelta(hours=1),
        now=NOW,
        all_clear_hour_utc=15,
    )
    assert plan.new == [] and plan.still_open == [owed]


def test_smd_closing_it_or_a_hand_delivery_drops_it() -> None:
    rows = [_owed(NOW - timedelta(days=1))]
    assert owed_wh.smd_owed_items(SEAT, rows, CFG, NOW, {(SEAT, OWED)}) == []
    proven = _row("DELIVERY_PROVEN", NOW - timedelta(hours=1), lane="drafting", job_id=OWED)
    assert owed_wh.smd_owed_items(SEAT, [*rows, proven], CFG, NOW) == []


def test_a_help_question_is_listed_in_its_own_words() -> None:
    for kind, words in (
        ("help_question", "help guide does not answer"),
        ("rule_not_recorded", "not recorded"),
        ("rule_install_failed", "did not install"),
        ("look_into", "did not finish"),
    ):
        (it,) = owed_wh.smd_owed_items(SEAT, [_owed(NOW - timedelta(hours=1), kind)], CFG, NOW)
        assert words in it.what and "note lane" in it.todo


def test_an_addition_to_a_stopped_draft_tells_staff_to_make_it_by_hand() -> None:
    """Owner's ruling, 2026-11-20: the requester was given the revise
    acknowledgment, so the line names the ended job and how to deliver it."""
    job = "01JENDEDJOBAAAAAAAAAAAAAAA"
    row = {
        "id": OWED,
        "created_at": (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "kind": "drafting_addition",
        "request_ref": REF,
        "document_class": "memo",
        "matter_number": "4417",
        "refusal_code": "",
        "detail": f"addition to the memo for matter 4417; job {job} had ended failed",
    }
    (it,) = owed_wh.smd_owed_items(SEAT, [], CFG, NOW, open_owed={"owed": [row], "owed_jobs": []})
    assert "added to a draft whose job had stopped" in it.what and REF in it.what
    assert f"jobs.drafting.deliver_by_smd {job}" in it.todo and "file the new version" in it.todo
    assert it.job == OWED


def test_with_nothing_recorded_the_unsent_request_is_listed_unanswered() -> None:
    """Broker down: the overlay sends nothing (say none), the relay holds any
    draft, and the request shows as held, or as no reply when nothing was drafted."""
    at = NOW - timedelta(hours=1)
    inbound = _row("INBOUND_RECEIVED", at, vendor_message_id="v-3", trust_class="internal")
    held = _row("REPLY_HELD", at + timedelta(seconds=30), message_id="v-3", reason="duplicate_reply")
    assert [i.kind for i in wh.request_items(SEAT, [inbound, held], CFG, NOW)] == ["reply_held"]
    assert [i.kind for i in wh.request_items(SEAT, [inbound], CFG, NOW)] == ["no_reply"]


class _Seam:
    """The seat's runtime read, answering one page for ``smd_owed``."""

    def __init__(self, page=None, error: Exception | None = None) -> None:
        self.page, self.error, self.kinds = page, error, []

    def read_page(self, kind, **_):
        self.kinds.append(kind)
        if self.error is not None:
            raise self.error
        return self.page


def test_an_owed_item_older_than_the_ledger_window_is_still_listed() -> None:
    """Review 2026-11-19: owed work fell off the list after the 192-hour
    lookback. FALSIFIER: read only the audit window."""
    old = (NOW - timedelta(days=30)).isoformat()
    page = {
        "entries": [{"id": OWED, "created_at": old, "kind": "help_question", "request_ref": REF, "detail": "q"}],
        "owed_jobs": [{"id": "01JJOB", "state": "delivered"}],
        "cursor": None,
    }
    seam = _Seam(page)
    open_owed = owed_wh.read_open_owed(SEAT, seam)
    assert seam.kinds == ["smd_owed"] and open_owed["owed_jobs"] == [{"id": "01JJOB", "state": "delivered"}]
    (it,) = owed_wh.smd_owed_items(SEAT, [], CFG, NOW, None, open_owed)
    assert it.job == OWED and "help guide does not answer" in it.what
    assert owed_wh.smd_owed_items(SEAT, [], CFG, NOW, {(SEAT, OWED)}, open_owed) == []


def test_a_failed_open_read_falls_back_to_the_window_never_to_nothing_owed() -> None:
    assert owed_wh.read_open_owed(SEAT, _Seam(error=RuntimeError("down"))) is None
    assert owed_wh.read_open_owed(SEAT, _Seam({"entries": [], "unavailable": True})) is None
    (it,) = owed_wh.smd_owed_items(SEAT, [_owed(NOW - timedelta(hours=2))], CFG, NOW, None, None)
    assert it.job == OWED
    assert owed_wh.owed_jobs_kwargs(None) == {}


def test_owed_jobs_are_passed_only_to_a_reader_that_takes_them() -> None:
    open_owed = {"owed": [], "owed_jobs": [{"id": "j"}]}

    def takes(seat, rows, cfg, now, closed=None, owed_jobs=None):
        return []

    def older(seat, rows, cfg, now, closed=None):
        return []

    assert owed_wh.owed_jobs_kwargs(open_owed, takes) == {"owed_jobs": [{"id": "j"}]}
    assert owed_wh.owed_jobs_kwargs(open_owed, older) == {}
