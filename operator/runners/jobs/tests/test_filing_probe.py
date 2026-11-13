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


def test_the_committed_probe_refuses_until_its_matter_is_filled_in():
    """The lead commits the fictional matter's id after reading it off the
    staging tenant; until then the probe cannot file anywhere."""
    assert probe.PROBE_MATTER_ID == ""
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
    assert any(line.startswith("teardown: the deletes stopped") for line in said)
    assert any("still read present: (the matter could not be read" in line for line in said), said
