"""The final set's repeats, paragraph by paragraph (2026-11-19). On a real
delivered 509-page update 38 of 144 added entries repeated a visit and the
all-or-nothing rule dropped 7: one value the other entry lacked kept the whole
repeat. Now an added entry that is one encounter with another loses each cited
paragraph one keeper covers on its own; delivered entries and keepers are never
edited. Each rule is tested with the mutation that makes it fail, through the
real step and parsers. Every name, date and value here is invented."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import update_testkit as K
from jobs.medchron.update import (
    assemble as A,
    body as B,
    entry_edits as EE,
    final_set as FS,
    final_set_io as FIO,
    proofs as P,
)
from jobs.medchron.update import repeat_paras as RP
from jobs.stages import update_final_set
from test_update_final_set import COPY, D, NEW_POST, NEX, ROE_A, TIMED, _doc, _inputs, _job, _sr

DELIVERED = B.entries_of(K.base_md())[1]
DIAG = "Medical Diagnoses"
OTHER = "All Other Information"
DISCHARGE = "Discharge / Discontinuation in Care"
#: A delivered visit's second copy, from a new exhibit: every paragraph of it the delivered entry states.
POST_COPY = K.ENTRY_POST.replace("Exhibit 1", "Exhibit 2")
STOPPED = "Cyclobenzaprine 7.5 mg was discontinued on 02/14/2026. (Exhibit 2 - p. 3)"


def _added(o: FS.Outcome) -> list[str]:
    return B.entries_of(o.md)[1]


def _keys_stable(x: FS.Inputs, o: FS.Outcome) -> None:
    assert o.counts["new claim keys created"] == 0 and o.counts["claim keys lost unaccounted"] == 0
    assert FS.keys_of(o.md) <= FS.keys_of(x.md) and o.counts["distinct visits merged"] == 0


def test_an_added_entry_that_adds_one_dated_fact_keeps_only_that_paragraph(monkeypatch: pytest.MonkeyPatch) -> None:
    """The delivered entry is unchanged; the added copy keeps only the
    medication stop, cited to its own exhibit."""
    added = f"{POST_COPY}\n\n{DISCHARGE}\n{STOPPED}"
    x = _inputs([added])
    o = FS.run(x)
    head = "\n".join(added.split("\n")[:2])
    assert _added(o) == [f"{head}\n\n{DISCHARGE}\n{STOPPED}"]
    assert B.entries_of(o.md)[1] == _added(o)
    md, _ = A.assemble(
        K.base_md(), o.md, describe=lambda *_a: None, canon=str, headings=K.HEADINGS, new_exhibits=NEX[:1]
    )
    assert K.ENTRY_POST in md and B.entries_of(md)[1][: len(DELIVERED)] == DELIVERED  # delivered, byte for byte
    assert o.counts["repeats: paragraphs dropped"] == 2 and o.counts["repeats: entries shortened"] == 1
    _keys_stable(x, o)
    # mutation: with the paragraph rule off, the copy stays whole (all or nothing, as before)
    monkeypatch.setattr(RP, "covers", lambda *_a: False)
    assert _added(FS.run(_inputs([added]))) == [added]


def test_a_paragraph_whose_removal_changes_another_key_is_left_and_counted() -> None:
    """Two cited lines under one heading: the first closes the heading's span,
    so removing it makes the heading part of the next claim (a new key)."""
    added = f"{POST_COPY}\n{STOPPED}"  # the stop date sits under "All Other Information", right below the recheck
    x = _inputs([added])
    o = FS.run(x)
    (e,) = _added(o)
    assert "Cervical strain" not in e and "Return to clinic" in e and STOPPED in e
    assert o.counts["repeats: paragraphs left, removing them would change a claim"] == 1
    _keys_stable(x, o)
    lines = e.split("\n")
    k = next(i for i, ln in enumerate(lines) if ln.startswith("Return to clinic"))
    assert FS.keys_of("\n".join(lines[:k] + lines[k + 1 :])) - FS.keys_of(e)  # falsifier: it WOULD create a key


def test_the_last_paragraph_citing_an_exhibit_stays() -> None:
    only_three = (
        COPY.replace("(Exhibit 2 - p. 4)", "(Exhibit 3 - p. 1)")
        + f"\n\n{DISCHARGE}\nIbuprofen was stopped on 03/20/2026. (Exhibit 2 - p. 6)"
    )
    x = _inputs([ROE_A, only_three])
    o = FS.run(x)
    assert _added(o) == [ROE_A, only_three]
    assert o.counts["repeats: paragraphs left, an exhibit would be uncited"] == 1
    _keys_stable(x, o)
    other_three = NEW_POST.replace("Exhibit 2", "Exhibit 3")  # mutation: exhibit 3 cited elsewhere, the paragraph goes
    (_r, short, _n) = _added(FS.run(_inputs([ROE_A, only_three, other_three])))
    assert "Exhibit 3" not in short and "Ibuprofen was stopped" in short


#: The same visit seen at another true time of day, and its keeper.
TIMED_KEEPER = ROE_A.replace("Neck pain after", "Seen at 09:15 for neck pain after")
TIMED_COPY = COPY.replace("Neck pain after", "Seen at 10:32 for neck pain after")


def test_an_entry_left_with_no_cited_paragraph_goes_whole_and_a_true_time_of_day_may_differ() -> None:
    x = _inputs([TIMED_KEEPER, TIMED_COPY])
    o = FS.run(x)
    assert _added(o) == [TIMED_KEEPER]
    assert o.counts["repeats: entries removed whole, every paragraph covered"] == 1
    assert o.values_dropped == {RP.excuse_key(TIMED_COPY): {"10:32"}}
    assert FS.keys_of(TIMED_COPY) <= o.removed
    _keys_stable(x, o)
    dosed = TIMED_COPY.replace("Seen at 10:32", "Given 30 mg at 10:32")  # mutation: a dose is care, it stays
    assert _added(FS.run(_inputs([TIMED_KEEPER, dosed]))) == [TIMED_KEEPER, dosed]
    # a time the keeper does not have, on words it does not hold either, stays
    assert _added(FS.run(_inputs([ROE_A, TIMED]))) == [ROE_A, TIMED]


def test_a_fold_is_never_touched() -> None:
    fold = EE.fold_text(
        K.ENTRY_POST, "Cervical strain after the collision. (Exhibit 2 - p. 3)", K.HEADINGS, today="11/17/2026"
    )
    x = _inputs([fold, POST_COPY + f"\n\n{DISCHARGE}\n{STOPPED}"])
    o = FS.run(x)
    assert _added(o)[0] == fold and o.counts["repeats: entries shortened"] == 1
    _keys_stable(x, o)


def test_two_copies_each_with_a_value_keep_their_own_and_say_the_shared_once() -> None:
    first = ROE_A + f"\n\n{DISCHARGE}\nIbuprofen was stopped on 03/20/2026. (Exhibit 2 - p. 5)"
    second = (
        ROE_A.replace("Roe, Jane (M.D.)", "Example Clinic Orthopedics, Jane Roe").replace("Exhibit 2", "Exhibit 3")
        + f"\n\n{DISCHARGE}\nA cervical collar was ordered, order number 448812. (Exhibit 3 - p. 2)"
    )
    x = _inputs([first, second])
    o = FS.run(x)
    md = "\n\n".join(_added(o))
    for shared in ("rotation to the left", "Plan physical therapy twice weekly"):
        assert md.count(shared) == 1
    assert "Ibuprofen was stopped" in md and "448812" in md and len(_added(o)) == 2
    assert first in _added(o) or second in _added(o)  # the keeper is never edited
    _keys_stable(x, o)


def test_the_final_set_then_the_proofs_excuse_a_dropped_time_on_its_own_entry_only(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """Proof 10 reads entry A's dropped clock time as removed on purpose
    (``final_set_io.values_dropped``, per entry); entry B, which lost the same
    time for real, still fails it."""
    sr = _sr(job_dir, firm_config_path, data_root, [])
    sd = sr.slug_dir
    _job(sd, [TIMED_KEEPER, TIMED_COPY])
    assert update_final_set.run(sr) == 0
    ud = sd / "runs" / "alpha" / "update"
    mark = json.loads((ud / FS.REPORT).read_text())
    key = RP.excuse_key(TIMED_COPY)
    assert mark["counts"]["repeats: paragraphs dropped"] == 1 and mark["values_dropped"] == {key: ["10:32"]}
    updated = DELIVERED + B.entries_of((sd / "runs" / "alpha" / "final-chronology.md").read_text())[1]
    excused = FIO.values_dropped(ud)
    assert excused == {key: {"10:32"}}
    assert P.dropped_values([TIMED_KEEPER, TIMED_COPY], updated, {2}, set(), (), excused) == []
    assert P.dropped_values([TIMED_KEEPER, TIMED_COPY], updated, {2}, set()) != []  # unrecorded, proof 10 fails
    lost_b = (
        "02/02/2026\nExample Imaging | Medical Diagnoses\n\nMedical Diagnoses\nX-ray taken at 10:32. (Exhibit 2 - p. 7)"
    )
    got = P.dropped_values([TIMED_KEEPER, TIMED_COPY, lost_b], updated, {2}, set(), (), excused)
    assert [d for d, _v in got] == ["02/02/2026"] and "10:32" in got[0][1]  # B is not excused by A's record
    (sd / "runs" / "alpha" / "final-chronology.md").write_text(_doc([TIMED_KEEPER, TIMED_COPY]))
    assert FIO.values_dropped(ud) == {}  # a document the step did not write excuses nothing


#: A keeper, and five paragraphs that share its numbers or cue terms but say otherwise (the 2026-11-19 review).
FACTS = (
    f"{D}\nRoe, Jane (M.D.) | Medical Diagnoses\n\nMedical Diagnoses\n"
    "MRI showed disc herniation at L4-L5 with nerve root compression. Pain 7/10 in the left knee. "
    "Insulin carb ratio 1:15. Prior lumbar injury in 2021. Heparin 5000 units given. (Exhibit 2 - p. 4)"
)
SAYS_OTHERWISE = [
    "MRI showed no disc herniation at L4-L5 and no nerve root compression.",
    "Off work for two weeks; pain 7/10 in the right knee.",
    "Insulin carb ratio 1:12.",
    "Prior lumbar injury in 2019.",
    "Heparin 2000 units given.",
]


def _copy_of_facts(para: str) -> str:
    return (
        f"{D}\nExample Clinic Orthopedics, Jane Roe | Medical Diagnoses\n\nMedical Diagnoses\n{para} (Exhibit 3 - p. 1)"
    )


@pytest.mark.parametrize("para", SAYS_OTHERWISE)
def test_a_paragraph_that_says_otherwise_is_kept(para: str) -> None:
    added = _copy_of_facts(para)
    three = NEW_POST.replace("Exhibit 2", "Exhibit 3")  # exhibit 3 cited elsewhere: only the rule can keep it
    x = _inputs([FACTS, added, three])
    o = FS.run(x)
    assert added in _added(o)
    assert not RP.covers(FACTS, added.split("\n")[-1], FS.dates_present) and not FS.covers(FACTS, added)
    _keys_stable(x, o)


def test_a_true_verbatim_repeat_is_still_dropped() -> None:
    added = _copy_of_facts("MRI showed disc herniation at L4-L5 with nerve root compression.")
    x = _inputs([FACTS, added, NEW_POST.replace("Exhibit 2", "Exhibit 3")])
    o = FS.run(x)
    assert added not in _added(o) and FACTS in _added(o)
    _keys_stable(x, o)


def test_the_falsifier_is_its_own_reading() -> None:
    keep = FACTS.split("\n")[-1]
    assert not RP.lost("MRI showed disc herniation at L4-L5 with nerve root compression. (Exhibit 3 - p. 1)", FACTS)
    for para in SAYS_OTHERWISE:
        assert RP.lost(para, FACTS), para
    assert RP.lost("Seen at 10:32 for neck pain.", keep)


def test_a_paragraph_with_no_value_goes_only_when_its_words_are_in_the_keeper_whole() -> None:
    p = "Return to clinic in four weeks for a recheck of the neck. (Exhibit 2 - p. 2)"
    assert RP.covers(K.ENTRY_POST, p, FS.dates_present)
    assert not RP.covers(K.ENTRY_POST.replace("the neck.", "the necklace."), p, FS.dates_present)  # whole words
    assert not RP.covers(K.ENTRY_POST, p.replace("Return", "Do not return"), FS.dates_present)
