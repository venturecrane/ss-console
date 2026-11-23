"""2026-11-22: entries citing a household member's pages (the client's
surname, another given name) reached two delivered chronologies.

* A registration placeholder date of birth (01/01/1900, 00/00/0000, an
  impossible date) is nobody's: ``foreign_identity.findings`` never reads it
  as "a date of birth that is not the client's" (it withheld a client's own
  pages). A real child's birthday (1/1/11) still is one.
* The entry reader's ``another_person`` findings (``runs/<unit>/entry_read.json``,
  recorded only until now) and the review's check 1 are settled before
  delivery by a model looking at the flagged page's picture
  (``stages/whose_page``): "other", the entry is left out and disclosed;
  anything else, kept and counted. Each verdict is cached, and nothing is
  looked at when nothing is flagged.
* A model check 1 that names pages lands only on an entry citing one of them
  (``review_evidence.entry_for``), so an entry the reader left out never
  passes its finding to the other entry on that date.

Every name and date here is invented.
"""

from __future__ import annotations

import json

import pytest

from jobs.medchron import dag
from jobs.medchron.gates import foreign_identity as FI
from jobs.proof import review as proof_review
from jobs.stages import review_evidence, review_policy, whose_page
from jobs.stages.entry_audit_apply import doc_sha
from test_final_stages import DOC
from test_review_proof import CLEAN, FakeDoorway, _look, _package, _settle_review, _verdict
from test_review_stop_rules import CHECK1, _md, _notes, _pages

CLIENT_DOB = "07/02/1964"


# ---- placeholder dates of birth -----------------------------------------------------
@pytest.mark.parametrize(
    "dob",
    ["01/01/1900", "1/1/1900", "00/00/0000", "01/01/0000", "00/15/1980", "05/00/1980", "02/31/1980", "13/01/1980"],
)
def test_a_placeholder_date_of_birth_is_nobodys(dob: str) -> None:
    assert FI.findings(f"Patient: Elena Reyes DOB: {dob}", {"Reyes"}, {CLIENT_DOB}) == []


@pytest.mark.parametrize(
    ("dob", "norm"),
    [("03/14/1961", "03/14/1961"), ("1/1/11", "01/01/2011"), ("2/2/22", "02/02/2022"), ("11/11/11", "11/11/2011")],
)
def test_a_real_date_of_birth_that_is_not_the_clients_still_flags(dob: str, norm: str) -> None:
    found = FI.findings(f"Patient: Elena Reyes DOB: {dob}", {"Reyes"}, {CLIENT_DOB})
    assert found == [f"a date of birth {norm} that is not the client's"]


def test_a_four_digit_year_is_never_read_as_two_digits() -> None:
    assert FI._norm_dob("01", "01", "0000") is None
    assert FI._norm_dob("01", "01", "0011") == "01/01/0011"
    assert FI.placeholder_dob("01", "01", "0011")


# ---- the picture check's reply --------------------------------------------------
FENCED = "```json\n" + _look("both", "name") + "\n```"


@pytest.mark.parametrize(
    ("text", "subject"),
    [
        (_look("other"), "other"),
        (FENCED, "both"),
        ("```\n" + _look("none", "none") + "\n```", "none"),
        ('{"subject": "Gamma", "basis": "dob"}', None),
        ('{"subject": "other"}', None),
        ("the page is another person's", None),
        ("[]", None),
    ],
)
def test_the_reply_is_parsed_strictly(text: str, subject: str | None) -> None:
    assert whose_page.parse(text) == subject


def test_the_prompt_is_the_proofs() -> None:
    assert whose_page.system().startswith("You decide whose medical record one page is.")
    assert whose_page.system().endswith("Never write any name or date in your answer.")


# ---- the entry reader's another_person findings ------------------------------------
#: The page the tests show the model. Its words decide nothing: the scripted
#: reply does, as the picture check's does on a real run.
PAGE = "Patient: Gamma Example | Visit 03/30/2026\nContinue therapy."


def _reader(sr, *, sha: str | None = None, page: int = 3, rows: object = None) -> None:
    """The entry reader's record: one another_person finding on the 03/30/2026
    entry (N2), recorded only."""
    doc = sr.slug_dir / "runs" / "alpha" / "final-chronology.md"
    if rows is None:
        found = {"class": "another_person", "line": "S1", "exhibit": 1, "page": page, "reason": "record_only_class"}
        rows = [
            {"key": "N1", "status": "read", "findings": 0, "applied": 0, "recorded": []},
            {"key": "N2", "status": "read", "findings": 1, "applied": 0, "recorded": [found]},
        ]
    rec = {"complete": True, "doc_sha": doc_sha(doc.read_text(encoding="utf-8")) if sha is None else sha, "rows": rows}
    (sr.slug_dir / "runs" / "alpha" / "entry_read.json").write_text(json.dumps(rec))


def _policy(sr) -> dict:
    return json.loads((sr.slug_dir / "runs" / "alpha" / review_policy.POLICY_RECORD).read_text())


def _left(sr) -> list[dict]:
    p = sr.slug_dir / "out" / "alpha" / review_evidence.LEFT_OUT
    return json.loads(p.read_text())["entries"] if p.is_file() else []


@pytest.mark.parametrize("trusted", [True, False])
def test_the_readers_flag_and_an_other_verdict_leave_the_entry_out(job_dir, firm_config_path, data_root, trusted):
    """Placed by its anchor while the chronology is the one the reader read,
    else by the one entry citing the page."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    _reader(sr, sha=None if trusted else "not-this-document")
    d = FakeDoorway(CLEAN, _look("other"))
    assert _settle_review(sr, d) == 0, log
    assert len(d.calls) == 2, "the review and one look"
    look = d.calls[1]
    assert look["stage"] == "review" and look["model"] == whose_page.DEFAULT_MODEL and look["max_tokens"] == 400
    content = look["messages"][0]["content"]
    assert content[0]["text"] == "Client: Alpha Example, date of birth 01/01/1970. Page 3."
    assert content[1]["type"] == "image"
    md = _md(sr)
    assert "03/30/2026\nExample Clinic" not in md and "Continue therapy." not in md and "Cervical strain." in md
    assert "| Example Clinic | 01/20/2026 | 1 | Exhibit 1 - p. 2 |" in md, "the tables are re-derived"
    assert [(e["date"], e["pages"], e["reason"]) for e in _left(sr)] == [
        ("03/30/2026", [3], review_evidence.LEFT_OUT_REASON)
    ]
    notes = _notes(sr)
    assert "left out before delivery by rule" in notes and "Gamma" not in notes, "the other person is never named"
    pol = _policy(sr)
    assert pol["identity_left_out"] == 1 and pol["identity_reader_unplaced"] == 0


@pytest.mark.parametrize("subject", ["client", "both", "none"])
def test_the_readers_flag_with_any_other_verdict_keeps_the_entry(job_dir, firm_config_path, data_root, subject):
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    _reader(sr)
    assert _settle_review(sr, FakeDoorway(CLEAN, _look(subject, "name"))) == 0, log
    assert "Continue therapy." in _md(sr) and _left(sr) == []
    pol = _policy(sr)
    assert pol[f"identity_page_{subject}"] == 1 and pol["identity_left_out"] == 0


def test_a_fenced_reply_is_read(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    _reader(sr)
    d = FakeDoorway(CLEAN, "```json\n" + _look("other") + "\n```")
    assert _settle_review(sr, d) == 0, log
    assert len(d.calls) == 2 and len(_left(sr)) == 1


def test_a_reply_unparsed_twice_is_unreadable_and_the_entry_is_kept(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    _reader(sr)
    d = FakeDoorway(CLEAN, "not json", '{"subject": "maybe"}')
    assert _settle_review(sr, d) == 0, log
    assert len(d.calls) == 3, "asked once more, then unreadable"
    assert "Continue therapy." in _md(sr)
    assert _policy(sr)["identity_page_unreadable"] == 1


def test_a_saved_verdict_is_never_paid_for_twice(job_dir, firm_config_path, data_root):
    sr, _log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    asked: list[tuple[int, int, int]] = []

    def ask(ex: int, pg: int, attempt: int, _content: list) -> str:
        asked.append((ex, pg, attempt))
        return "```json\n" + _look("other") + "\n```"

    assert whose_page.Looker(sr, ask=ask).subject(1, 3) == "other"
    assert whose_page.Looker(sr, ask=ask).subject(1, 3) == "other", "a new review reads the saved verdict"
    assert asked == [(1, 3, 1)]
    saved = json.loads((sr.slug_dir / "runs" / "alpha" / whose_page.CACHE).read_text())
    assert [v["subject"] for v in saved.values()] == ["other"] and "Gamma" not in json.dumps(saved)


def test_a_resumed_review_does_not_look_again(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    _reader(sr)
    whose_page.Looker(sr, ask=lambda *_a: _look("client")).subject(1, 3)
    d = FakeDoorway(CLEAN)
    assert _settle_review(sr, d) == 0, log
    assert len(d.calls) == 1, "the review only: the page's verdict was saved"
    assert _policy(sr)["identity_page_client"] == 1


def test_a_job_with_no_flags_makes_no_look(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    d = FakeDoorway(CLEAN)
    assert _settle_review(sr, d) == 0, log
    assert len(d.calls) == 1, "the review only"
    assert not (sr.slug_dir / "runs" / "alpha" / whose_page.CACHE).exists()


def test_a_reader_finding_its_entry_does_not_cite_is_unplaced(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    _reader(sr, page=1)  # N2 cites page 3 only
    assert _settle_review(sr, FakeDoorway(CLEAN, _look("other"))) == 0, log
    assert "Continue therapy." in _md(sr)
    assert _policy(sr)["identity_reader_unplaced"] == 1


@pytest.mark.parametrize("rows", ["not a list", [{"key": "N2", "recorded": "not a list"}, "not a row", {"key": "N1"}]])
def test_a_malformed_reader_record_is_skipped(job_dir, firm_config_path, data_root, rows):
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    _reader(sr, rows=rows)
    d = FakeDoorway(CLEAN)
    assert _settle_review(sr, d) == 0, log
    assert len(d.calls) == 1 and "Continue therapy." in _md(sr)
    assert _policy(sr)["identity_left_out"] == 0


def test_the_reader_and_the_review_on_one_entry_leave_it_out_once(job_dir, firm_config_path, data_root):
    """One page flagged twice is looked at once."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    _reader(sr)
    d = FakeDoorway(_verdict(CHECK1), _look("other"))
    assert _settle_review(sr, d) == 0, log
    assert len(d.calls) == 2 and len(_left(sr)) == 1
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["must_fix"] == 0 and rec["findings"][0]["finding"].startswith("left out before delivery by rule")
    assert _policy(sr)["identity_left_out"] == 1


def test_the_reviews_check1_with_an_other_verdict_leaves_the_entry_out(job_dir, firm_config_path, data_root):
    """No reader record (an older job): the review's own check 1 is looked at."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    assert _settle_review(sr, FakeDoorway(_verdict(CHECK1), _look("other"))) == 0, log
    assert "Continue therapy." not in _md(sr) and _policy(sr)["identity_left_out"] == 1


def test_a_failed_look_leaves_the_chronology_as_it_was(job_dir, firm_config_path, data_root):
    """A look the doorway gives up on raises, as the review's own call does
    (the driver's network-failure path): nothing was edited before it."""
    sr, _log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    _reader(sr)
    before = _md(sr)

    class Down(FakeDoorway):
        def call(self, stage, **kw):
            if self.calls:
                raise ConnectionError("the network is down")
            return super().call(stage, **kw)

    with pytest.raises(ConnectionError):
        _settle_review(sr, Down(CLEAN))
    assert _md(sr) == before and _left(sr) == []


# ---- a finding never lands on the date's other entry ---------------------------------
#: Entry B: the same date as A (N2, citing p. 3), citing only the client's p. 2.
ENTRY_B = "03/30/2026\nExample Clinic | Medical Diagnoses\n\nCervical strain, improving. (Exhibit 1 - p. 2)\n\n"
TWO_ON_ONE_DATE = DOC.replace("## Exhibit List", ENTRY_B + "## Exhibit List")
#: A check 1 naming A's page and a page no remaining entry cites.
CHECK1_TWO_PAGES = (
    "| MUST-FIX | 1 another person's records | the 03/30/2026 visit is another patient's "
    "| entry 03/30/2026 Example Clinic, Exhibit 1 p. 3 and Exhibit 1 p. 1 |"
)


def test_entry_for_needs_a_named_page_even_when_one_entry_has_the_date() -> None:
    md = DOC.replace("Continue therapy. (Exhibit 1 - p. 3)", "Continue therapy. (Exhibit 1 - p. 2)")
    assert review_evidence.entry_for(md, CHECK1_TWO_PAGES) is None
    assert review_evidence.entry_for(DOC, CHECK1_TWO_PAGES) is not None, "DOC's entry cites p. 3"


def test_after_the_reader_leaves_entry_a_out_the_review_never_lands_on_entry_b(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root, TWO_ON_ONE_DATE)
    _pages(sr, PAGE)
    _reader(sr)
    _settle_review(sr, FakeDoorway(_verdict(CHECK1_TWO_PAGES), _look("other")))
    md = _md(sr)
    assert "Continue therapy." not in md, "entry A is left out by the reader settle"
    assert "Cervical strain, improving." in md, "entry B cites neither named page and is kept"
    assert [e["pages"] for e in _left(sr)] == [[3]]


# ---- rehearse and the limits, at $0 --------------------------------------------------
def test_the_review_stage_rehearses_its_looks_at_no_cost(job_dir, firm_config_path, data_root):
    sr, _log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    _reader(sr)
    d = FakeDoorway()
    sr._doorway = d  # type: ignore[assignment]
    stage = next(s for s in dag.STAGES if s.name == "review")
    assert stage.rehearse is whose_page.rehearse
    lines = stage.rehearse(sr)
    assert d.calls == [] and "1 page(s) the entry reader flagged" in lines[0] and "1 not yet looked at" in lines[0]
    whose_page.Looker(sr, ask=lambda *_a: _look("client")).subject(1, 3)
    assert "0 not yet looked at" in stage.rehearse(sr)[0], "a saved verdict is not projected again"


def test_the_limits_see_the_looks_the_review_will_make(job_dir, firm_config_path, data_root):
    from jobs.budget import Pricing
    from litigation_observe_shapes import PRICING

    sr, _log = _package(job_dir, firm_config_path, data_root)
    pricing = Pricing.load(PRICING)
    assert whose_page.projected_usd(sr.slug_dir, "alpha", sr.cfg, pricing) == 0.0
    _reader(sr)
    one = whose_page.projected_usd(sr.slug_dir, "alpha", sr.cfg, pricing)
    assert one == pytest.approx(0.0132, abs=0.001), "about the $0.0134 a look measured on the proof"
