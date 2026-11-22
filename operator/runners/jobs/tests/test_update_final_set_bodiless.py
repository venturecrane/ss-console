"""Rule 0 of the update's final set (2026-11-22): an added entry that says
nothing is removed, and the values of its two heading lines are named for
proof 10.

On a real update the review found an entry was not a visit and dropped each
of its claims, which left only its date line and provider line; proof 8
("added entries citing no new exhibit") then stopped the update after the
review was paid for. Every name, date and value here is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

from jobs.medchron.update import final_set as FS, final_set_io as FIO, proofs as P
from test_update_final_set import NEW_POST, ROE_A, _added, _inputs

EMPTY = "07/17/2026\nExample Clinic | All Other Information"
HEADED = EMPTY + "\n\nAll Other Information"


def test_an_entry_with_only_its_heading_lines_is_removed_and_its_values_named() -> None:
    """Fails if the heading-only entry stays (proof 8 fails the update) or
    goes without its values named (proof 10 fails it instead)."""
    for bare in (EMPTY, HEADED):
        o = FS.run(_inputs([ROE_A, bare, NEW_POST]))
        assert _added(o) == [ROE_A, NEW_POST]
        op = next(x for x in o.ops if x["op"] == "bodiless")
        assert op["applied"] and set(op["named"]) == P.numbers(bare) and op["named"]
        assert o.counts["bodiless entries removed"] == 1


def test_an_entry_with_any_text_under_it_stays() -> None:
    """A record row with words and no citation is not empty: it stays."""
    row = EMPTY + "\n\nAll Other Information\nRecords request received from counsel."
    o = FS.run(_inputs([ROE_A, row]))
    assert row in _added(o) and not o.counts["bodiless entries removed"]


def test_the_named_values_reach_proof_10_only_while_the_document_is_the_final_sets(tmp_path: Path) -> None:
    rd = tmp_path / "runs" / "alpha"
    ud = rd / "update"
    ud.mkdir(parents=True)
    o = FS.run(_inputs([ROE_A, EMPTY]))
    (rd / "final-chronology.md").write_text(o.md)
    src = _inputs([ROE_A, EMPTY]).md
    FIO.write(tmp_path, "alpha", o, src)
    assert FIO.named(ud) == P.numbers(EMPTY)
    (rd / "final-chronology.md").write_text(o.md + "\nedited later\n")  # no longer its output
    assert FIO.named(ud) == set()
    assert json.loads((ud / FIO.REPORT).read_text())["ops"][0]["op"] == "bodiless"


def test_proof_8s_condition_holds_once_the_empty_entry_is_gone(monkeypatch) -> None:
    """Proof 8 fails an added entry citing no exhibit (``proofs.py``). Fails if
    rule 0 is skipped: the falsifier runs the step without it."""
    from jobs.medchron.update import body as B

    def no_cite(md: str) -> list[str]:
        return [e for e in B.entries_of(md)[1] if not B.cited(e)]

    assert no_cite(FS.run(_inputs([ROE_A, EMPTY])).md) == []
    monkeypatch.setattr(FS, "bodiless", lambda ents, _h, _o: ents)
    assert no_cite(FS.run(_inputs([ROE_A, EMPTY])).md) == [EMPTY]
