"""SMD's correction of a held update can remove a paragraph another added
entry states word for word, and confirm a claim the audit rightly dropped
(2026-11-22).

On a real update two added entries of one day each carried the same two
cited sentences as paragraphs of their own (proof 7), and proof 10 listed a
value lost with a claim the audit gate dropped as unreadable, a page number
inside a false statement. Neither could be settled. Every name, date and
value here is invented.
"""

from __future__ import annotations

import json

import pytest

from jobs.medchron import update_decide as UD
from jobs.medchron.update import review_repair as RR, values as V
from test_update_decide import HEAD, LATER, RIGHT, WRONG, _doc, _plan, _seat

SHARED = "Urine drug screen was negative. (Exhibit 3 - p. 1)"
SECOND = "09/10/2026\nSample Therapy | All Other Information"


def _with_repeat(d, sd):
    """The held document with SHARED in both added entries."""
    md = _doc([HEAD + WRONG + "\n\n" + SHARED, LATER + "\n\n" + SHARED])
    rd = sd / "runs" / "alpha"
    (rd / "final-chronology.md").write_text(md)
    rec_p = rd / "update" / UD.RECORD
    rec = json.loads(rec_p.read_text())
    rec["doc_sha_out"] = UD._sha(md)
    rec_p.write_text(json.dumps(rec))
    return md


def _fix() -> list[dict]:
    return [{"old": WRONG, "new": RIGHT, "covers": []}]


def test_a_paragraph_another_entry_states_is_removed_from_the_entry_named(tmp_path, job_dir, data_root) -> None:
    d, sd, paths = _seat(tmp_path, job_dir, data_root)
    _with_repeat(d, sd)
    p = _plan(d, corrections=_fix(), repeats=[{"entry": SECOND, "paragraph": SHARED, "why": "same sentence"}])
    UD.apply(d, p)
    md = paths.doc.read_text()
    assert md.count(SHARED) == 1 and RIGHT in md and "lumbar flexion" in md  # once, in the first entry


@pytest.mark.parametrize(
    ("row", "needle"),
    [
        ({"entry": SECOND, "paragraph": "A sentence nowhere else. (Exhibit 3 - p. 1)"}, "not a paragraph"),
        ({"entry": "01/01/2026\nNobody | All Other Information", "paragraph": SHARED}, "0 added entries"),
    ],
)
def test_a_repeat_is_refused_when_it_would_lose_anything(tmp_path, job_dir, data_root, row, needle) -> None:
    d, sd, _paths = _seat(tmp_path, job_dir, data_root)
    _with_repeat(d, sd)
    with pytest.raises(UD.Refused, match=needle):
        _plan(d, corrections=_fix(), repeats=[{**row, "why": "x"}])


def test_a_paragraph_no_other_entry_states_is_never_removed(tmp_path, job_dir, data_root) -> None:
    """Fails if a repeat removes a paragraph whose words exist only there."""
    d, sd, _paths = _seat(tmp_path, job_dir, data_root)
    md = _doc([HEAD + WRONG, LATER + "\n\n" + SHARED])  # SHARED only in the second entry
    (sd / "runs" / "alpha" / "final-chronology.md").write_text(md)
    rec_p = sd / "runs" / "alpha" / "update" / UD.RECORD
    rec = json.loads(rec_p.read_text())
    rec["doc_sha_out"] = UD._sha(md)
    rec_p.write_text(json.dumps(rec))
    with pytest.raises(UD.Refused, match="no other added entry"):
        _plan(d, corrections=_fix(), repeats=[{"entry": SECOND, "paragraph": SHARED, "why": "x"}])


def test_a_confirmed_drop_names_only_that_claims_values(tmp_path, job_dir, data_root) -> None:
    """Fails if a confirmed drop names values of a claim the audit did not drop,
    or names nothing (proof 10 then fails a correct update)."""
    d, sd, _paths = _seat(tmp_path, job_dir, data_root)
    false_claim = "Labs drawn at 0915 per page 556. (Exhibit 2 - p. 9)"
    out = sd / "out" / "alpha"
    (out / "audit-dropped-claims.json").write_text(
        json.dumps({"claims": [{"key": "k1", "claim": false_claim, "verdict": "PAGE_UNREADABLE"}]})
    )
    with pytest.raises(UD.Refused, match="not a claim the audit dropped"):
        _plan(d, corrections=_fix(), confirmed=[{"key": "k9", "why": "x"}])
    p = _plan(d, corrections=_fix(), confirmed=[{"key": "k1", "why": "a false statement on an unreadable page"}])
    UD.apply(d, p)
    rec = json.loads((sd / "runs" / "alpha" / "update" / UD.RECORD).read_text())
    assert RR.named_values(rec) >= V.numbers(false_claim) and "556" in RR.named_values(rec)


def test_both_are_read_from_the_decisions_file(tmp_path) -> None:
    f = tmp_path / "d.json"
    body = {
        "corrections": [{"old": "a", "new": "b"}],
        "repeats": [{"entry": SECOND, "paragraph": SHARED, "why": "w"}],
        "confirmed_drops": [{"key": "k1", "why": "w"}],
    }
    f.write_text(json.dumps(body))
    assert UD.load_repeats(f)[0]["paragraph"] == SHARED and UD.load_confirmed(f)[0]["key"] == "k1"
    f.write_text(json.dumps({**body, "repeats": [{"entry": SECOND}]}))
    with pytest.raises(UD.Refused, match="all required"):
        UD.load_repeats(f)
