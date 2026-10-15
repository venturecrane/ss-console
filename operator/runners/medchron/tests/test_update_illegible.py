"""A new entry the audit emptied or trimmed on a handwritten page carries the
firm's illegible note, and the review stops raising what it does not say
(2026-10-14 parity runs: rounds stuck at 27 must-fix, most of them on a
handwritten chiropractic ledger the image audit graded PARTIAL half the time).
Synthetic text; the shapes are the run's."""

from __future__ import annotations

from medchron.update import finisher as FN, illegible as IL, review_repair as RR

HEAD = "03/18/2026\nExample Chiropractic (provider not stated in the record) | Patient Complaints & Limitations"
COMPOSED = HEAD + "\nLow back pain 6/10 with stiffness. (Exhibit 6 - p. 14)"
HW = lambda ex, p: (ex, p) == (6, 14)  # noqa: E731


def doc(*entries: str) -> str:
    return (
        "# Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n## Medical Chronology\n\n"
        + "\n\n".join(entries)
        + "\n\n## Exhibit List\n"
    )


def test_the_note_is_the_firms_own_handwritten_wording_verbatim():
    from pathlib import Path

    import medchron

    prompt = (Path(medchron.__file__).parent / "prompts" / "map-system.md").read_text(encoding="utf-8")
    assert IL.HOUSE_NOTE in prompt


def test_an_emptied_entry_on_a_handwritten_page_keeps_its_visit_with_the_note():
    md, done = IL.restore([COMPOSED], doc(HEAD), [], HW)
    assert done == ["03/18/2026"]
    assert HEAD + "\n\n" + IL.HOUSE_NOTE + " (Exhibit 6 - p. 14)" in md


def test_a_trimmed_entry_keeps_what_the_audit_kept_and_gains_the_note():
    # the date line was rewritten (a handwritten re-date): the entry is matched by date and exhibit
    kept = "03/18/2026 (handwritten date, unconfirmed)\nExample Chiropractic | Patient Complaints & Limitations\nLow back pain. (Exhibit 6 - p. 14)"
    md, done = IL.restore([COMPOSED], doc(kept), [], HW, trimmed={(6, 14)})
    assert done == ["03/18/2026 (handwritten date, unconfirmed)"]
    assert kept + "\n\n" + IL.HOUSE_NOTE in md
    # untrimmed and unchanged: nothing to note
    assert IL.restore([COMPOSED], doc(COMPOSED), [], HW) == (doc(COMPOSED), [])


def test_a_typed_page_a_delivered_entry_or_an_unmatched_entry_is_never_noted():
    assert IL.restore([COMPOSED], doc(HEAD), [], lambda ex, p: False) == (doc(HEAD), [])
    assert IL.restore([HEAD], doc(HEAD), [HEAD], HW) == (doc(HEAD), [])
    other = "04/01/2026\nOther Clinic | Patient Complaints & Limitations"
    assert IL.restore([COMPOSED], doc(other), [], HW) == (doc(other), [])


def test_trimmed_pages_come_from_repairs_and_drops_only():
    rows = [
        {"action": "repair", "exhibit": 6, "page_spec": "14-15"},
        {"action": "drop-residual", "exhibit": 6, "page_spec": "11"},
        {"action": "cite-fix", "exhibit": 6, "page_spec": "9"},
    ]
    assert IL.trimmed_pages(rows) == {(6, 14), (6, 15), (6, 11)}


def _finding(klass: str, anchor: str) -> RR.Finding:
    return RR.Finding("MUST", klass, anchor, 6, 14, "x", "replace", "", "")


def test_the_note_itself_is_never_raised_as_process_wording():
    md, _ = IL.restore([COMPOSED], doc(HEAD), [], HW)
    ents = RR.anchor_entries(md, {6}, set())
    keep, n = FN.settled_as_read([_finding("6 process wording", "N1")], ents)
    assert (keep, n) == ([], 1)
    plain = RR.anchor_entries(doc(COMPOSED), {6}, set())
    assert FN.settled_as_read([_finding("6 process wording", "N1")], plain)[1] == 0


def test_the_reviewer_is_told_the_note_is_the_firms_wording():
    import update_testkit as K
    from medchron import prompts

    assert "never a finding" in prompts.load("update-review-system", K.cfg())


def test_a_mark_or_process_finding_on_a_noted_entry_is_dropped_without_the_model():
    md, _ = IL.restore(
        [COMPOSED],
        doc(COMPOSED.replace("Low back pain 6/10", "Cervical circled; pain 6/10")),
        [],
        HW,
        trimmed={(6, 14)},
    )
    ents = RR.anchor_entries(md, {6}, set())
    found = [_finding("9 marks on a form", "N1.c1"), _finding("1 wrong value", "N1.c1")]
    for i, f in enumerate(found, 1):
        f.fid = f"F{i}"
    auto, rest = IL.drop_on_noted(found, ents)
    assert [(e.action, e.anchor, e.fid) for e in auto] == [("drop", "N1.c1", "F1")]
    assert [f.klass for f in rest] == ["1 wrong value"]
    assert found[0].action == "replace"  # the finding itself is not changed
    # the same finding on an entry without the note goes to the finisher
    plain = RR.anchor_entries(doc(COMPOSED), {6}, set())
    assert IL.drop_on_noted(found[:1], plain) == ([], found[:1])


def test_the_review_settles_omissions_on_a_noted_entry_but_never_a_stated_value():
    md, _ = IL.restore([COMPOSED], doc(HEAD), [], HW)
    ents = RR.anchor_entries(md, {6}, set())
    found = [
        _finding("2 omitted content", "N1.c1"),
        _finding("3 truncated content", "N1"),
        _finding("1 wrong value", "N1.c1"),
    ]
    keep, n = FN.settled_as_read(found, ents)
    assert n == 2 and [f.klass for f in keep] == ["1 wrong value"]
