"""The staging filing probe (``jobs/filing_probe.py``): it refuses anywhere but
SMD's staging twin, files through the one filing module on a recording seat,
sends nothing on its second runs, and fails while a stamped file remains."""

from __future__ import annotations

from typing import Any

import pytest

from filing_kit import RecordingSeat
from jobs import filing_probe as probe, seat_config


@pytest.fixture
def matter(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(probe, "PROBE_MATTER_ID", "00000000-0000-4000-8000-0000000000fe")
    return probe.PROBE_MATTER_ID


def _client_seat(environment: str) -> Any:
    """A real ``ClientSeat`` over a real ``SmokeballClient`` on ``environment``
    (made-up credentials; nothing here makes a request)."""
    from smokeball_connector.client import SmokeballClient

    from jobs.seat import ClientSeat

    client = SmokeballClient(region="us", environment=environment, client_id="x", client_secret="y", api_key="z")
    return ClientSeat(client)


def test_the_probe_refuses_while_its_matter_is_empty(monkeypatch):
    """MUTATION: drop the check and an empty id reaches the filing."""
    assert probe.PROBE_MATTER_ID, "the committed fictional matter is filled in"
    monkeypatch.setattr(probe, "PROBE_MATTER_ID", "")
    assert "PROBE_MATTER_ID is empty" in probe.refusal("ap-staging")


@pytest.mark.parametrize(
    "slug,environment,why",
    [
        (None, "staging", "not on the probe's allowlist"),
        ("", "staging", "not on the probe's allowlist"),
        ("ashton-price", "staging", "not on the probe's allowlist"),
        ("ap-staging", "production", "not 'staging'"),
        ("ap-staging", None, "not 'staging'"),
    ],
)
def test_it_refuses_a_client_seat_and_a_production_client(matter, slug, environment, why):
    """MUTATION: drop either check and a client seat's matter could be written."""
    assert why in probe.refusal(slug, environment)


def test_on_staging_with_its_matter_it_may_file(matter):
    assert probe.refusal("ap-staging", "staging") == ""


def test_a_seat_off_the_allowlist_is_refused_before_any_client_is_built(monkeypatch, capsys, matter):
    monkeypatch.setenv(seat_config.SLUG_VAR, "ashton-price")

    def no_client(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a refused probe must not build a client")

    monkeypatch.setattr("jobs.seat.ClientSeat", no_client)
    assert probe.main([]) == 2
    assert "REFUSED: the seat 'ashton-price'" in capsys.readouterr().err


def test_a_client_on_production_is_refused_before_anything_is_filed(monkeypatch, capsys, matter):
    monkeypatch.setenv(seat_config.SLUG_VAR, "ap-staging")
    on_production = _client_seat("production")
    monkeypatch.setattr("jobs.seat.ClientSeat", lambda: on_production)
    monkeypatch.setattr(probe, "run", lambda *_a, **_k: pytest.fail("a production client must never reach the probe"))
    assert probe.main([]) == 2
    assert "is on 'production', not 'staging'" in capsys.readouterr().err


def test_both_paths_are_proven_the_second_runs_send_nothing_and_every_stamped_file_is_deleted(matter):
    seat = RecordingSeat()
    said: list[str] = []
    assert probe.run(seat, said.append, gone_tries=2, gone_pause=0) == 0, said
    assert seat.writes.get("add_file") == 3, "two dated files and one standing file, each sent once"
    assert sum(line.startswith("deleting ") for line in said) == 3
    assert not [d for d in seat.docs if not d.get("deleted")], "nothing stamped is left on the matter"
    assert any("PROVEN, second run sent nothing" in line for line in said)


def test_a_stamped_file_that_still_reads_present_fails_the_probe(matter):
    """MUTATION: report a clean teardown without polling and this passes a
    probe that left a file on the staging matter."""
    seat = RecordingSeat()
    seat.delete_file = lambda matter_id, file_id: {"href": "accepted"}  # the delete is accepted and never happens
    said: list[str] = []
    assert probe.run(seat, said.append, gone_tries=2, gone_pause=0) == 1
    assert any(line.startswith("FAILED: 3 stamped file(s) still read present") for line in said)


def test_a_path_that_raises_fails_the_probe_and_still_tears_down(matter):
    seat = RecordingSeat()
    seat.list_fails_after_raise = True
    seat.write_fault = "not_landed"  # the first send fails outright
    said: list[str] = []
    assert probe.run(seat, said.append, gone_tries=2, gone_pause=0) == 1
    assert any(line.startswith("dated folder: FAILED") for line in said)
    assert any(line.startswith("teardown: the matter could not be read") for line in said)
    assert any("still read present: (the matter could not be read" in line for line in said), said


STAMP = "[SMD-PROBE 20261113T000000 a1b2c3]"


def _row(fid: str, what: str, stamp: str = STAMP) -> dict:
    return {"id": fid, "name": f"{stamp} {what}", "ext": ".txt", "size": 1, "folderId": "f"}


def test_a_file_of_its_own_the_listing_shows_only_late_is_deleted_too():
    """MUTATION: delete only what the first pass listed and a file the vendor
    indexed a minute later stays on the staging matter while the probe passes."""
    seat = RecordingSeat()
    seat.docs.append(_row("early", "early"))
    seat._pending.append((seat._lists + 2, _row("late", "late")))  # the vendor indexes it only on the second listing
    said: list[str] = []
    assert probe.teardown(seat, STAMP, {"early", "late"}, said.append, tries=4, pause=0) == []
    assert seat.deleted == ["early", "late"]


def test_a_delete_that_fails_is_tried_again_on_the_next_poll():
    """MUTATION: mark an id asked before its delete returns and a failed delete
    is never retried."""
    seat = RecordingSeat()
    seat.docs.append(_row("mine", "dated 1"))
    real, fails = seat.delete_file, [RuntimeError("503 from the vendor")]

    def flaky(matter_id: str, file_id: str) -> dict:
        if fails:
            raise fails.pop()
        return real(matter_id, file_id)

    seat.delete_file = flaky
    said: list[str] = []
    assert probe.teardown(seat, STAMP, {"mine"}, said.append, tries=3, pause=0) == []
    assert seat.deleted == ["mine"] and any("failed" in line and "trying again" in line for line in said)


def test_only_its_own_files_are_deleted_and_a_stranger_with_its_stamp_fails_it():
    """MUTATION: delete by stamp and a file this run never sent (another run's,
    a person's) is deleted off the matter."""
    seat = RecordingSeat()
    seat.docs += [
        _row("mine", "dated 1"),
        _row("theirs", "copied by hand"),
        _row("other", "x", "[SMD-PROBE other run]"),
    ]
    said: list[str] = []
    left = probe.teardown(seat, STAMP, {"mine"}, said.append, tries=2, pause=0)
    assert seat.deleted == ["mine"]
    assert left == [f"{STAMP} copied by hand.txt"]


def test_every_run_has_its_own_stamp():
    assert len({probe._stamp() for _ in range(50)}) == 50


def test_the_ids_come_from_its_own_send_records(tmp_path):
    (tmp_path / "dated" / "runs" / "probe").mkdir(parents=True)
    (tmp_path / "standing").mkdir()
    (tmp_path / "dated" / "runs" / "probe" / "delivery.json").write_text(
        '{"files": [{"name": "a", "file_id": "d1"}, {"name": "b", "pending": true}]}'
    )
    (tmp_path / "standing" / "delivery.json").write_text('{"name": "c", "file_id": "s1"}')
    assert probe.recorded_ids(tmp_path) == {"d1", "s1"}
