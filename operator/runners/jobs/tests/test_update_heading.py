"""Every added entry's heading is printed on the record (2026-11-17). A
heading is never a claim, so the audit never reads one, and a repair that
wrote after the audit once put a name and a date no page states in front of
a firm. The check records; its one action is to put a finisher-written
heading that fails back to the audited one. Every name, date and value here
is invented."""

from __future__ import annotations

import update_testkit as K
from jobs.medchron.update import body as B, collect as C, final_set as FS, heading as HD
from jobs.medchron.audit import claims as CL

ENTRY = (
    "03/04/2026\nRoe, Jane (M.D.) | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Neck pain rated 6 of 10 after the collision; cervical strain assessed. (Exhibit 2 - p. 4)"
)
ON_4 = "Example Clinic\nVisit date 3/4/26\nJane Roe, M.D.\nNeck pain rated 6 of 10 after the collision."
#: A continuation page: the note's heading is on the page before, which was not composed.
CONT = ENTRY.replace("p. 4)", "p. 5)")
CONT_TEXT = "Continued: cervical strain assessed, therapy advised twice weekly for the neck."
NOTE_HEAD = "Office Note\nAuthor: Jane Roe, M.D.\nDate of Service: 03/04/2026\nHistory of present illness follows."


def _pages(texts: dict[int, str], read: set[int], transcribed: frozenset[int] = frozenset()) -> HD.Pages:
    return HD.Pages(
        text=lambda ex, p: texts.get(p) if ex == 2 else None,
        pages_of=lambda ex: texts if ex == 2 else {},
        read=lambda ex: read,
        transcribed=lambda ex, p: p in transcribed,
    )


def test_a_heading_printed_on_its_cited_page_passes() -> None:
    c = HD.check(ENTRY, _pages({4: ON_4}, {4}))
    assert (c.where, c.page, c.weaker) == (HD.CITED, (2, 4), "")


def test_a_wrong_year_is_a_recorded_failure_never_a_pass() -> None:
    wrong = ENTRY.replace("03/04/2026", "03/04/2025")
    c = HD.check(wrong, _pages({4: ON_4}, {4}))
    assert c.where == HD.FAILED and c.missing == ["date"]


def test_a_provider_no_page_names_fails_with_no_canonical_fallback() -> None:
    other = ENTRY.replace("Roe, Jane (M.D.)", "Poe, Sam (M.D.)")
    assert HD.check(other, _pages({4: ON_4}, {4})).missing == ["name"]


def test_a_continuation_page_passes_on_its_notes_heading() -> None:
    texts = {3: NOTE_HEAD, 5: CONT_TEXT}
    c = HD.check(CONT, _pages(texts, {5}))
    assert (c.where, c.page) == (HD.SAME_NOTE, (2, 3))
    # the heading page composed itself: no note heading is carried, so it fails
    assert HD.check(CONT, _pages(texts, {3, 5})).where == HD.FAILED


def test_a_transcribed_page_is_the_weaker_reading() -> None:
    c = HD.check(ENTRY, _pages({4: ON_4}, {4}, frozenset({4})))
    assert c.where == HD.CITED and c.weaker == HD.WEAKER


def test_no_page_text_is_a_failure() -> None:
    assert HD.check(ENTRY, _pages({}, set())).missing == ["text"]


def _run(entries: list[str], composed: list[str], texts: dict[int, str]) -> FS.Outcome:
    nex = [{"exhibit": 2, "title": "Exhibit 2 - Example Clinic (Medical Records)", "pages": 9}]
    md = C.audit_document(K.base_md(), entries, nex)
    body = CL.body_of(md)
    rows = [{"key": c["key"], "kind": "real", "verdict": "SUPPORTED"} for c in CL.extract_claims(body, {2})]
    return FS.run(FS.Inputs(md, K.base_md(), composed, K.HEADINGS, rows, pages=_pages(texts, {4})))


def test_a_finisher_written_heading_that_fails_goes_back_to_the_audited_one() -> None:
    finisher = ENTRY.replace("03/04/2026", "03/04/2025")
    o = _run([finisher], [ENTRY], {4: ON_4})
    assert B.entries_of(o.md)[1] == [ENTRY]
    assert o.counts["headings reverted to the audited one"] == 1 and o.counts["headings failed"] == 1
    (row,) = o.rows
    assert row["heading"]["reverted"] and row["origin"] == FS.COMPOSED


def test_a_composed_heading_that_fails_is_recorded_and_kept() -> None:
    wrong = ENTRY.replace("03/04/2026", "03/04/2025")
    o = _run([wrong], [wrong], {4: ON_4})
    assert B.entries_of(o.md)[1] == [wrong] and o.counts["headings reverted to the audited one"] == 0
    assert o.counts["headings failed"] == 1


def test_a_finisher_heading_the_page_states_is_kept() -> None:
    texts = {4: ON_4 + "\nVisit date 03/05/2026"}
    finisher = ENTRY.replace("03/04/2026", "03/05/2026")
    o = _run([finisher], [ENTRY], texts)
    assert B.entries_of(o.md)[1] == [finisher] and o.counts["headings on a cited page"] == 1
