"""Rule 0 of the update's final set (2026-11-22): an added entry that says
nothing is removed, and the values of its date line are named for proof 10.

On a real update the review found an entry was not a visit and dropped each
of its claims, which left only its date line and provider line; proof 8
("added entries citing no new exhibit") then stopped the update after the
review was paid for. Every name, date and value here is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobs.medchron.update import body as B, final_set as FS, final_set_io as FIO, proofs as P
from test_update_final_set import NEW_POST, ROE_A, _added, _inputs

DATE = "07/17/2026"
EMPTY = f"{DATE}\nExample Clinic | All Other Information"
HEADED = EMPTY + "\n\nAll Other Information"


def test_an_entry_with_only_its_heading_lines_is_removed_and_its_date_named() -> None:
    """Fails if the heading-only entry stays (proof 8 fails the update) or
    goes without its date named (proof 10 fails it instead)."""
    for bare in (EMPTY, HEADED):
        o = FS.run(_inputs([ROE_A, bare, NEW_POST]))
        assert _added(o) == [ROE_A, NEW_POST]
        op = next(x for x in o.ops if x["op"] == "bodiless")
        assert op["applied"] and set(op["named"]) == P.numbers(DATE) and op["named"]
        assert o.counts["bodiless entries removed"] == 1


@pytest.mark.parametrize(
    "kept",
    [
        EMPTY + "\n\nAll Other Information\nRecords request received from counsel.",  # words under it
        f"{DATE}\nPatient seen for neck pain, BP 140/90.",  # line 2 is content, not a provider line
        f"{DATE}\nExample Clinic | Not A Section",  # its heading is not one of the firm's sections
    ],
)
def test_an_entry_that_says_anything_stays(kept: str) -> None:
    o = FS.run(_inputs([ROE_A, kept]))
    assert kept in _added(o) and not o.counts["bodiless entries removed"]


def test_a_digit_in_the_provider_name_is_not_named() -> None:
    """proof 10's named set is global: "200" of "Suite 200" must not excuse a
    200 lost from another entry. Only the date line's values are named."""
    suite = f"{DATE}\nUrgent Care #3, Suite 200 | All Other Information"
    o = FS.run(_inputs([ROE_A, suite]))
    named = set(next(x for x in o.ops if x["op"] == "bodiless")["named"])
    assert named == P.numbers(DATE) and not {"3", "200"} & named


def _filed(tmp_path: Path, entries: list[str]) -> tuple[Path, FS.Outcome]:
    rd = tmp_path / "runs" / "alpha"
    (rd / "update").mkdir(parents=True)
    src = _inputs(entries).md
    (rd / "final-chronology.md").write_text(src)
    (rd / "update" / "base.md").write_text(_inputs([]).base_md)
    o = FS.run(_inputs(entries))
    FIO.write(tmp_path, "alpha", o, src)
    return rd / "update", o


def test_the_named_date_reaches_proof_10_only_while_the_document_is_the_final_sets(tmp_path: Path) -> None:
    ud, o = _filed(tmp_path, [ROE_A, EMPTY])
    assert FIO.named(ud) == P.numbers(DATE)
    (ud.parent / "final-chronology.md").write_text(o.md + "\nedited later\n")  # no longer its output
    assert FIO.named(ud) == set()
    assert json.loads((ud / FIO.REPORT).read_text())["ops"][0]["op"] == "bodiless"


def test_a_rerun_after_every_entry_was_removed_reads_the_steps_input(tmp_path: Path) -> None:
    """Fails if the stage counts the entries of its own output (none) and takes
    the nothing-added branch, which drops the named date and fails proof 10."""
    ud, _o = _filed(tmp_path, [EMPTY])
    assert FIO.added_entries(tmp_path, "alpha") == 1  # the input it read, not its empty output
    assert FIO.named(ud) == P.numbers(DATE)


def test_proof_8s_condition_holds_once_the_empty_entry_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proof 8 fails an added entry citing no exhibit (``proofs.py``). Fails if
    rule 0 is skipped: the falsifier runs the step without it."""

    def no_cite(md: str) -> list[str]:
        return [e for e in B.entries_of(md)[1] if not B.cited(e)]

    assert no_cite(FS.run(_inputs([ROE_A, EMPTY])).md) == []
    monkeypatch.setattr(FS, "bodiless", lambda ents, _h, _o: ents)
    assert no_cite(FS.run(_inputs([ROE_A, EMPTY])).md) == [EMPTY]
