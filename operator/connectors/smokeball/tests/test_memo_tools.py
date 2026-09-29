"""File notes: plain, one per routine per matter, silent when unchanged (memo_tools.py)."""

from __future__ import annotations

import pytest

from smokeball_connector import memo_tools as mt
from smokeball_connector import server as srv
from smokeball_connector.task_update import MatterReferenceMismatch

M = "11111111-2222-3333-4444-555555555555"
GUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _nosleep(_s) -> None:
    return None


class _Fake:
    """A matter's memos. PUT/POST apply after ``lag`` reads of that memo, so a
    read-back can see the stale text first, as the vendor's 202 does."""

    def __init__(self, memos: list[dict] | None = None, lag: int = 0) -> None:
        self.memos = {m["id"]: dict(m) for m in (memos or [])}
        self.lag = lag
        self.pending: dict[str, tuple[int, str]] = {}
        self.calls: list[tuple[str, str, object]] = []

    def request(self, method, path, json=None, **_):
        self.calls.append((method, path, json))
        if method == "POST":
            new_id = f"new{len(self.memos)}"
            self.memos[new_id] = {"id": new_id, "plainText": "", "createdDate": "2026-09-29T15:00:00Z"}
            self.pending[new_id] = (self.lag, json["text"])
            return {"id": new_id, "href": f"https://x/memos/{new_id}"}
        if method == "PUT":
            memo_id = path.rsplit("/", 1)[1]
            self.pending[memo_id] = (self.lag, json["text"])
            return None  # 202, empty body
        raise AssertionError(method)

    def get(self, path, **params):
        self.calls.append(("GET", path, params))
        if path.endswith("/memos"):
            items = list(self.memos.values())
            off, lim = params.get("Offset", 0), params.get("Limit", 500)
            return {"value": items[off : off + lim]}
        memo_id = path.rsplit("/", 1)[1]
        if memo_id in self.pending:
            left, text = self.pending[memo_id]
            if left <= 0:
                self.memos[memo_id]["plainText"] = text
                del self.pending[memo_id]
            else:
                self.pending[memo_id] = (left - 1, text)
        return self.memos[memo_id]

    def writes(self) -> list[tuple[str, str, object]]:
        return [c for c in self.calls if c[0] in ("POST", "PUT")]


def _memo(memo_id: str, text: str, created: str = "2026-09-28T14:00:00Z", **extra) -> dict:
    return {"id": memo_id, "plainText": text, "createdDate": created, **extra}


OLD = (
    "[Operator] Motion calendar as of 2026-09-28\n"
    "Motion calendar assembled: 1 hearing (next: MSJ on Oct 6 at 9:30 a.m., Dept 31); gaps: none.\n"
    "Nothing to do."
)


# ---- normalize ---------------------------------------------------------------
def test_normalize_strips_markup_and_renumbers() -> None:
    text = (
        "[Operator] # Motion calendar as of 2026-09-29\n"
        "> **What:** two motions `pending`.\n"
        "- first item\n"
        "* second item\n"
        "1. one\n1. two\n1. three\n"
        "\n\n\n\n"
        "## Next\n"
        "Nothing to do."
    )
    assert mt.normalize_memo_text(text) == (
        "[Operator] Motion calendar as of 2026-09-29\n"
        "What: two motions pending.\n"
        "first item\n"
        "second item\n"
        "1. one\n2. two\n3. three\n"
        "\n\n"
        "Next\n"
        "Nothing to do."
    )


def test_normalize_leaves_marker_lines_byte_for_byte() -> None:
    lines = [
        f"  op-mmou:{GUID}:638609288928990639",
        f"fileId {GUID} recorded",
        "Package job: job-1; covered document ids: `a`, **b**",
    ]
    out = mt.normalize_memo_text("[Operator] Matter updates as of Sep 29, 2026\n" + "\n".join(lines))
    assert out.splitlines()[1:] == lines


def test_a_table_is_refused_with_the_remedy() -> None:
    with pytest.raises(mt.MemoRefused, match="render_docx_draft and name the file in the note"):
        mt.normalize_memo_text(
            "[Operator] Trial binder as of 2026-09-29\n| Tab | Document |\n|---|---|\n| 1 | Complaint |"
        )


def test_a_bare_stamp_line_joins_the_header() -> None:
    assert mt.memo_header(mt.normalize_memo_text("[Operator]\n\nRecords chase as of Sep 29, 2026\nx")) == (
        "Records chase",
        "Sep 29, 2026",
    )


def test_a_bare_stamp_never_joins_a_marker_line() -> None:
    # matter-memo-on-update can write the change key straight after the stamp;
    # joining them would destroy the key and the note would lose its dedup.
    text = f"[Operator]\nop-mmou:{GUID}:638609288928990639\nMatter updated."
    out = mt.normalize_memo_text(text)
    assert out.splitlines() == ["[Operator]", f"op-mmou:{GUID}:638609288928990639", "Matter updated."]
    assert mt.memo_header(out) is None


def test_header_needs_the_stamp() -> None:
    assert mt.memo_header("Motion calendar as of 2026-09-29") is None
    assert mt.memo_header(OLD) == ("Motion calendar", "2026-09-28")


# ---- upsert ------------------------------------------------------------------
def test_no_header_is_the_legacy_post_and_reads_no_listing() -> None:
    c = _Fake()
    out = mt.upsert_memo(c, M, "Plain log line.", sleep=_nosleep)
    assert out["confirmed"] is True
    assert [w[0] for w in c.writes()] == ["POST"]
    assert not any(call[1].endswith("/memos") and call[0] == "GET" for call in c.calls)


def test_first_routine_note_is_created() -> None:
    c = _Fake()
    out = mt.upsert_memo(c, M, OLD, sleep=_nosleep)
    assert out["created"] is True and out["confirmed"] is True
    assert c.writes()[0][2] == {"text": OLD}


def test_unchanged_moves_only_the_header_day() -> None:
    c = _Fake([_memo("m1", OLD)])
    new = OLD.replace("2026-09-28", "2026-09-29")
    out = mt.upsert_memo(c, M, new, sleep=_nosleep)
    assert out["unchanged"] is True and out["confirmed"] is True and out["id"] == "m1"
    ((method, path, body),) = c.writes()
    assert (method, path) == ("PUT", f"/matters/{M}/memos/m1")
    assert body == {"title": "[Operator] Motion calendar as of 2026-09-29", "text": new}


def test_unchanged_on_the_same_day_writes_nothing() -> None:
    c = _Fake([_memo("m1", OLD)])
    out = mt.upsert_memo(c, M, OLD, sleep=_nosleep)
    assert out == {
        "id": "m1",
        "confirmed": True,
        "unchanged": True,
        "confirm_detail": "already current; nothing written",
    }
    assert c.writes() == []


def test_changed_replaces_in_place_with_a_previously_tail_and_markers() -> None:
    old = OLD + f"\nfileId {GUID} recorded"
    c = _Fake([_memo("m1", old)])
    new = (
        "[Operator] Motion calendar as of 2026-09-29\n"
        "> Motion calendar assembled: 2 hearings; gaps: **opposition window**.\n"
        "Pat Lee to calendar the opposition window."
    )
    out = mt.upsert_memo(c, M, new, sleep=_nosleep)
    assert out["updated"] is True and out["confirmed"] is True
    ((_, _, body),) = c.writes()
    assert body == {
        "title": "[Operator] Motion calendar as of 2026-09-29",
        "text": (
            "[Operator] Motion calendar as of 2026-09-29\n"
            "Motion calendar assembled: 2 hearings; gaps: opposition window.\n"
            "Pat Lee to calendar the opposition window.\n"
            f"fileId {GUID} recorded\n"
            "Previously (2026-09-28): Motion calendar assembled: 1 hearing (next: MSJ on Oct 6 at 9:30 a.m., Dept 31); gaps: none."
        ),
    }


def test_the_fourth_change_drops_the_oldest_previously() -> None:
    c = _Fake([_memo("m1", "[Operator] Lien ledger as of 2026-09-01\nfinding 0")])
    for n, day in enumerate(("2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05"), start=1):
        mt.upsert_memo(c, M, f"[Operator] Lien ledger as of {day}\nfinding {n}", sleep=_nosleep)
    text = c.memos["m1"]["plainText"]
    assert text.splitlines() == [
        "[Operator] Lien ledger as of 2026-09-05",
        "finding 4",
        "Previously (2026-09-04): finding 3",
        "Previously (2026-09-03): finding 2",
        "Previously (2026-09-02): finding 1",
    ]


def test_an_embedded_file_id_survives_its_sentence_being_superseded() -> None:
    old = "[Operator] Service confirmation as of 2026-09-28\nService on Acme confirmed (fileId f-1 recorded).\nNothing to do."
    c = _Fake([_memo("m1", old)])
    mt.upsert_memo(
        c,
        M,
        "[Operator] Service confirmation as of 2026-09-29\nService on Beta confirmed (fileId f-2 recorded).\nNothing to do.",
        sleep=_nosleep,
    )
    text = c.memos["m1"]["plainText"]
    assert "fileId f-1 recorded" in text and "fileId f-2 recorded" in text


def test_a_new_marker_with_the_same_content_is_kept() -> None:
    old = f"[Operator] Matter updates as of Sep 28, 2026\nMatter updated.\nop-mmou:{GUID}:1"
    c = _Fake([_memo("m1", old)])
    out = mt.upsert_memo(
        c, M, f"[Operator] Matter updates as of Sep 28, 2026\nMatter updated.\nop-mmou:{GUID}:2", sleep=_nosleep
    )
    assert out["updated"] is True
    assert c.memos["m1"]["plainText"].splitlines()[-2:] == [f"op-mmou:{GUID}:1", f"op-mmou:{GUID}:2"]


def test_latest_is_newest_by_last_updated_and_ignores_other_notes() -> None:
    c = _Fake(
        [
            _memo("old", OLD, created="2026-09-20T00:00:00Z", lastUpdated="2026-09-28T00:00:00Z"),
            _memo("newer", OLD, created="2026-09-25T00:00:00Z"),
            _memo("human", "Motion calendar as of 2026-09-29\ntyped by a person", created="2026-09-29T00:00:00Z"),
            _memo("other", "[Operator] Lien ledger as of 2026-09-29\nx", created="2026-09-29T00:00:00Z"),
            _memo("gone", OLD, created="2026-09-29T00:00:00Z", isDeleted=True),
        ]
    )
    assert mt.find_latest_routine_memo(c, M, "motion  CALENDAR")["id"] == "old"


def test_the_listing_is_paged_past_one_page() -> None:
    memos = [
        _memo(f"p{i}", f"[Operator] Records chase as of 2026-09-01\nn{i}", created="2026-09-01T00:00:00Z")
        for i in range(mt._PAGE)
    ]
    memos.append(_memo("last", "[Operator] Records chase as of 2026-09-02\nlast", created="2026-09-02T00:00:00Z"))
    assert mt.find_latest_routine_memo(_Fake(memos), M, "Records chase")["id"] == "last"


def test_put_readback_confirms_after_two_lagging_reads() -> None:
    c = _Fake([_memo("m1", OLD)], lag=2)
    slept: list = []
    out = mt.put_and_confirm(c, M, "m1", "[Operator] Motion calendar as of 2026-09-29\nx", sleep=slept.append)
    assert out["confirmed"] is True and out["id"] == "m1"
    assert slept == [1, 2]


def test_put_readback_that_never_lands_is_false() -> None:
    c = _Fake([_memo("m1", OLD)], lag=99)
    assert mt.put_and_confirm(c, M, "m1", "[Operator] X as of 2026-09-29\ny", sleep=_nosleep)["confirmed"] is False


# ---- the tools ---------------------------------------------------------------
def test_create_memo_upserts(monkeypatch) -> None:
    c = _Fake([_memo("m1", OLD)])
    monkeypatch.setattr(srv, "_get_client", lambda: c)
    monkeypatch.setattr(mt.time, "sleep", _nosleep)
    out = srv.create_memo(M, "Motion calendar as of 2026-09-28\n" + OLD.split("\n", 1)[1])
    assert out["unchanged"] is True and c.writes() == []


def test_update_memo_refuses_a_foreign_matter_number(monkeypatch) -> None:
    c = _Fake([_memo("m1", OLD)])
    monkeypatch.setattr(srv, "_get_client", lambda: c)
    monkeypatch.setattr(srv, "_resolve_matter_ref", lambda *_a, **_k: {"number": "2026-PI-101"})
    with pytest.raises(MatterReferenceMismatch):
        mt.update_memo(M, "m1", "See matter 2026-PI-106.")
    assert c.writes() == []


def test_update_memo_writes_plain_and_confirms(monkeypatch) -> None:
    c = _Fake([_memo("m1", OLD)])
    monkeypatch.setattr(srv, "_get_client", lambda: c)
    monkeypatch.setattr(mt.time, "sleep", _nosleep)
    out = mt.update_memo(M, "m1", "**Corrected** note.")
    assert out["confirmed"] is True
    assert c.writes()[0][2] == {"title": "[Operator] Corrected note.", "text": "[Operator] Corrected note."}
