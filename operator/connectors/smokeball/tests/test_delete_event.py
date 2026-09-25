"""prepare_event_deletion / delete_events: the manifest is read from the vendor,
and at confirm time every entry is re-verified against the live calendar before
it is deleted. No live Smokeball calls; a scripted fake stands in for the
vendor, following the published DeleteEvent shape (202 + Link, async; 404 when
absent; GET /events/{id} carries isDeleted)."""

from __future__ import annotations

import pytest

from smokeball_connector import event_delete, server
from smokeball_connector.client import SmokeballApiError
from smokeball_connector.event_delete import EventDeletionRefused

M1 = "8d7c2a4e-1f3b-4c5d-9e6f-0a1b2c3d4e5f"
M2 = "11111111-2222-3333-4444-555555555555"


def _ev(eid: str, subject: str = "Old deadline", start: str = "2026-01-05T00:00:00Z", kind: str = "Normal") -> dict:
    return {"id": eid, "subject": subject, "startTime": start, "type": kind}


class _Fake:
    """Calendars per matter; deletes mark events deleted after ``lag`` reads."""

    def __init__(self, calendars: dict[str, list[dict]], numbers: dict[str, str] | None = None, lag: int = 0) -> None:
        self.calendars = {m: list(rows) for m, rows in calendars.items()}
        self.numbers = numbers or {M1: "200213", M2: "200214"}
        self.lag = lag
        self.deleted: dict[str, int] = {}  # event id -> reads until it shows deleted
        self.refuse: dict[str, int] = {}  # event id -> HTTP status the DELETE answers
        self.calls: list[tuple] = []

    def get(self, path: str, **params):
        self.calls.append(("GET", path, params))
        if path == "/events":
            rows = self.calendars.get(params.get("MatterId"), [])
            off, lim = params.get("Offset", 0), params.get("Limit", 500)
            return {"value": rows[off : off + lim]}
        if path.startswith("/matters/"):
            return {"id": path.split("/")[-1], "number": self.numbers.get(path.split("/")[-1])}
        eid = path.split("/")[-1]
        if eid in self.deleted:
            if self.deleted[eid] <= 0:
                return {"id": eid, "isDeleted": True}
            self.deleted[eid] -= 1
        return {"id": eid, "isDeleted": False}

    def request(self, method: str, path: str, *, json=None):
        self.calls.append((method, path, json))
        eid = path.split("/")[-1]
        if eid in self.refuse:
            raise SmokeballApiError(method, path, self.refuse[eid], "")
        self.deleted[eid] = self.lag
        for rows in self.calendars.values():
            rows[:] = [r for r in rows if r["id"] != eid]
        return {"id": eid, "href": path, "relation": "self", "method": "GET"}

    def deletes(self) -> list[str]:
        return [c[1].split("/")[-1] for c in self.calls if c[0] == "DELETE"]


def _nosleep(_s: float) -> None:
    return None


# ---- prepare ----------------------------------------------------------------
def test_prepare_reads_vendor_facts_and_deletes_nothing() -> None:
    fake = _Fake({M1: [_ev("e2", start="2026-03-01T00:00:00Z"), _ev("e1")]})
    out = event_delete.prepare(fake, [M1])
    assert [e["event_id"] for e in out["events"]] == ["e1", "e2"]  # date order
    assert out["events"][0] == {
        "event_id": "e1",
        "matter_id": M1,
        "matter_number": "200213",
        "subject": "Old deadline",
        "start_time": "2026-01-05T00:00:00Z",
    }
    assert fake.deletes() == []
    listing = [c for c in fake.calls if c[1] == "/events"][0]
    assert listing[2]["MatterId"] == M1 and listing[2]["ExcludeDeletedEvents"] is True


def test_prepare_skips_recurring_and_unknown_ids() -> None:
    fake = _Fake({M1: [_ev("e1"), _ev("r1", kind="Pattern")]})
    out = event_delete.prepare(fake, [M1], ["e1", "r1", "nope"])
    assert [e["event_id"] for e in out["events"]] == ["e1"]
    reasons = {s["event_id"]: s["reason"] for s in out["skipped"]}
    assert "recurring" in reasons["r1"] and "not among" in reasons["nope"]


def test_prepare_caps_the_manifest_and_says_how_many_remain() -> None:
    rows = [_ev(f"e{i:03d}", start=f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z") for i in range(60)]
    out = event_delete.prepare(_Fake({M1: rows}), [M1])
    assert len(out["events"]) == event_delete.MAX_EVENTS_PER_ACT
    assert out["remaining"] == 60 - event_delete.MAX_EVENTS_PER_ACT


def test_prepare_pages_past_500() -> None:
    rows = [_ev(f"f{i:04d}") for i in range(event_delete._PAGE)] + [_ev("last")]
    fake = _Fake({M1: rows})
    out = event_delete.prepare(fake, [M1], ["last"])
    assert [e["event_id"] for e in out["events"]] == ["last"]
    offsets = [c[2]["Offset"] for c in fake.calls if c[1] == "/events"]
    assert offsets == [0, event_delete._PAGE]


@pytest.mark.parametrize("bad", [[], None, "x", ["10006"], [""]])
def test_prepare_refuses_bad_matter_lists_before_any_call(bad) -> None:
    fake = _Fake({})
    with pytest.raises(EventDeletionRefused):
        event_delete.prepare(fake, bad)
    assert fake.calls == []


# ---- delete_set --------------------------------------------------------------
def test_confirmed_manifest_deletes_and_reports_the_read_back() -> None:
    fake = _Fake({M1: [_ev("e1"), _ev("e2")], M2: [_ev("e3", subject="Trial")]})
    manifest = event_delete.prepare(fake, [M1, M2])["events"]
    out = event_delete.delete_set(fake, manifest, sleep=_nosleep)
    assert sorted(fake.deletes()) == ["e1", "e2", "e3"]
    assert out["counts"] == {"proposed": 3, "deleted": 3, "pending": 0, "skipped": 0, "failed": 0}
    assert all(d["readback"]["isDeleted"] is True for d in out["deleted"])
    assert out["ref"] == "deleted=3 pending=0 skipped=0 failed=0"


def test_async_delete_is_polled_and_never_resent() -> None:
    fake = _Fake({M1: [_ev("e1")]}, lag=2)
    out = event_delete.delete_set(fake, event_delete.prepare(fake, [M1])["events"], sleep=_nosleep)
    assert out["counts"]["deleted"] == 1 and fake.deletes() == ["e1"]


def test_still_live_after_every_poll_is_pending() -> None:
    fake = _Fake({M1: [_ev("e1")]}, lag=99)
    out = event_delete.delete_set(fake, event_delete.prepare(fake, [M1])["events"], sleep=_nosleep)
    assert [p["event_id"] for p in out["pending"]] == ["e1"]
    assert fake.deletes() == ["e1"]


def test_a_404_read_back_counts_as_gone() -> None:
    fake = _Fake({M1: [_ev("e1")]})
    manifest = event_delete.prepare(fake, [M1])["events"]

    def gone(path: str, **params):
        if path == "/events/e1":
            raise SmokeballApiError("GET", path, 404, "")
        return _Fake.get(fake, path, **params)

    fake.get = gone  # type: ignore[method-assign]
    out = event_delete.delete_set(fake, manifest, sleep=_nosleep)
    assert out["deleted"][0]["readback"] == {"status": 404}


def test_changed_moved_and_vanished_events_are_skipped_not_deleted() -> None:
    fake = _Fake({M1: [_ev("e1"), _ev("e2"), _ev("e3"), _ev("e4")], M2: []})
    manifest = event_delete.prepare(fake, [M1])["events"]
    # After the proposal and before the yes: e1's subject changes, e2's date
    # moves, e3 is moved to another matter, e4 is untouched.
    fake.calendars[M1] = [
        _ev("e1", subject="Renamed"),
        _ev("e2", start="2026-09-09T00:00:00Z"),
        _ev("e4"),
    ]
    fake.calendars[M2] = [_ev("e3")]
    out = event_delete.delete_set(fake, manifest, sleep=_nosleep)
    assert fake.deletes() == ["e4"]
    reasons = {s["event_id"]: s["reason"] for s in out["skipped"]}
    assert "subject changed" in reasons["e1"]
    assert "date changed" in reasons["e2"]
    assert "no longer among" in reasons["e3"]


def test_a_hand_composed_manifest_deletes_nothing() -> None:
    # The model invents a subject for a real event id on the right matter: the
    # administrator would have read the invented subject, so it must not match.
    fake = _Fake({M1: [_ev("e1")]})
    forged = [
        {
            "event_id": "e1",
            "matter_id": M1,
            "matter_number": "200213",
            "subject": "Lunch",
            "start_time": "2026-01-05T00:00:00Z",
        }
    ]
    out = event_delete.delete_set(fake, forged, sleep=_nosleep)
    assert fake.deletes() == [] and out["counts"]["skipped"] == 1


def test_an_event_on_another_matter_is_skipped() -> None:
    fake = _Fake({M1: [], M2: [_ev("e1")]})
    wrong = [
        {
            "event_id": "e1",
            "matter_id": M1,
            "matter_number": "200213",
            "subject": "Old deadline",
            "start_time": "2026-01-05T00:00:00Z",
        }
    ]
    out = event_delete.delete_set(fake, wrong, sleep=_nosleep)
    assert fake.deletes() == [] and "no longer among" in out["skipped"][0]["reason"]


def test_a_wrong_matter_number_is_skipped() -> None:
    fake = _Fake({M1: [_ev("e1")]})
    entry = event_delete.prepare(fake, [M1])["events"][0]
    out = event_delete.delete_set(fake, [{**entry, "matter_number": "999999"}], sleep=_nosleep)
    assert fake.deletes() == [] and "matter number" in out["skipped"][0]["reason"]


def test_recurring_event_is_never_deleted_even_if_listed() -> None:
    fake = _Fake({M1: [_ev("r1", kind="Occurrence")]})
    entry = {
        "event_id": "r1",
        "matter_id": M1,
        "matter_number": "200213",
        "subject": "Old deadline",
        "start_time": "2026-01-05T00:00:00Z",
    }
    out = event_delete.delete_set(fake, [entry], sleep=_nosleep)
    assert fake.deletes() == [] and "recurring" in out["skipped"][0]["reason"]


def test_a_vendor_refusal_is_reported_per_event_and_the_rest_proceed() -> None:
    fake = _Fake({M1: [_ev("e1"), _ev("e2")]})
    manifest = event_delete.prepare(fake, [M1])["events"]
    fake.refuse["e1"] = 403
    out = event_delete.delete_set(fake, manifest, sleep=_nosleep)
    assert [f["event_id"] for f in out["failed"]] == ["e1"]
    assert [d["event_id"] for d in out["deleted"]] == ["e2"]


def test_over_the_cap_refuses_before_any_call() -> None:
    fake = _Fake({M1: []})
    entry = {"event_id": "e", "matter_id": M1, "subject": "", "start_time": ""}
    too_many = [{**entry, "event_id": f"e{i}"} for i in range(event_delete.MAX_EVENTS_PER_ACT + 1)]
    with pytest.raises(EventDeletionRefused, match="cap"):
        event_delete.delete_set(fake, too_many, sleep=_nosleep)
    assert fake.calls == []


@pytest.mark.parametrize(
    "bad",
    [
        [],
        None,
        [{"event_id": "e1", "matter_id": "10006"}],
        [{"event_id": "../x", "matter_id": M1}],
        [{"event_id": "e1", "matter_id": M1}, {"event_id": "e1", "matter_id": M1}],
    ],
)
def test_malformed_manifests_refuse_before_any_call(bad) -> None:
    fake = _Fake({M1: []})
    with pytest.raises(EventDeletionRefused):
        event_delete.delete_set(fake, bad, sleep=_nosleep)
    assert fake.calls == []


def test_tools_use_the_servers_client(monkeypatch) -> None:
    fake = _Fake({M1: [_ev("e1")]})
    monkeypatch.setattr(server, "_get_client", lambda: fake)
    manifest = event_delete.prepare_event_deletion(matter_ids=[M1])["events"]
    assert event_delete.delete_set(fake, manifest, sleep=_nosleep)["counts"]["deleted"] == 1
