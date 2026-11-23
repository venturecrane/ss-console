"""2026-11-22: entries citing a household member's pages (the client's
surname, another given name) reached two delivered chronologies.

* A registration placeholder date of birth (01/01/1900, 00/00/0000, a 00
  month or day) is nobody's: ``foreign_identity.findings`` never reads it as
  "a date of birth that is not the client's" (it withheld a client's own
  pages). A real child's birthday (1/1/11), or an impossible date
  (06/31/2014), still is one.
* One decision by page (``review_evidence.settle_identity``, 2026-11-23): the
  pages the entry reader recorded as another person's and the pages the
  review's check 1 is about are looked at by a model reading each picture
  (``stages/whose_page``), and EVERY entry citing a page judged "other" is
  left out and disclosed in code's words, never the model's. Each verdict is
  cached; nothing is looked at when nothing is flagged; the reader's pages
  are looked at before the paid review call, so a failed look never costs
  that call twice.

Every name and date here is invented.
"""

from __future__ import annotations

import json

import pytest

from jobs.deadline import CallDeadlineError
from jobs.medchron.gates import foreign_identity as FI
from jobs.proof import review as proof_review
from jobs.stages import review, review_evidence, review_policy, whose_page
from test_final_stages import DOC
from test_review_proof import CLEAN, FakeDoorway, _look, _package, _settle_review, _verdict
from test_review_stop_rules import CHECK1, _md, _notes, _pages

CLIENT_DOB = "07/02/1964"


# ---- placeholder dates of birth -----------------------------------------------------
@pytest.mark.parametrize("dob", ["01/01/1900", "1/1/1900", "00/00/0000", "01/01/0000", "00/15/1980", "05/00/1980"])
def test_a_placeholder_date_of_birth_is_nobodys(dob: str) -> None:
    assert FI.findings(f"Patient: Elena Reyes DOB: {dob}", {"Reyes"}, {CLIENT_DOB}) == []


@pytest.mark.parametrize(
    ("dob", "norm"),
    [
        ("03/14/1961", "03/14/1961"),
        ("1/1/11", "01/01/2011"),
        ("2/2/22", "02/02/2022"),
        ("11/11/11", "11/11/2011"),
        ("06/31/2014", "06/31/2014"),
    ],
)
def test_a_real_or_impossible_date_of_birth_that_is_not_the_clients_still_flags(dob: str, norm: str) -> None:
    found = FI.findings(f"Patient: Elena Reyes DOB: {dob}", {"Reyes"}, {CLIENT_DOB})
    assert found == [f"a date of birth {norm} that is not the client's"]


def test_a_four_digit_year_is_never_read_as_two_digits() -> None:
    assert FI._norm_dob("01", "01", "0000") == "01/01/0000"
    assert FI.placeholder_dob("01", "01", "0000") and FI.placeholder_dob("01", "01", "0011")


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


# ---- scene ---------------------------------------------------------------------------
#: The page the tests show the model. Its words decide nothing: the scripted
#: reply does, as the picture check's does on a real run.
PAGE = "Patient: Gamma Example | Visit 03/30/2026\nContinue therapy."
#: A second entry citing the same page 3 as the 03/30/2026 entry.
ENTRY_C = (
    "04/02/2026\nExample Clinic | Treatment Recommendations\n\nFollow-up of the same visit. (Exhibit 1 - p. 3)\n\n"
)
TWO_ON_ONE_PAGE = DOC.replace("## Exhibit List", ENTRY_C + "## Exhibit List")


def _reader(sr, *, page: int = 3, rows: object = None) -> None:
    """The entry reader's record: one another_person finding on page `page`."""
    if rows is None:
        found = {"class": "another_person", "line": "S1", "exhibit": 1, "page": page, "reason": "record_only_class"}
        rows = [{"key": "N1", "recorded": []}, {"key": "N2", "recorded": [found]}]
    rec = {"complete": True, "doc_sha": "x", "rows": rows}
    (sr.slug_dir / "runs" / "alpha" / "entry_read.json").write_text(json.dumps(rec))


def _policy(sr) -> dict:
    return json.loads((sr.slug_dir / "runs" / "alpha" / review_policy.POLICY_RECORD).read_text())


def _left(sr) -> list[dict]:
    p = sr.slug_dir / "out" / "alpha" / review_evidence.LEFT_OUT
    return json.loads(p.read_text())["entries"] if p.is_file() else []


def _main(calls: list[dict]) -> list[dict]:
    """The review's own calls, not the page looks."""
    return [c for c in calls if not str(c.get("custom_id") or "").startswith("whose-page")]


def _delivered_text(sr) -> str:
    """Every word the firm receives: the notes, and the chronology with its Limitations."""
    chron = sr.slug_dir / "out" / "alpha" / "Alpha Example - Medical Chronology 10-13-26.docx"
    return _notes(sr) + "\n" + review.docx_text(chron) + "\n" + _md(sr)


# ---- the entry reader's flags ----------------------------------------------------
def test_the_readers_flag_and_an_other_verdict_leave_the_entry_out(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    _reader(sr)
    d = FakeDoorway(_look("other"), CLEAN)
    assert _settle_review(sr, d) == 0, log
    assert len(d.calls) == 2 and d.calls[0]["custom_id"].startswith("whose-page"), "the look comes before the review"
    look = d.calls[0]
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
    assert review_evidence.left_out_note("03/30/2026") in _notes(sr)
    assert _policy(sr)["identity_left_out"] == 1


@pytest.mark.parametrize("subject", ["client", "both", "none"])
def test_the_readers_flag_with_any_other_verdict_keeps_the_entry(job_dir, firm_config_path, data_root, subject):
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    _reader(sr)
    assert _settle_review(sr, FakeDoorway(_look(subject, "name"), CLEAN)) == 0, log
    assert "Continue therapy." in _md(sr) and _left(sr) == []
    pol = _policy(sr)
    assert pol[f"identity_page_{subject}"] == 1 and pol["identity_left_out"] == 0


def test_a_fenced_reply_is_read(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    _reader(sr)
    d = FakeDoorway("```json\n" + _look("other") + "\n```", CLEAN)
    assert _settle_review(sr, d) == 0, log
    assert len(d.calls) == 2 and len(_left(sr)) == 1


def test_a_reply_unparsed_twice_is_unreadable_and_the_entry_is_kept(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    _reader(sr)
    d = FakeDoorway("not json", '{"subject": "maybe"}', CLEAN)
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


@pytest.mark.parametrize("rows", ["not a list", [{"key": "N2", "recorded": "not a list"}, "not a row", {"key": "N1"}]])
def test_a_malformed_reader_record_is_skipped(job_dir, firm_config_path, data_root, rows):
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    _reader(sr, rows=rows)
    d = FakeDoorway(CLEAN)
    assert _settle_review(sr, d) == 0, log
    assert len(d.calls) == 1 and "Continue therapy." in _md(sr)
    assert _policy(sr)["identity_left_out"] == 0


# ---- the bug review's probes, fixed (2026-11-23) -------------------------------------
def test_p1_the_delivered_package_never_carries_the_models_words_about_the_other_person(
    job_dir, firm_config_path, data_root
):
    """CHECK1's own text names "Beta Roe"; the disclosure is code's."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    _reader(sr)
    assert _settle_review(sr, FakeDoorway(_look("other"), _verdict(CHECK1))) == 0, log
    text = _delivered_text(sr)
    assert "Beta" not in text and "Roe" not in text and "co-plaintiff" not in text
    assert review_evidence.left_out_note("03/30/2026") in _notes(sr)
    assert len(_left(sr)) == 1
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["must_fix"] == 0 and all("Beta" not in json.dumps(f) for f in rec["findings"])


CHECK1_NO_PAGE = (
    "| MUST-FIX | 1 another person's records | the 03/30/2026 Example Clinic visit is another patient's "
    "| entry 03/30/2026 Example Clinic |"
)


def test_p2_a_check1_on_an_entry_the_reader_left_out_is_noted_and_does_not_hold(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    _reader(sr)
    assert _settle_review(sr, FakeDoorway(_look("other"), _verdict(CHECK1_NO_PAGE))) == 0, log
    assert "Continue therapy." not in _md(sr)
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["must_fix"] == 0
    assert [f["finding"] for f in rec["findings"]] == [review_evidence.left_out_note("03/30/2026")]


def test_p3_every_entry_citing_the_readers_page_is_left_out(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root, TWO_ON_ONE_PAGE)
    _pages(sr, PAGE)
    _reader(sr)
    assert _settle_review(sr, FakeDoorway(_look("other"), CLEAN)) == 0, log
    md = _md(sr)
    assert "Continue therapy." not in md and "Follow-up of the same visit." not in md and "Cervical strain." in md
    assert sorted(e["date"] for e in _left(sr)) == ["03/30/2026", "04/02/2026"]
    assert _policy(sr)["identity_left_out"] == 2


def test_p3b_every_entry_citing_a_check1_page_is_left_out(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root, TWO_ON_ONE_PAGE)
    _pages(sr, PAGE)
    assert _settle_review(sr, FakeDoorway(_verdict(CHECK1), _look("other"))) == 0, log
    md = _md(sr)
    assert "Continue therapy." not in md and "Follow-up of the same visit." not in md
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["must_fix"] == 0 and len(_left(sr)) == 2


class _Down(FakeDoorway):
    """The doorway giving up (past its retries) on the `fail`-th call."""

    def __init__(self, *answers: str, fail: int) -> None:
        super().__init__(*answers)
        self.fail = fail

    def call(self, stage, **kw):
        if len(self.calls) + 1 == self.fail:
            self.calls.append({"stage": stage, **kw})
            raise CallDeadlineError("the call ran past its deadline")
        return super().call(stage, **kw)


def test_p4_a_failed_reader_look_ends_the_stage_before_the_paid_review(job_dir, firm_config_path, data_root):
    sr, _log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    _reader(sr)
    before = _md(sr)
    d1 = _Down(fail=1)
    with pytest.raises(CallDeadlineError):
        _settle_review(sr, d1)
    assert _main(d1.calls) == [] and _md(sr) == before, "no review paid, nothing edited"
    d2 = FakeDoorway(_look("other"), CLEAN)
    assert _settle_review(sr, d2) == 0
    assert len(_main(d1.calls + d2.calls)) == 1, "one review call across both attempts"
    assert "Continue therapy." not in _md(sr)


def test_p4b_a_failed_check1_look_after_the_review_keeps_the_entry_and_files(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    d = _Down(_verdict(CHECK1), fail=2)
    assert _settle_review(sr, d) == 0, log
    assert len(_main(d.calls)) == 1 and "Continue therapy." in _md(sr)
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["must_fix"] == 0 and rec["findings"][0]["finding"] == review_evidence.KEPT_NOTE
    assert _policy(sr)["identity_page_unreadable"] == 1
    assert not (sr.slug_dir / "runs" / "alpha" / whose_page.CACHE).exists(), "a failed look is never saved"


CHECK1_MISCITED = (
    "| MUST-FIX | 1 another person's records | the 03/30/2026 visit is another patient's "
    "| entry 03/30/2026 Example Clinic, Exhibit 1 p. 2 |"
)


def test_p5_a_check1_naming_the_clients_page_is_kept_and_filed(job_dir, firm_config_path, data_root):
    """It names p. 2, which the 03/30 entry does not cite: decided on p. 2, as main did."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    assert _settle_review(sr, FakeDoorway(_verdict(CHECK1_MISCITED), _look("client"))) == 0, log
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["must_fix"] == 0 and "Continue therapy." in _md(sr)


def test_the_reviews_check1_with_an_other_verdict_leaves_the_entry_out(job_dir, firm_config_path, data_root):
    """No reader record (an older job): the review's own check 1 is looked at."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    d = FakeDoorway(_verdict(CHECK1), _look("other"))
    assert _settle_review(sr, d) == 0, log
    assert len(d.calls) == 2 and "Continue therapy." not in _md(sr) and _policy(sr)["identity_left_out"] == 1


# ---- the A/B same-date case: decided by what each entry cites --------------------------
#: Entry B: the same date as A (citing p. 3), citing only the client's p. 2.
ENTRY_B = "03/30/2026\nExample Clinic | Medical Diagnoses\n\nCervical strain, improving. (Exhibit 1 - p. 2)\n\n"
TWO_ON_ONE_DATE = DOC.replace("## Exhibit List", ENTRY_B + "## Exhibit List")
CHECK1_TWO_PAGES = (
    "| MUST-FIX | 1 another person's records | the 03/30/2026 visit is another patient's "
    "| entry 03/30/2026 Example Clinic, Exhibit 1 p. 3 and Exhibit 1 p. 1 |"
)


def test_an_entry_on_the_same_date_is_left_out_only_if_it_cites_a_page_judged_other(
    job_dir, firm_config_path, data_root
):
    sr, log = _package(job_dir, firm_config_path, data_root, TWO_ON_ONE_DATE)
    _pages(sr, PAGE)
    _reader(sr)
    d = FakeDoorway(_look("other"), _verdict(CHECK1_TWO_PAGES), _look("client"))
    assert _settle_review(sr, d) == 0, log
    md = _md(sr)
    assert "Continue therapy." not in md, "A cites p. 3, judged another person's"
    assert "Cervical strain, improving." in md, "B cites only p. 2, which nobody judged"
    assert [e["pages"] for e in _left(sr)] == [[3]]


# ---- the limits --------------------------------------------------------------------
def test_the_limits_see_the_looks_the_review_will_make(job_dir, firm_config_path, data_root):
    from jobs.budget import Pricing
    from litigation_observe_shapes import PRICING

    sr, _log = _package(job_dir, firm_config_path, data_root)
    pricing = Pricing.load(PRICING)
    assert whose_page.projected_usd(sr.slug_dir, "alpha", sr.cfg, pricing) == 0.0
    _reader(sr)
    one = whose_page.projected_usd(sr.slug_dir, "alpha", sr.cfg, pricing)
    assert one == pytest.approx(0.0132, abs=0.001), "about the $0.0134 a look measured on the proof"
