"""arrivals: lane-scoped new-document detection shared by the runner lanes."""

from __future__ import annotations

import pytest

from medchron import arrivals as A


class _Seat:
    def __init__(self, files: dict[str, list[dict]]):
        self.files = files
        self.calls: list[str] = []

    def list_files(self, matter_id: str) -> list[dict]:
        self.calls.append(matter_id)
        return self.files[matter_id]


def _f(fid: str, mod: str, name: str = "doc.pdf", deleted: bool = False) -> dict:
    return {"id": fid, "modified": mod, "name": name, "deleted": deleted}


def test_no_cursor_returns_every_live_file(tmp_path):
    seat = _Seat({"m1": [_f("a", "1"), _f("b", "1", deleted=True)]})
    assert A.arrivals(seat, "lit", ["m1"], data=tmp_path) == {"m1": [_f("a", "1")]}


def test_only_new_or_modified_after_commit(tmp_path):
    files = [_f("a", "1"), _f("b", "1")]
    A.commit("lit", "m1", files, data=tmp_path)
    seat = _Seat({"m1": [_f("a", "1"), _f("b", "2"), _f("c", "1")]})
    got = A.arrivals(seat, "lit", [{"id": "m1"}], data=tmp_path)
    assert [f["id"] for f in got["m1"]] == ["b", "c"]


def test_unchanged_matter_is_omitted(tmp_path):
    files = [_f("a", "1")]
    A.commit("lit", "m1", files, data=tmp_path)
    assert A.arrivals(_Seat({"m1": files}), "lit", ["m1"], data=tmp_path) == {}


def test_lanes_do_not_share_a_cursor(tmp_path):
    files = [_f("a", "1")]
    A.commit("lit", "m1", files, data=tmp_path)
    assert A.arrivals(_Seat({"m1": files}), "negotiation", ["m1"], data=tmp_path) == {"m1": files}


def test_select_filters_and_listings_skip_the_seat(tmp_path):
    seat = _Seat({})
    rows = [_f("a", "1", "Offer letter.pdf"), _f("b", "1", "bill.pdf")]
    got = A.arrivals(seat, "neg", ["m1"], select=lambda f: "Offer" in f["name"], data=tmp_path, listings={"m1": rows})
    assert [f["id"] for f in got["m1"]] == ["a"]
    assert seat.calls == []


def test_unparseable_cursor_raises_rather_than_replaying(tmp_path):
    p = A.lane_dir("lit", tmp_path) / "manifest" / "m1.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(A.ArrivalsError):
        A.arrivals(_Seat({"m1": [_f("a", "1")]}), "lit", ["m1"], data=tmp_path)


@pytest.mark.parametrize("lane", ["", "Lit", "../x", "a/b"])
def test_lane_name_is_a_plain_identifier(tmp_path, lane):
    with pytest.raises(A.ArrivalsError):
        A.lane_dir(lane, tmp_path)


def test_matter_id_cannot_escape_the_lane_dir(tmp_path):
    with pytest.raises(A.ArrivalsError):
        A.commit("lit", "../m1", [], data=tmp_path)


def test_paged_reads_until_a_short_page():
    pages = [[{"id": str(i)} for i in range(A.PAGE)], [{"id": "x"}]]
    calls: list[int] = []

    def get(path, Limit, Offset, **_):
        calls.append(Offset)
        return {"value": pages[len(calls) - 1]}

    assert len(A.paged(get, "/matters", pause=0)) == A.PAGE + 1
    assert calls == [0, A.PAGE]


def test_paged_refuses_an_unparsed_page():
    with pytest.raises(A.ArrivalsError):
        A.paged(lambda *a, **k: {"oops": 1}, "/matters", pause=0)
