"""The page finder's facts: counts, pointers, unread pages, the match cache (2026-11-02).

On an A&P records request (2026-11-02) the Operator found the right files, but the reply was wrong:
the model retyped pages and counts, a visit came back 1-4 then 1-5, and the
match's "closest" sentence was dropped. Each test here pins one fact the
result now carries in code, so a reply can be rendered from it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from smokeball_connector import page_finder as pf
from smokeball_connector import page_finder_match as pm
from smokeball_connector import page_finder_prompts as prompts
from smokeball_connector import page_finder_tools as pft

from .test_matter_files import MATTER, FakeClient, _pdf
from .test_page_finder import (  # noqa: F401 - fixtures and helpers shared with the finder's suite
    TRUTH,
    VISIT,
    FakeModel,
    _page,
    _refusing,
    _set,
    _spool,
    _staff_seat,
    _work,
    model,
)


def _matches(fake: FakeModel) -> int:
    return sum(1 for b in fake.bodies if "items" in b["output_config"]["format"]["schema"]["properties"])


# ---- counts and the schema version -----------------------------------------


def _sums(out: dict[str, Any]) -> bool:
    c = out["counts"]
    return c["read"] + c["unreadable"] + c["notReached"] == c["pages"] == out["pageCount"]


def test_a_whole_file_counts_every_page_read(model: FakeModel, tmp_path: Path) -> None:
    out = pf.locate(_set(tmp_path), "the visit", _work(tmp_path))
    assert out["schemaVersion"] == pm.SCHEMA_VERSION == 2
    assert out["counts"] == {"pages": 40, "read": 40, "unreadable": 0, "notReached": 0} and _sums(out)


def test_counts_sum_with_pages_unread_and_not_reached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _refusing(monkeypatch, tmp_path, {3})
    monkeypatch.setattr(pf, "PAGES_PER_CALL", 16)
    out = pf.locate(_set(tmp_path, 40), "x", _work(tmp_path))
    assert out["counts"] == {"pages": 40, "read": 15, "unreadable": 1, "notReached": 24} and _sums(out)


# ---- closest and related: ids from the model, facts from the index ---------


def test_closest_and_related_are_pointers_from_the_index_only(model: FakeModel, tmp_path: Path) -> None:
    model.items = [
        {
            "asked": "the 3/14/2024 x-ray",
            "found": False,
            "sections": [],
            "reason": "nothing dated 3/14/2024",
            "closest": ["S2", "S404", "S2"],
            "relatedCandidates": [],
        },
        {
            "asked": "the discharge",
            "found": True,
            "sections": ["S3"],
            "reason": "S3",
            "closest": ["S1"],
            "relatedCandidates": ["S3", "S4", "S9"],
        },
    ]
    xray, discharge = pf.locate(_set(tmp_path), "x", _work(tmp_path))["items"]
    # An unknown id is dropped, a repeat is not repeated, and every field is the index's.
    assert xray["closest"] == [{"section": "S2", "pages": "5-28", "heading": "LAB RESULT", "date": "4/2/2025"}]
    # A found item has no closest; a related id already sent is not repeated.
    assert discharge["closest"] == []
    assert discharge["relatedCandidates"] == [
        {"section": "S4", "pages": "39-40", "heading": "BILLING STATEMENT", "date": "7/1/2024"}
    ]
    for item in (xray, discharge):
        for ptr in item["closest"] + item["relatedCandidates"]:
            assert set(ptr) == {"section", "pages", "heading", "date"}


def test_a_related_section_inside_a_found_range_is_not_repeated() -> None:
    summary = ("DISCHARGE SUMMARY", "discharge summary", "6/30/2024", "Hospital B")
    flow = ("VITALS FLOWSHEET", "flowsheet", "6/28/2024", "Hospital B")
    pages = {"1": _page(summary), "2": _page(flow, cont=True), "3": _page(cont=True)}
    pages["2"]["note_label"] = "Discharge Summary (continued)"
    pages["3"]["note_label"] = "Discharge Summary (continued)"
    secs = pf.sections(pages)
    raw = [{"asked": "a", "found": True, "sections": ["S1"], "reason": "", "relatedCandidates": ["S2"]}]
    (item,) = pm.compose(raw, secs)
    assert item["relatedCandidates"] == []


def test_the_match_asks_for_ids_for_closest_and_related() -> None:
    props = prompts.MATCH_SCHEMA["properties"]["items"]["items"]
    assert {"closest", "relatedCandidates"} <= set(props["required"])
    assert props["properties"]["closest"] == {"type": "array", "items": {"type": "string"}}
    assert "closest:" in prompts.MATCH_INSTRUCTION and "relatedCandidates:" in prompts.MATCH_INSTRUCTION


# ---- not established: lowercase, and the model's sentence never shown -----


def test_not_established_is_lowercase_and_the_models_sentence_stays_inside(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _refusing(monkeypatch, tmp_path, {3})
    fake.items = [{"asked": "the x-ray", "found": False, "sections": [], "reason": "There is no x-ray."}]
    (xray,) = pf.locate(_set(tmp_path, 8), "x", _work(tmp_path))["items"]
    assert xray["reason"].startswith("not established: ") and "NOT" not in xray["reason"]
    assert xray["_modelReason"] == "There is no x-ray."


def test_the_tool_never_returns_the_models_internal_sentence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _refusing(monkeypatch, tmp_path, {3})
    fake.items = [{"asked": "the x-ray", "found": False, "sections": [], "reason": "There is no x-ray."}]
    _staff_seat(tmp_path, monkeypatch)
    c = FakeClient()
    fid = c.add(1, "Clinic records", _pdf(8))
    out = pft.find_pages(c, MATTER, fid, "the x-ray")
    assert out["schemaVersion"] == 2 and "There is no x-ray" not in json.dumps(out)
    assert "do not retype pages or counts" in out["nextStep"]


# ---- unread pages never extend a section by themselves ---------------------


def _spans(pages: dict[str, dict[str, Any]], unread: set[int]) -> list[tuple[str, int, int, list[str]]]:
    unreadable = {str(n): "declined" for n in unread}
    return [
        (s["heading"], s["first"], s["last"], sorted(s.get("unreadablePages") or {}, key=int))
        for s in pf.sections(pages, unreadable)
    ]


NEXT = ("OFFICE VISIT", "office visit note", "4/4/2021", "Clinic A")


def test_one_unread_page_inside_a_note_is_bridged_and_named() -> None:
    pages = {"1": _page(VISIT), "2": _page(cont=True), "4": _page(cont=True)}
    assert _spans(pages, {3}) == [("OFFICE VISIT", 1, 4, ["3"])]


def test_two_unread_pages_in_a_row_are_bridged_and_named() -> None:
    pages = {"1": _page(VISIT), "2": _page(cont=True), "5": _page(cont=True)}
    assert _spans(pages, {3, 4}) == [("OFFICE VISIT", 1, 5, ["3", "4"])]


def test_an_unread_first_page_starts_nothing() -> None:
    pages = {"2": _page(cont=True), "3": _page(cont=True)}
    assert _spans(pages, {1}) == [("(untitled: starts before the indexed pages)", 2, 3, [])]


def test_an_unread_last_page_is_never_run_on() -> None:
    pages = {"1": _page(VISIT), "2": _page(cont=True)}
    assert _spans(pages, {3}) == [("OFFICE VISIT", 1, 2, [])]


def test_an_unread_page_before_a_new_start_belongs_to_no_section() -> None:
    pages = {"1": _page(VISIT), "2": _page(cont=True), "4": _page(NEXT)}
    assert _spans(pages, {3}) == [("OFFICE VISIT", 1, 2, []), ("OFFICE VISIT", 4, 4, [])]


def test_an_unread_page_next_to_an_embedded_section_keeps_both_open() -> None:
    summary = ("DISCHARGE SUMMARY", "discharge summary", "6/30/2024", "Hospital B")
    flow = ("VITALS FLOWSHEET", "flowsheet", "6/28/2024", "Hospital B")
    pages = {"1": _page(summary), "2": _page(flow, cont=True), "4": _page(cont=True), "5": _page(NEXT)}
    for n in ("2", "4"):
        pages[n]["note_label"] = "Discharge Summary (continued)"
    secs = pf.sections(pages, {"3": "declined"})
    assert [(s["heading"], s["first"], s["last"]) for s in secs] == [
        ("DISCHARGE SUMMARY", 1, 4),
        ("VITALS FLOWSHEET", 2, 4),
        ("OFFICE VISIT", 5, 5),
    ]
    assert secs[1]["insideOf"] == "S1" and list(secs[0]["unreadablePages"]) == ["3"]


def test_the_unread_third_page_shape_is_one_to_four() -> None:
    """8-C: p3 unread, p5 a new visit. The visit is 1-4, never 1-5, and p3 is
    named inside it."""
    pages = {"1": _page(VISIT), "2": _page(cont=True), "4": _page(cont=True), "5": _page(NEXT)}
    assert _spans(pages, {3}) == [("OFFICE VISIT", 1, 4, ["3"]), ("OFFICE VISIT", 5, 5, [])]
    raw = [{"asked": "the visit", "found": True, "sections": ["S1"], "reason": ""}]
    (item,) = pm.compose(raw, pf.sections(pages, {"3": "declined"}))
    pm.qualify([item], {"3": "declined"}, 0)
    assert [r["pages"] for r in item["ranges"]] == ["1-4"] and item["unreadableInside"] == ["3"]


# ---- the match cache --------------------------------------------------------


def test_a_whole_index_caches_the_match_for_the_same_request(model: FakeModel, tmp_path: Path) -> None:
    src = _set(tmp_path)
    first = pf.locate(src, "The visit", _work(tmp_path))
    again = pf.locate(src, "  the   VISIT ", _work(tmp_path))
    assert _matches(model) == 1 and again["matchFromCache"] and not first["matchFromCache"]
    assert again["items"] == first["items"] and again["tokens"]["input_tokens"] == 0
    other = pf.locate(src, "the discharge", _work(tmp_path))
    assert _matches(model) == 2 and not other["matchFromCache"]


def test_a_partial_index_never_caches_its_match(model: FakeModel, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pf, "PAGES_PER_CALL", 16)
    src = _set(tmp_path)
    pf.locate(src, "the visit", _work(tmp_path))
    assert pf.load_index(pf.file_sha256(src))["matches"] == {}


def test_the_key_moves_with_the_unread_pages_and_the_match_version(monkeypatch) -> None:
    secs = pf.sections({"1": _page(VISIT), "2": _page(cont=True)})
    base = pm.match_key("m", secs, {}, "the visit")
    assert pm.match_key("m", secs, {"9": "x"}, "the visit") != base
    assert pm.match_key("other", secs, {}, "the visit") != base
    monkeypatch.setattr(pm, "MATCH_VERSION", pm.MATCH_VERSION + 1)
    assert pm.match_key("m", secs, {}, "the visit") != base


def test_the_cache_is_bounded() -> None:
    index: dict[str, Any] = {}
    for n in range(pm.MAX_CACHED_MATCHES + 5):
        pm.remember_match(index, f"k{n}", [])
    assert len(index["matches"]) == pm.MAX_CACHED_MATCHES and "k0" not in index["matches"]


# ---- the on-disk index: version 4 indexes stay valid -----------------------


def test_a_v4_index_shaped_like_the_seats_is_read_whole_from_cache(model: FakeModel, tmp_path: Path) -> None:
    """The four indexes on the seat (2026-11-02) are version 4, full view, this
    model, and carry no ``matches`` key. Loading one reads no page again."""
    src = _set(tmp_path)
    sha = pf.file_sha256(src)
    pages = {}
    for n, (date, provider, kind, starts) in TRUTH.items():
        section = {"heading": kind.upper(), "document_type": kind, "date": date, "provider": provider}
        pages[str(n)] = {
            "continues_previous": not starts,
            "sections": [section] if starts else [],
            "dates": [f"DOS {date}"],
            "running_header": "",
            "note_label": "",
            "page_of": "",
            "legible": True,
        }
    on_disk = {
        "version": 4,
        "model": pf.model(),
        "view": "full",
        "sha256": sha,
        "page_count": 40,
        "pages": pages,
        "matters": [MATTER],
        "unreadable": {},
        "refusals": {},
        "spent": {"input_tokens": 64000, "output_tokens": 3200},
        "saved_at": 1793600000.0,
    }
    base = pf.index_dir()
    base.mkdir(parents=True, exist_ok=True)
    (base / f"{sha}.json").write_text(json.dumps(on_disk), encoding="utf-8")
    assert pf.PROMPT_VERSION == 4
    out = pf.locate(src, "the visit", _work(tmp_path))
    assert model.index_calls() == 0 and out["readNow"] == 0 and out["fromCache"] == 40 and out["complete"]


def test_found_with_every_section_unknown_is_not_established(model: FakeModel, tmp_path: Path) -> None:
    """The model said found, but no id it gave is in the index: a whole, fully
    read file, yet "not found" would be a guess."""
    model.items = [{"asked": "the visit", "found": True, "sections": ["S77"], "reason": "S77"}]
    (item,) = pf.locate(_set(tmp_path), "the visit", _work(tmp_path))["items"]
    assert item["found"] is False and item["droppedSections"] == 1
    assert item["established"] is False and "does not hold" in item["notEstablishedBecause"]


def test_a_file_with_an_unreadable_page_caches_its_match(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The 8-C case: every page tried, one unreadable. The match must keep, so
    the same request gets the same ranges, although the file is never ``complete``."""
    fake = _refusing(monkeypatch, tmp_path, {3})
    src = _set(tmp_path, 8)
    first = pf.locate(src, "the visit", _work(tmp_path))
    again = pf.locate(src, "the visit", _work(tmp_path))
    assert not first["complete"] and first["remainingPages"] == 0
    assert again["matchFromCache"] and _matches(fake) == 1
    assert [r["pages"] for r in again["items"][0]["ranges"]] == [r["pages"] for r in first["items"][0]["ranges"]]


def test_a_file_with_pages_not_reached_never_caches_its_match(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _refusing(monkeypatch, tmp_path, {3})
    monkeypatch.setattr(pf, "PAGES_PER_CALL", 4)
    src = _set(tmp_path, 8)
    pf.locate(src, "the visit", _work(tmp_path))
    assert pf.load_index(pf.file_sha256(src))["matches"] == {} and _matches(fake) == 1


# ---- the $0 replay on the seat's indexes (2026-11-02): section rows only ----

NOTE = ("PROGRESS NOTE", "progress note", "5/15/2024", "Clinic A")
ENTRY = ("PROGRESS NOTE", "progress note", "3/20/2024", "Clinic A")
SUMMARY = ("DISCHARGE SUMMARY", "discharge summary", "6/30/2024", "Hospital B")
RECORD_LABEL = "Clinic A Clinical Notes (continued)"


def _labelled_row(label: str, *starts: tuple[str, str, str, str]) -> dict[str, Any]:
    row = _page(*starts, cont=True)
    row["note_label"] = label
    return row


def _heads(pages: dict[str, dict[str, Any]]) -> list[tuple[str, str, int, int]]:
    return [(s["heading"], s["date"], s["first"], s["last"]) for s in pf.sections(pages)]


def test_a_record_level_continued_label_never_keeps_a_note_open_over_a_new_start() -> None:
    """8-B shape: the label on p4 is the RECORD's ("... Clinical Notes
    (continued)"), not the 5/15 note's. The 3/20 entry starting on p4 closes the
    5/15 note there: 2-4, never 2-5."""
    pages = {
        "1": _page(("CLINIC A RECORDS", "records certification", "", "Clinic A")),
        "2": _page(NOTE),
        "3": _labelled_row(RECORD_LABEL),
        "4": _labelled_row(RECORD_LABEL, ENTRY),
        "5": _labelled_row(RECORD_LABEL),
    }
    spans = _heads(pages)
    assert ("PROGRESS NOTE", "5/15/2024", 2, 4) in spans and ("PROGRESS NOTE", "3/20/2024", 4, 5) in spans


def test_a_discharge_note_stops_where_a_record_label_page_starts_something() -> None:
    """The twin files' shape (closest discharge note 33-100 on one, 33-149 on
    the other): the summary's own label runs 34-37; from 38 the label is the
    record's, and the note starting on 38 ends the summary there."""
    pages = {"33": _page(SUMMARY)}
    for n in range(34, 38):
        pages[str(n)] = _labelled_row("Discharge Summary (continued)")
    pages["38"] = _labelled_row(RECORD_LABEL, NOTE)
    for n in range(39, 150):
        pages[str(n)] = _labelled_row(RECORD_LABEL, ENTRY) if n % 10 == 0 else _labelled_row(RECORD_LABEL)
    assert _heads(pages)[0] == ("DISCHARGE SUMMARY", "6/30/2024", 33, 38)


def test_a_note_whose_own_label_says_continued_still_runs_whole() -> None:
    """8-E shape: a 35-page discharge summary whose labels name the summary
    itself keeps running through what is embedded in it: 1-35."""
    flow = ("VITALS FLOWSHEET", "flowsheet", "6/28/2024", "Hospital B")
    pages = {"1": _page(SUMMARY)}
    for n in range(2, 36):
        pages[str(n)] = _labelled_row("Discharge Summary (continued)", *((flow,) if n in (4, 20) else ()))
    pages["36"] = _page(("OFFICE VISIT", "office visit note", "8/1/2024", "Clinic A"))
    assert _heads(pages)[0] == ("DISCHARGE SUMMARY", "6/30/2024", 1, 35)


SPEECH = ("SPEECH THERAPY NOTE", "speech therapy note", "12/11/2024", "Clinic C")
GROWTH = ("GROWTH CHART", "growth chart", "", "Clinic C")


@pytest.mark.parametrize("legible", [True, False])
def test_a_blank_page_never_extends_a_note(legible: bool) -> None:
    """The replay's 48-51: a speech note ran through a blank p50 and onto the
    growth chart that starts on p51. The key is 48-49. A truly empty page
    (nothing printed, continuing nothing) breaks the note however it is marked."""
    empty = _page()
    empty["legible"] = legible
    pages = {"48": _page(SPEECH), "49": _page(cont=True), "50": empty, "51": _page(GROWTH, cont=True)}
    assert _heads(pages) == [("SPEECH THERAPY NOTE", "12/11/2024", 48, 49), ("GROWTH CHART", "", 51, 51)]


def test_a_handwritten_page_mid_note_stays_inside_and_is_flagged() -> None:
    """An illegible page that continues the note never ends it: ending there
    would send a short record. It stays inside, listed as unreadable."""
    hand = _page(cont=True)
    hand["legible"] = False
    pages = {"1": _page(NOTE), "2": _page(cont=True), "3": hand, "4": _page(cont=True), "5": _page(ENTRY)}
    note, entry = pf.sections(pages)
    assert (note["first"], note["last"], entry["first"]) == (1, 4, 5)
    assert list(note["unreadablePages"]) == ["3"]
    raw = [{"asked": "the 5/15 note", "found": True, "sections": ["S1"], "reason": ""}]
    (item,) = pm.compose(raw, pf.sections(pages))
    pm.qualify([item], {}, 0)
    assert [r["pages"] for r in item["ranges"]] == ["1-4"] and item["unreadableInside"] == ["3"]


def test_a_new_start_after_unread_pages_never_bridges() -> None:
    pages = {"48": _page(SPEECH), "49": _page(cont=True), "51": _page(GROWTH, cont=True)}
    secs = pf.sections(pages, {"50": "declined"})
    assert [(s["heading"], s["first"], s["last"]) for s in secs] == [
        ("SPEECH THERAPY NOTE", 48, 49),
        ("GROWTH CHART", 51, 51),
    ]
