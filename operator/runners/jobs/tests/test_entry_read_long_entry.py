"""An entry too long for one read is read in parts that each judge only their
own lines (2026-11-23, job 01M84VVS: one entry of 1,947 lines and 329 cited
pages made every read, even one page alone, verdict all 1,947 lines, so all
206 replies ran out at 16,000 tokens before any finding). Invented values."""

from __future__ import annotations

import json
from typing import Any

import pytest

from jobs.medchron.update import entry_lines as EL, entry_read as ER
from jobs.stages import entry_audit as EA

HEADINGS = {"All Other Information"}


def npages(ex: int) -> int:
    return 400


def doc(entry: str) -> str:
    return (
        "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n## Medical Chronology\n\n"
        + entry
        + "\n\n## Exhibit List\n"
    )


def long_entry(pages: int, per_page: int, extra: str = "") -> str:
    """An inpatient stay: ``per_page`` one-sentence paragraphs citing each of
    ``pages`` pages of Exhibit 3."""
    paras = [
        f"Nursing note {p}.{k} recorded stable vitals on the ward. (Exhibit 3 - p. {p})"
        for p in range(1, pages + 1)
        for k in range(1, per_page + 1)
    ]
    body = "\n\n".join(paras + ([extra] if extra else []))
    return f"03/01/2025\nExample Hospital | All Other Information\n\nAll Other Information\n{body}"


def parts(entry: str) -> list[ER.Unit]:
    return [u for u in ER.plan(doc(entry), {3}, HEADINGS, npages) if u.anchor == "N1"]


def test_the_real_size_entry_is_read_in_parts_none_judging_more_than_the_cap() -> None:
    # 329 cited pages x 6 lines = 1,974 lines, the size that ran out on 11-23.
    us = parts(long_entry(329, 6))
    every = EL.numbered(us[0].text)[1]
    assert len(every) == 1_976  # D, P and 1,974 sentences
    assert all(len(u.lines) <= ER.LABELS_PER_READ for u in us)
    assert all({"D", "P"} <= set(u.lines) for u in us)
    judged = set().union(*(u.lines for u in us))
    assert judged == set(every)  # every line is judged by some part
    assert [u.key for u in us] == [f"N1-{i}" for i in range(1, len(us) + 1)]


def test_a_part_judges_the_lines_resting_on_its_own_pages() -> None:
    us = parts(long_entry(60, 12))  # 722 lines over 60 pages: 3 page chunks
    rests = EL.label_pages(us[0].text)
    for u in us:
        mine = set(u.cited)
        for lb in u.lines:
            if lb not in ("D", "P"):
                assert rests[lb] & mine, (u.key, lb)
    assert len({tuple(u.cited) for u in us}) == len(us)  # no chunk was cut further


def test_a_chunk_with_more_lines_than_the_cap_becomes_several_parts_on_the_same_pages() -> None:
    # 20 pages show 22 (one chunk), but 20 x 40 = 800 lines: two parts, same pages.
    us = parts(long_entry(20, 40))
    assert len(us) == 2 and us[0].pages == us[1].pages and us[0].cited == us[1].cited
    assert len(us[0].lines) == ER.LABELS_PER_READ and len(us[1].lines) == 802 - ER.LABELS_PER_READ + 2
    assert set(us[0].lines) & set(us[1].lines) == {"D", "P"}


def test_an_uncited_line_is_judged_beside_the_line_before_it_and_a_sentence_takes_its_paragraph_citation() -> None:
    extra = "The patient was ambulating. Pain was controlled with oral medication. (Exhibit 3 - p. 60)"
    uncited = "Family visited daily throughout the stay."
    us = parts(long_entry(60, 12, extra=extra + "\n\n" + uncited))
    labels = EL.numbered(us[0].text)[1]
    amb = next(lb for lb, t in labels.items() if t.startswith("The patient was ambulating"))
    fam = next(lb for lb, t in labels.items() if t.startswith("Family visited"))
    assert EL.label_pages(us[0].text)[amb] == {(3, 60)}  # no citation of its own: the paragraph's
    assert amb in us[-1].lines and amb not in us[0].lines
    assert fam in us[-1].lines and fam not in us[0].lines  # after p. 60's lines: the last part, not the first


def test_a_line_citing_a_page_no_part_shows_is_judged_beside_the_line_before_it() -> None:
    # Exhibit 9 is outside the plan's kept exhibits, so no chunk carries its page.
    us = parts(long_entry(60, 12, extra="A late addendum was filed. (Exhibit 9 - p. 2)"))
    labels = EL.numbered(us[0].text)[1]
    late = next(lb for lb, t in labels.items() if t.startswith("A late addendum"))
    assert late in us[-1].lines and late not in us[0].lines


def test_a_sentence_whose_citation_sits_on_the_next_line_goes_to_the_part_showing_that_page() -> None:
    extra = "The wound was healing well on the last day.\n(Exhibit 3 - p. 60)"
    us = parts(long_entry(60, 12, extra=extra))
    labels = EL.numbered(us[0].text)[1]
    wound = next(lb for lb, t in labels.items() if t.startswith("The wound was healing"))
    assert EL.label_pages(us[0].text)[wound] == {(3, 60)}
    assert wound in us[-1].lines and wound not in us[0].lines  # before the fix: part 1, which never shows p. 60


def test_label_pages_names_exactly_the_labels_numbered_shows() -> None:
    text = long_entry(3, 2, extra="Heading Only\n\nA closing line without a citation.")
    assert set(EL.label_pages(text)) == set(EL.numbered(text)[1])


def test_an_entry_under_the_cap_is_planned_and_asked_exactly_as_before(firm_config_path: Any) -> None:
    us = parts(long_entry(45, 1))  # 47 lines over 45 pages: split by pages only
    every = EL.numbered(us[0].text)[1]
    assert len(us) == 2 and all(u.lines == every for u in us)
    _, content = ER.request(us[0], _cfg(firm_config_path), "CLIENT: Alpha Example", _img)
    assert not any("THIS READ JUDGES ONLY" in b.get("text", "") for b in content)


def test_the_request_names_exactly_the_part_s_lines_and_parse_needs_only_those(firm_config_path: Any) -> None:
    u = parts(long_entry(20, 40))[1]
    _, content = ER.request(u, _cfg(firm_config_path), "CLIENT: Alpha Example", _img)
    note = next(b["text"] for b in content if "THIS READ JUDGES ONLY" in b.get("text", ""))
    assert note.startswith(f"THIS READ JUDGES ONLY THESE {len(u.lines)} LINES: {', '.join(u.labels)}.")
    mine = {lb: "ok" for lb in u.labels}
    got = ER.parse(json.dumps({"lines": {**mine, "S1": "problem"}, "findings": [], "complete": True}), u)
    assert set(got.lines) == set(u.labels)  # a verdict on another part's line is ignored
    short = dict(mine)
    short.pop(u.labels[-1])
    with pytest.raises(ER.RR.ReplyUnparsed):
        ER.parse(json.dumps({"lines": short, "findings": [], "complete": True}), u)


def test_a_page_by_page_read_of_a_part_keeps_only_that_part_s_lines() -> None:
    u = parts(long_entry(20, 40))[0]
    subs = EA._subs(u)
    assert subs and all(s.lines == u.lines for _, s in subs)


def _img(ex: int, pg: int) -> dict[str, Any]:
    return {"type": "image", "page": f"{ex}:{pg}"}


def _cfg(path: Any) -> Any:
    from jobs.medchron import config as config_mod

    return config_mod.load(str(path))


def test_a_saved_read_that_ran_out_is_still_to_pay_in_the_projection(tmp_path: Any) -> None:
    from jobs.stages import entry_audit_io as IO

    u, other = parts(long_entry(20, 40))

    def save(name: str, stop: str) -> None:
        (tmp_path / f"{name}.json").write_text(json.dumps({"hash": "h", "text": "{}", "stop_reason": stop}))

    assert not IO._read_on_file(tmp_path, u)  # nothing on file
    save(f"{u.key}.read1", "max_tokens")
    save(f"{u.key}.read2", "max_tokens")
    assert not IO._read_on_file(tmp_path, u)  # every reply ran out: still to pay (before the fix: counted read)
    n = len([pg for pg in u.pages if pg in set(u.cited)])
    for i in range(1, n + 1):
        save(f"{u.key}~s{i}.read1", "end_turn")
    assert IO._read_on_file(tmp_path, u)  # read page by page
    save(f"{other.key}.read2", "end_turn")
    assert IO._read_on_file(tmp_path, other)  # a whole read that finished
