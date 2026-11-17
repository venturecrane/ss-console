"""A class 2 finding whose content is already composed elsewhere is settled
by code, and the second review round is bounded to what a second read can
settle (2026-11-17, ``update/elsewhere.py``, ``update/recheck.py``).

WHY. An update held on 8 class 2 (omitted content) findings after its second
review round; a person read all 8 against the pages and the reviewer was
wrong on every one: the content was already composed in a delivered entry
citing another exhibit copy of the visit, in another added entry citing a
different filed copy, or in a different added entry than the one the finding
was anchored to. Round 2 had read 851 pages to re-check 20 entries. Each rule
has its falsifier here: a page carrying a value or a finding term the
covering entry lacks stays open and holds. Every name and value is synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jobs.medchron.update import pageid as PI, recheck as RC, review_parts as RP, review_repair as RR
from jobs.stages import update_review as UR, update_review_settle as URS
from test_update_finisher import LATER, N1, _edits, _finding, _record, _review, _run
from test_update_review_settle import _scene, _settled

D = "09/10/2026\nSample Therapy | All Other Information\n\nAll Other Information\n"
SHORT = D + "Therapy evaluation recorded lumbar flexion limited to forty degrees. (Exhibit 3 - p. 1)"
FULL = (
    D + "Therapy evaluation recorded lumbar flexion limited to forty degrees; assessment showed lumbar "
    "radiculopathy, blood pressure 132/96, and a plan of therapy twice weekly. (Exhibit 2 - p. 1)"
)
#: Exhibit 3 p. 2: another filed copy of the visit FULL was composed from.
PAGE = (
    "Sample Therapy evaluation 09/10/2026. Therapy evaluation recorded lumbar flexion limited to forty degrees; "
    "assessment showed lumbar radiculopathy, blood pressure 132/96, and a plan of therapy twice weekly."
)
OMITTED = "the assessment of lumbar radiculopathy and the therapy plan are left out"


def _pages(sr: Any, texts: dict[tuple[int, int], str], identity: dict | None = None) -> None:
    """The new exhibits' page text (``text/<id>.txt``) and the page identity the update decided."""
    ud = sr.slug_dir / "runs" / "alpha" / "update"
    docs = {2: "doc-two", 3: "doc-three"}
    rows = [{"exhibit": ex, "id": d} for ex, d in docs.items()]
    (ud / "new_exhibits.final.json").write_text(json.dumps(rows))
    text = sr.slug_dir / "text"
    text.mkdir(exist_ok=True)
    for ex, d in docs.items():
        body = "".join(f"[p.{p}]\n{t}\n" for (x, p), t in sorted(texts.items()) if x == ex)
        (text / f"{d}.txt").write_text(body)
    if identity is not None:
        (ud / "page_identity.json").write_text(json.dumps({"pages": identity}))


def _omission(anchor: str = "N1", exhibit: int = 3, page: int = 2, says: str = OMITTED) -> RR.Finding:
    f = RR.Finding("MUST", "2 omitted content", anchor, exhibit, page, says, "none", "", "")
    f.page_says = says
    return f


# ---- (e) the class 2 rule ---------------------------------------------------------------
def test_an_omission_another_added_entry_already_holds_is_closed_as_a_note_naming_it(tmp_path: Path) -> None:
    """Case B: another added entry, composed from a different filed copy of the
    visit, holds every value and term of the cited page."""
    sr, doc = _scene(tmp_path, [SHORT, FULL])
    _pages(sr, {(3, 2): PAGE})
    f = _omission()
    rec: dict[str, Any] = {}
    before = doc.read_text()
    assert URS.settle(sr, doc, [f], rec) == []
    assert doc.read_text() == before  # a closure changes no text
    (row,) = rec["settled_by_rule"]
    assert row == {
        "grade": "NOTE",
        "finding": "Exhibit 3 p. 2 is already covered in the entry for 09/10/2026, Sample Therapy.",
        "cite": "",
    }
    assert "review" not in row["finding"].lower()  # the attorney never saw the review
    (done,) = rec["rounds"][-1]["findings"]
    assert rec["rounds"][-1]["kind"] == "covered_elsewhere" and done["covered_by"] == "N2"
    assert done["arm"] == "same content" and _settled(sr) == rec["settled_by_rule"]
    assert f.outcome.startswith("settled by rule before delivery: Exhibit 3 p. 2 is already covered")
    assert URS.notes(sr.slug_dir / "out" / "alpha")[3].startswith("- NOTE: Exhibit 3 p. 2 is already covered")


@pytest.mark.parametrize(
    "extra, lacks",
    [
        (" Pulse 88 was recorded at the evaluation.", "value puls 88"),  # a vital the entry lacks
        (" Impression: lumbar stenosis noted on the evaluation.", "term stenosis"),  # a finding term it lacks
    ],
)
def test_falsifier_a_page_carrying_a_value_or_term_the_covering_entry_lacks_stays_open_and_holds(
    tmp_path: Path, extra: str, lacks: str
) -> None:
    sr, doc = _scene(tmp_path, [SHORT, FULL])
    _pages(sr, {(3, 2): PAGE + extra})
    f = _omission()
    (cover, why) = URS.covers(sr, doc, [f], lambda ex, p: PAGE + extra if (ex, p) == (3, 2) else None)[0]
    assert cover is None and lacks in why
    assert URS.settle(sr, doc, [f], {}) == [f] and _settled(sr) == []
    ud = sr.slug_dir / "runs" / "alpha" / "update"
    assert UR._hold(_ctx(sr), {"rounds": []}, [f], doc, ud) == 2
    assert [o["anchor"] for o in json.loads((ud / UR.RECORD).read_text())["open"]] == ["N1"]


def test_falsifier_the_point_the_finding_names_must_be_in_the_covering_entry(tmp_path: Path) -> None:
    """The page's every value and term is in the entry, but the words of the
    reviewer's point that the page carries are not: a plan (no value, no cue
    word) left out stays open."""
    page = PAGE + " The patient was referred to orthopedics for a surgical consultation."
    sr, doc = _scene(tmp_path, [SHORT, FULL])
    _pages(sr, {(3, 2): page})
    f = _omission(says="referred to orthopedics for a surgical consultation")
    assert URS.settle(sr, doc, [f], {}) == [f]


def test_a_delivered_entry_citing_a_copy_of_the_page_covers_it_by_page_identity(tmp_path: Path) -> None:
    """Case A: the cited page is a copy of a delivered page; the delivered
    entry citing that page holds what the page says."""
    copy = "Cervical strain after the collision, pain rated 6 of 10 at intake. Return to clinic in four weeks."
    sr, doc = _scene(tmp_path, [SHORT])
    identity = {"doc-three": {"2": [PI.ALREADY, f"{PI.DELIVERED}1", 2, "near"]}}
    _pages(sr, {(3, 2): copy}, identity)
    rec: dict[str, Any] = {}
    assert URS.settle(sr, doc, [_omission(says="return to clinic in four weeks")], rec) == []
    (done,) = rec["rounds"][-1]["findings"]
    assert done["covered_by"] == "D2" and done["arm"] == "page identity"
    assert rec["settled_by_rule"][0]["finding"] == (
        "Exhibit 3 p. 2 is already covered in the entry for 01/20/2026, Example Clinic."
    )


def test_falsifier_a_near_copy_with_no_text_and_no_same_content_entry_stays_open(tmp_path: Path) -> None:
    sr, doc = _scene(tmp_path, [SHORT])
    identity = {"doc-three": {"2": [PI.ALREADY, f"{PI.DELIVERED}1", 2, "near"]}}
    _pages(sr, {(3, 2): PI.SKIPPED}, identity)  # never transcribed: nothing says what it reads
    assert URS.settle(sr, doc, [_omission(says="return to clinic in four weeks")], {}) != []


def test_a_delivered_entry_on_the_same_visit_that_reads_like_the_page_covers_it(tmp_path: Path) -> None:
    visit = (
        "01/20/2026\nExample Clinic | All Other Information\n\nAll Other Information\n"
        "Follow-up after the collision recorded neck stiffness. (Exhibit 3 - p. 1)"
    )
    copy = "Cervical strain after the collision, pain rated 6 of 10 at intake. Return to clinic in four weeks for a recheck of the neck."
    sr, doc = _scene(tmp_path, [visit])
    _pages(sr, {(3, 2): copy})
    rec: dict[str, Any] = {}
    assert URS.settle(sr, doc, [_omission(says="return to clinic in four weeks")], rec) == []
    assert rec["rounds"][-1]["findings"][0]["covered_by"] == "D2"


# ---- the anchoring -------------------------------------------------------------------
def test_an_omission_is_anchored_to_the_entry_that_cites_its_page_not_the_nearest() -> None:
    keep, heads = {2, 3}, {"Medical Diagnoses", "All Other Information"}
    doc = (
        "X - Medical Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n"
        f"## Medical Chronology\n\n{SHORT}\n\n{FULL}\n\n## Exhibit List\n"
    )
    ents = RR.anchor_entries(doc, keep, heads)
    near = RR.Finding("MUST", "2 omitted content", "N1", 2, 1, "the plan is left out", "none", "", "")
    value = RR.Finding("MUST", "1 wrong value", "N1.c1", 2, 1, "flexion misread", "none", "", "")
    nowhere = RR.Finding("MUST", "2 omitted content", "N1", 2, 9, "a page no entry cites", "none", "", "")
    assert RP.anchor_by_page([near, value, nowhere], ents) == 1
    assert near.anchor == "N2.c1"  # the claim citing Exhibit 2 p. 1
    assert value.anchor == "N1.c1" and nowhere.anchor == "N1"  # a value dispute and an uncited page stay


# ---- (d) the second round ------------------------------------------------------------
DISPUTED = N1.split("\n")[-1].replace("4 mm", "6 mm")


def test_round_two_rechecks_only_a_disputed_page_and_every_other_finding_is_settled_or_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 1: a wrong value whose correction the audit reverted, a truncation
    the finisher declined, and an omission no entry covers. Only the first is
    re-checked, on its page and the next, never all of its exhibit; the other
    two hold, and the record says why for each."""
    r1 = _review(
        _finding("N1.c1", "1 wrong value", "size misread"),
        _finding("N2.c1", "3 truncated", "cut off", exhibit=3),
        _finding("N1", "2 omitted content", "a plan on p. 3 is left out", page=3),
    )
    r2 = _review(_finding("N1.c1", "1 wrong value", "size misread"))
    fix = {"finding": "F1", "anchor": "N1.c1", "action": "replace", "exhibit": 2, "page": 1, "corrected": DISPUTED}
    good = {**fix, "corrected": DISPUTED.replace("6 mm", "5 mm")}
    decline = {"unresolved": [{"finding": "F2", "why": "faint"}, {"finding": "F3", "why": "not on the page"}]}
    first = json.dumps({"edits": [fix], **decline, "complete": True})
    rc, sr, md = _run(tmp_path, monkeypatch, [N1, LATER], [r1, r2], [first, _edits(good)], unsupported=[DISPUTED])
    assert rc == 2
    r2_calls = [kw for kw in sr.doorway.review_calls if "-r2-" in kw["custom_id"]]
    shown = {b["page"] for kw in r2_calls for b in kw["messages"][0]["content"] if b.get("type") == "image"}
    assert shown == {"2:1", "2:2"}  # the page and its neighbour; page 3 of Exhibit 2 and Exhibit 3 are not read
    rec = _record(sr)
    why = {r["finding"]["anchor"]: r["recheck"] for r in rec["frozen"]}
    assert why["N1.c1"] == RC.SENT
    assert why["N2.c1"].startswith("not re-checked: the finisher made no edit the audit reverted")
    assert why["N1"].startswith("not re-checked: an omission is settled by rule")
    outcomes = {r["finding"]["anchor"]: r["finding"]["outcome"] for r in rec["frozen"]}
    assert outcomes["N1.c1"] == "applied" and DISPUTED.replace("6 mm", "5 mm") in md
    # nothing dropped: every frozen finding not settled is open, on the held document's anchors
    assert sorted(o["anchor"] for o in rec["open"]) == ["N1", "N2.c1"]
    assert any("1 finding(s) to re-check in 1 part(s), 2 page(s)" in m for m in sr.logged)
    assert rec["rounds"][-1]["pages"] == 2 and rec["rounds"][-1]["not_sent"] == 2


def test_round_two_is_skipped_over_its_cap_and_its_findings_hold_with_the_projection_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(RC, "DEFAULT_CAP_USD", 0.01)
    monkeypatch.setattr(UR, "_model", lambda _cfg: "claude-opus-5-5")  # a priced review tier
    r1 = _review(_finding("N1.c1", "1 wrong value", "size misread"))
    fix = {"finding": "F1", "anchor": "N1.c1", "action": "replace", "exhibit": 2, "page": 1, "corrected": DISPUTED}
    rc, sr, md = _run(tmp_path, monkeypatch, [N1, LATER], [r1, _review()], [_edits(fix)], unsupported=[DISPUTED])
    assert rc == 2
    assert not [kw for kw in sr.doorway.review_calls if "-r2-" in kw["custom_id"]]  # nothing paid for round 2
    line = next(m for m in sr.logged if "(re-check): 1 finding(s)" in m)
    assert "projected 0." in line and "against the cap of 0.01 USD (levers.update_recheck_cap_usd)" in line
    rec = _record(sr)
    assert rec["rounds"][-1]["skipped"].startswith("its projection of") and rec["rounds"][-1]["cap_usd"] == 0.01
    assert rec["frozen"][0]["recheck"].startswith("not re-checked: its projection of")
    assert [o["anchor"] for o in rec["open"]] == ["N1.c1"]


def test_the_cap_is_read_from_the_firm_levers_and_defaults_when_unset() -> None:
    import update_testkit as K

    cfg = K.cfg()
    assert RC.cap_usd(cfg) == RC.DEFAULT_CAP_USD == 5.0
    cfg.data["levers"] = {**(cfg.data.get("levers") or {}), "update_recheck_cap_usd": 1.5}
    assert RC.cap_usd(cfg) == 1.5


def _ctx(sr: Any) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(sr=sr)
