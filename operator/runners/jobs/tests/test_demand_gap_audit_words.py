"""The gap audit (the missing-records list) reaches the firm in the firm's
words, never the system's.

On 2026-11-06 a 10,590-page demand ran to the pre-delivery review and was held
there on five MUST-FIX findings, every one check 5 (system vocabulary) and
every one in the gap audit: the model wrote "digest shows", "the digest", "in
digest" and "not in digest" for the record it had read as the record digest;
the letter, whose repairs already pass through ``draft.firm_reason``, carried
none. The list also cited the free bill reconciliation as "preflight", the
system's name for it, which the review did not yet hold on.

Two guarantees, one rule (``gapaudit.firm_words``): a fresh list is rendered
in the firm's words cell by cell, and the list is scrubbed again as it is
filed, so a list cached before the rule (that job's) is corrected on a resume
without being bought again. The review's own list of banned phrases gains the
bill reconciliation's system name, so a check that cannot fail is not what
guards this. Every name here is invented."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from demand_testkit import GAP, ScriptedClient, make_inputs, make_job, seat_with, standard_docs
from jobs_testkit import PRICING
from jobs.demand import gapaudit, review as rv, review_stage
from jobs.demand.run import DemandRun
from jobs.proof import review as proof_review

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"

#: The 2026-11-06 list's phrases, on the fixture's rows: each cell is a shape
#: the model wrote that night (patterns, not its words).
SAID = (
    GAP.replace(
        "| ER record 1-15-26, p. 1 | Referenced in record |",
        "| digest shows no ED bill; ER record 1-15-26, p. 1 | Referenced in the digest |",
    )
    .replace(
        "| ER record 1-15-26, p. 2 | Referenced in record | records | Strengthens",
        "| not in digest; order in ER record 1-15-26, p. 2 | Referenced in record | records | Strengthens",
    )
    .replace(
        "| ER bill 1-15-26, p. 1 | Billing mismatch |",
        "| none in digest (copy in digest per Billing tab) | preflight (Billing tab vs bills) |",
    )
)
assert SAID != GAP


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


@pytest.fixture
def pricing(tmp_path: Path) -> Path:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return p


def _run(tmp_path, pricing, client):
    inputs = tmp_path / "inputs"
    if not inputs.is_dir():
        make_inputs(inputs)
    jd = make_job(tmp_path / "job")
    r = DemandRun(
        jd,
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat_with(standard_docs()),
        client=client,
        log=lambda _s: None,
        readback_pause=0.0,
        vendor_factory=lambda: None,
    )
    return r, r.run()


def _kept(data: Path, role: str) -> str:
    """The markdown the deliverable was rendered from, as the runner kept it for the review."""
    return json.loads((data / review_stage.DELIVERED_MD).read_text(encoding="utf-8"))[role]


def _unscrubbed(text: str) -> str:
    """The rule run backwards, on text where every "the record" was once "digest"."""
    return text.replace("the bill reconciliation", "preflight").replace("the record", "digest")


# ---- the rule itself -------------------------------------------------------------------------
@pytest.mark.parametrize(
    "said, firm",
    [
        ("digest shows no ED bill", "the record shows no ED bill"),
        ("Digest shows no ED bill", "The record shows no ED bill"),
        ("not in digest; order at p. 2", "not in the record; order at p. 2"),
        ("The digest states two visits.", "The record states two visits."),
        ("per the record digest, 1/16", "per the record, 1/16"),
        ("the digest's entry for 7/23", "the record's entry for 7/23"),
        ("no cite in digest", "no cite in the record"),
        ("preflight (Billing tab vs bills)", "the bill reconciliation (Billing tab vs bills)"),
        ("Preflight counts the ledger as a bill", "The bill reconciliation counts the ledger as a bill"),
        ("the free preflight found it", "the bill reconciliation found it"),
    ],
)
def test_the_systems_words_for_its_inputs_become_the_firms(said, firm):
    assert gapaudit.firm_words(said) == firm
    assert rv.wording({"gap_audit": firm}) == []
    assert gapaudit.firm_words(firm) == firm  # idempotent: scrubbed at render and again at filing


@pytest.mark.parametrize(
    "kept",
    [
        "Upper digestive tract normal (GI note).",
        "Digestion was normal on exam.",
        "the preflight inspection of the aircraft (NTSB report, p. 3)",
        "Preflight checklist signed by the pilot",
    ],
)
def test_a_records_own_words_are_never_rewritten(kept):
    assert gapaudit.firm_words(kept) == kept
    assert rv.wording({"gap_audit": kept}) == []


def test_the_contract_asks_for_the_firms_words():
    assert '"the digest"' in gapaudit.CONTRACT and '"preflight"' in gapaudit.CONTRACT
    assert "never" in gapaudit.CONTRACT.split("The firm reads every cell", 1)[1]


# ---- the 2026-11-06 hold, on fixtures ---------------------------------------------------------
def test_that_nights_findings_reproduce_on_the_unscrubbed_list_and_the_scrub_clears_them():
    md = gapaudit.render(gapaudit.merge([SAID]), {})
    # FALSIFIER: render scrubs; the unscrubbed text is the rows as the model wrote them
    raw = "\n".join(ln for ln in SAID.splitlines() if ln.startswith("|"))
    found = rv.wording({"gap_audit": raw})
    said = [f.finding for f in found]
    assert all(f.grade == rv.R.MUST and f.check == "5 system vocabulary" for f in found)
    for phrase in ("digest shows", "the digest", "in digest", "preflight"):
        assert any(phrase in s for s in said), (phrase, said)
    # the fix: the same rows, rendered, carry none of it and nothing else changed
    assert rv.wording({"gap_audit": md}) == []
    assert "digest" not in md.lower() and "preflight" not in md.lower()
    scrubbed = gapaudit.firm_words(raw)
    assert scrubbed.count("\n") == raw.count("\n") and scrubbed.count("|") == raw.count("|")
    assert re.sub(r"\s+", " ", _unscrubbed(scrubbed)) == re.sub(r"\s+", " ", raw.replace("in the digest", "in digest"))


def test_a_run_on_such_a_list_is_delivered_in_the_firms_words(tmp_path, pricing):
    client = ScriptedClient(gap=SAID)
    _r, v = _run(tmp_path, pricing, client)
    assert v.outcome == "delivered", v.reason
    rec = json.loads((tmp_path / "job" / "data" / "review.json").read_text())
    assert rec["must_fix"] == 0
    filed = _kept(tmp_path / "job" / "data", "gap_audit")
    assert "digest" not in filed.lower() and "preflight" not in filed.lower()
    assert "the record shows no ED bill" in filed and "the bill reconciliation (Billing tab vs bills)" in filed
    assert "| 4 |" in filed  # every row survived the rewrite


def test_without_the_rule_the_review_holds_as_it_did_that_night(tmp_path, pricing, monkeypatch):
    monkeypatch.setattr(gapaudit, "firm_words", lambda s: s)
    client = ScriptedClient(gap=SAID)
    _r, v = _run(tmp_path, pricing, client)
    assert v.outcome == "failed" and v.reason.startswith("review_held:"), v.reason
    assert "5 system vocabulary" in v.reason and '"digest shows"' in v.reason
    rec = json.loads((tmp_path / "job" / "data" / "review.json").read_text())
    assert rec["must_fix"] >= 4 and not rec["released"]


# ---- the resume: the cached list is scrubbed, not bought again -----------------------------------
def test_a_list_cached_before_the_rule_is_corrected_on_resume_without_a_new_gap_audit(tmp_path, pricing, monkeypatch):
    monkeypatch.setattr(gapaudit, "firm_words", lambda s: s)
    client = ScriptedClient(gap=SAID)
    _r, v = _run(tmp_path, pricing, client)
    assert v.outcome == "failed" and v.reason.startswith("review_held:")
    monkeypatch.undo()
    data = tmp_path / "job" / "data"
    paid = client.stages()
    # SMD's redo of the review alone: the render mark stands, the same bytes
    # are reviewed again, and the same words hold it again (so a resume must
    # also clear the render mark; the proof the 11-06 resume rests on).
    (tmp_path / "job" / proof_review.REDO_MARKER).write_text("{}", encoding="utf-8")
    _r, v2 = _run(tmp_path, pricing, client)
    assert v2.outcome == "failed" and v2.reason.startswith("review_held:")
    # the resume as it must be run: render mark cleared, review redone
    state = json.loads((data / "state.json").read_text())
    state.pop("render")
    (data / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "job" / proof_review.REDO_MARKER).write_text("{}", encoding="utf-8")
    _r, v3 = _run(tmp_path, pricing, client)
    assert v3.outcome == "delivered", v3.reason
    again = client.stages()[len(paid) :]
    assert "GAP" not in again and "DIGEST" not in again and "VISION" not in again and "COMPOSE" not in again
    assert again.count("REVIEW") == 2  # one held redo, one that passed
    filed = _kept(data, "gap_audit")
    assert "digest" not in filed.lower() and "preflight" not in filed.lower()
    assert "the record shows no ED bill" in filed
