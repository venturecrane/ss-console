"""create_event puts the matter's responsible attorney on every matter event.

On 2026-09-24 the Operator added two deadline events to a client matter with
attendees copied from an existing event that carried only the paralegal, so
the responsible attorney never saw them on his calendar. The firm asked that
every date the Operator adds be on the responsible attorney's calendar.
``create_event`` now reads the matter and unions ``personResponsible.id`` into
the attendees: caller order kept, appended when missing, never duplicated,
and fail-open (a failed read or no responsible person sends the caller's list
unchanged and still POSTs). ``update_event`` is untouched.
"""

from __future__ import annotations

from typing import Any

import pytest

from smokeball_connector import server
from smokeball_connector.server import create_event

ATTORNEY = "staff-attorney"
PARALEGAL = "staff-paralegal"


class _FakeClient:
    def __init__(self, matter: Any = None, raise_on_get: bool = False) -> None:
        self.matter = matter
        self.raise_on_get = raise_on_get
        self.gets: list[str] = []
        self.posts: list[dict] = []

    def get(self, path: str, **_: Any) -> Any:
        self.gets.append(path)
        if self.raise_on_get:
            raise RuntimeError("matter read failed")
        return self.matter

    def request(self, method: str, path: str, json: dict | None = None) -> dict:
        assert (method, path) == ("POST", "/events")
        self.posts.append(json or {})
        return {"id": "evt-1"}


def _install(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> _FakeClient:
    monkeypatch.setattr(server, "_get_client", lambda: client)
    return client


def _create(attendees: list[str], matter_id: str | None = "m-1") -> None:
    create_event(
        subject="Discovery responses due",
        start_time="2026-10-20",
        end_time="2026-10-20",
        attendees=attendees,
        time_zone="America/Phoenix",
        matter_id=matter_id,
        all_day=True,
    )


def _matter(staff_id: str | None) -> dict:
    m: dict[str, Any] = {"id": "m-1", "number": "2026-PI-001"}
    if staff_id is not None:
        m["personResponsible"] = {"id": staff_id, "href": "x", "rel": "Staff"}
    return m


def test_attorney_is_appended_after_the_callers_attendees(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _install(monkeypatch, _FakeClient(_matter(ATTORNEY)))
    _create([PARALEGAL])
    assert client.gets == ["/matters/m-1"]
    assert len(client.posts) == 1
    assert client.posts[0]["attendees"] == [PARALEGAL, ATTORNEY]


def test_attorney_already_present_is_not_duplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _install(monkeypatch, _FakeClient(_matter(ATTORNEY)))
    _create([ATTORNEY, PARALEGAL])
    assert client.posts[0]["attendees"] == [ATTORNEY, PARALEGAL]


def test_flat_person_responsible_staff_id_is_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _install(monkeypatch, _FakeClient({"id": "m-1", "personResponsibleStaffId": ATTORNEY}))
    _create([PARALEGAL])
    assert client.posts[0]["attendees"] == [PARALEGAL, ATTORNEY]


def test_no_matter_id_leaves_attendees_unchanged_and_reads_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _install(monkeypatch, _FakeClient(_matter(ATTORNEY)))
    _create([PARALEGAL], matter_id=None)
    assert client.gets == []
    assert client.posts[0]["attendees"] == [PARALEGAL]


def test_matter_without_person_responsible_leaves_attendees_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _install(monkeypatch, _FakeClient(_matter(None)))
    _create([PARALEGAL])
    assert client.gets == ["/matters/m-1"]
    assert client.posts[0]["attendees"] == [PARALEGAL]


def test_a_failed_matter_read_still_posts_with_the_callers_attendees(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _install(monkeypatch, _FakeClient(raise_on_get=True))
    _create([PARALEGAL])
    assert client.gets == ["/matters/m-1"]
    assert len(client.posts) == 1
    assert client.posts[0]["attendees"] == [PARALEGAL]


def test_the_callers_list_is_not_mutated(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _FakeClient(_matter(ATTORNEY)))
    mine = [PARALEGAL]
    _create(mine)
    assert mine == [PARALEGAL]


def test_attendees_are_still_required_even_with_a_matter(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _install(monkeypatch, _FakeClient(_matter(ATTORNEY)))
    with pytest.raises(ValueError, match="at least one attendee"):
        _create([])
    assert client.gets == [] and client.posts == []
