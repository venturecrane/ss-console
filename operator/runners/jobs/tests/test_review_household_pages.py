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
  left out, exactly as the entry reader's own list bounds it, and disclosed
  in code's words, never the model's; so is a prior-history paragraph citing
  one. Each verdict is cached; nothing is looked at when nothing is flagged.
* A look that fails ends the review through its retry exit with the
  chronology unedited, and the paid review's findings are kept, so a resume
  settles from them without paying for the review again.

Every name and date here is invented.
"""

from __future__ import annotations

import json

import pytest

from jobs.deadline import CallDeadlineError
from jobs.limits import LimitHold
from jobs.medchron.gates import foreign_identity as FI
from jobs.medchron.update import body as B
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
    assert review_evidence.left_out_note("03/30/2026") in _notes(sr)
    assert _policy(sr)["identity_left_out"] == 1
    assert not (sr.slug_dir / "runs" / "alpha" / proof_review.PAID).exists(), "the saved review replaces it"


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


@pytest.mark.parametrize("rows", ["not a list", [{"key": "N2", "recorded": "not a list"}, "not a row", {"key": "N1"}]])
def test_a_malformed_reader_record_is_skipped(job_dir, firm_config_path, data_root, rows):
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    _reader(sr, rows=rows)
    d = FakeDoorway(CLEAN)
    assert _settle_review(sr, d) == 0, log
    assert len(d.calls) == 1 and "Continue therapy." in _md(sr)
    assert _policy(sr)["identity_left_out"] == 0


# ---- one entry list: the entry reader's bounds ----------------------------------------
RECORDS = (
    "04/02/2026 (records date, not a date of service)\nExample Clinic | Records\n\nForm printed. (Exhibit 1 - p. {p})\n"
)
YEARLESS = "07/18 (year not stated)\nExample Clinic | Treatment Log\n\nLog row written. (Exhibit 1 - p. {q})\n"


def _noted_doc(flagged: str) -> str:
    """The 03/30 entry on the client's p. 2, then a records-date entry and a
    yearless one; the `flagged` one cites p. 3, the other p. 2."""
    p, q = (3, 2) if flagged == "records" else (2, 3)
    extra = RECORDS.format(p=p) + "\n" + YEARLESS.format(q=q) + "\n"
    doc = DOC.replace("Continue therapy. (Exhibit 1 - p. 3)", "Continue therapy. (Exhibit 1 - p. 2)")
    return doc.replace("## Exhibit List", extra + "## Exhibit List")


def test_the_entry_list_is_the_entry_readers() -> None:
    md = _noted_doc("records")
    pre, ents = review_evidence.entries(md)
    assert [e[2].strip("\n") for e in ents] == B.entries_of(md)[1]
    assert pre is not None and pre[2].strip("\n") == B.entries_of(md)[0].strip("\n")
    lines = md.splitlines(keepends=True)
    assert all("".join(lines[a:b]) == t for a, b, t in ents)


@pytest.mark.parametrize("flagged", ["records", "yearless"])
def test_only_the_flagged_entry_goes_and_its_neighbours_stay_byte_for_byte(
    job_dir, firm_config_path, data_root, flagged
):
    doc = _noted_doc(flagged)
    sr, log = _package(job_dir, firm_config_path, data_root, doc)
    _pages(sr, PAGE)
    _reader(sr)
    before = B.entries_of(_md(sr))[1]
    assert _settle_review(sr, FakeDoorway(CLEAN, _look("other"))) == 0, log
    after = B.entries_of(_md(sr))[1]
    gone = [e for e in before if e not in after]
    assert len(gone) == 1 and ("Form printed." if flagged == "records" else "Log row written.") in gone[0]
    assert all(e in after for e in before if e not in gone), "every other entry, heading and text, unchanged"
    assert len(_left(sr)) == 1


# ---- prior medical history ------------------------------------------------------------
PRE_DOC = DOC.replace(
    "**Neck:** chronic pain before the incident. (Exhibit 1 - p. 1)",
    "**Neck:** chronic pain before the incident. (Exhibit 1 - p. 3)\n\n**Back:** no prior complaints. (Exhibit 1 - p. 1)",
).replace("Continue therapy. (Exhibit 1 - p. 3)", "Continue therapy. (Exhibit 1 - p. 2)")


def test_a_prior_history_paragraph_on_a_page_judged_other_goes_alone(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root, PRE_DOC)
    _pages(sr, PAGE)
    _reader(sr, rows=[{"key": "PRE", "recorded": [{"class": "another_person", "exhibit": 1, "page": 3}]}])
    assert _settle_review(sr, FakeDoorway(CLEAN, _look("other"))) == 0, log
    md = _md(sr)
    assert "chronic pain before the incident" not in md
    assert "Prior Medical History" in md and "**Back:** no prior complaints. (Exhibit 1 - p. 1)" in md
    assert "[NTD: 1 additional pre-incident encounter was reviewed and is not itemized.]" in md
    assert "Continue therapy." in md, "no dated entry cites the page"
    assert _left(sr) == [
        {
            "date": "Prior Medical History",
            "provider": "",
            "exhibit": 1,
            "pages": [3],
            "reason": review_evidence.LEFT_OUT_REASON,
        }
    ]
    note = review_evidence.prior_note([(1, 3)])
    assert note == (
        "a prior-history paragraph citing Exhibit 1 p. 3 names a person other than the client, "
        "so it was left out of this chronology"
    )
    assert note in _notes(sr)
    lim = md[md.index("## Records Reviewed and Limitations") :]
    assert "A prior-history paragraph citing Exhibit 1 p. 3 names a person other than the client" in lim
    assert _policy(sr)["identity_prior_left_out"] == 1


# ---- the bug review's probes, fixed (2026-11-23) -------------------------------------
def test_p1_the_delivered_package_never_carries_the_models_words_about_the_other_person(
    job_dir, firm_config_path, data_root
):
    """CHECK1's own text names "Beta Roe"; the disclosure is code's."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    _reader(sr)
    assert _settle_review(sr, FakeDoorway(_verdict(CHECK1), _look("other"))) == 0, log
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
    assert _settle_review(sr, FakeDoorway(_verdict(CHECK1_NO_PAGE), _look("other"))) == 0, log
    assert "Continue therapy." not in _md(sr)
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["must_fix"] == 0
    assert [f["finding"] for f in rec["findings"]] == [review_evidence.left_out_note("03/30/2026")]


def test_p3_every_entry_citing_the_readers_page_is_left_out(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root, TWO_ON_ONE_PAGE)
    _pages(sr, PAGE)
    _reader(sr)
    assert _settle_review(sr, FakeDoorway(CLEAN, _look("other"))) == 0, log
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
    """A page look that fails with `error` (the doorway past its retries, a
    400, a refusal, a limit); the review's own call answers as scripted."""

    def __init__(self, *answers: str, error: BaseException | None = None, refuse: bool = False) -> None:
        super().__init__(*answers)
        self.error, self.refuse = error, refuse

    def call(self, stage, **kw):
        if str(kw.get("custom_id") or "").startswith("whose-page"):
            if self.refuse:
                self.calls.append({"stage": stage, **kw})
                return type("R", (), {"text": "", "stop_reason": "refusal", "usage": None})()
            if self.error is not None:
                self.calls.append({"stage": stage, **kw})
                raise self.error
        return super().call(stage, **kw)


def test_p4_a_network_failed_look_exits_75_unedited_and_the_resume_pays_no_review(job_dir, firm_config_path, data_root):
    sr, _log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    _reader(sr)
    before = _md(sr)
    d1 = _Down(CLEAN, error=CallDeadlineError("the call ran past its deadline"))
    assert _settle_review(sr, d1) == review.EXIT_INFRA
    assert _md(sr) == before and _left(sr) == [], "nothing edited"
    assert (sr.slug_dir / "runs" / "alpha" / proof_review.PAID).is_file(), "the paid findings are kept"
    assert proof_review.load(sr.slug_dir / "runs" / "alpha") is None, "nothing was saved as reviewed"
    d2 = FakeDoorway(_look("other"))
    assert _settle_review(sr, d2) == 0
    assert _main(d2.calls) == [] and len(d2.calls) == 1, "the resume makes only the missing look"
    assert len(_main(d1.calls + d2.calls)) == 1 and "Continue therapy." not in _md(sr)


@pytest.mark.parametrize(
    ("down", "why"),
    [({"error": ValueError("400: the request was malformed")}, "rejected"), ({"refuse": True}, "refused")],
    ids=["400", "refusal"],
)
def test_f2_a_look_refused_or_rejected_ends_the_review_failed_once_not_retried(
    job_dir, firm_config_path, data_root, down, why
):
    """The same request would fail the same way: one attempt, the page
    check's own exit and reason, never the retry exit the daemon requeues."""
    from jobs import requeue

    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    _reader(sr)
    before = _md(sr)
    d = _Down(CLEAN, **down)
    code = _settle_review(sr, d)
    assert code == review.EXIT_LOOK != requeue.INFRA_EXIT
    outcome, reason = review.EXITS[code]
    assert outcome == "failed" and not requeue.MODEL_API.search(reason)
    line = f"review: the page check could not read Exhibit 1 p. 3 ({why}); nothing was filed"
    assert log[-1] == line and not requeue.MODEL_API.search(line)
    assert len(_main(d.calls)) == 1 and _md(sr) == before and _left(sr) == []


#: A check 1 naming p. 4, which no entry cites; its 03/30 entry cites p. 3.
CHECK1_P4 = (
    "| MUST-FIX | 1 another person's records | the 03/30/2026 visit is another patient's "
    "| entry 03/30/2026 Example Clinic, Exhibit 1 p. 4 |"
)


def test_f1_a_page_judged_other_that_nothing_cites_removes_nothing_and_says_kept(job_dir, firm_config_path, data_root):
    from jobs.stages import manifest
    from jobs_testkit import make_pdf

    sr, log = _package(job_dir, firm_config_path, data_root)
    out = sr.slug_dir / "out" / "alpha"
    pdf = make_pdf(["Patient: Alpha Example p1", "Patient: Alpha Example p2", "Patient: Alpha Example p3", PAGE])
    (out / "Exhibit 1 - Example Clinic - 01-20-2026 (Medical Records).pdf").write_bytes(pdf)
    assert manifest.run(sr) == 0
    d = FakeDoorway(_verdict(CHECK1_P4), _look("other"), _look("client"))  # p. 4, then the entry's p. 3
    assert _settle_review(sr, d) == 0, log
    md, notes = _md(sr), _notes(sr)
    assert "chronic pain before the incident" in md and "Continue therapy." in md and _left(sr) == []
    assert "prior-history paragraph" not in notes and review_evidence.KEPT_NOTE in notes
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert [f["finding"] for f in rec["findings"]] == [review_evidence.KEPT_NOTE]


def test_n1_a_failed_check1_look_after_the_review_exits_75_and_never_notes_it_checked(
    job_dir, firm_config_path, data_root
):
    sr, _log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    before = _md(sr)
    d1 = _Down(_verdict(CHECK1), error=CallDeadlineError("blip"))
    assert _settle_review(sr, d1) == review.EXIT_INFRA
    assert _md(sr) == before and not (sr.slug_dir / "runs" / "alpha" / whose_page.CACHE).exists()
    d2 = FakeDoorway(_look("client"))
    assert _settle_review(sr, d2) == 0
    assert len(d2.calls) == 1 and _main(d2.calls) == []
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["must_fix"] == 0 and rec["findings"][0]["finding"] == review_evidence.KEPT_NOTE


def test_a_limit_hold_on_a_look_is_a_limit_hold(job_dir, firm_config_path, data_root):
    sr, _log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    _reader(sr)
    hold = LimitHold("levers.cap_usd", "levers.cap_usd: the job's cap is reached")
    with pytest.raises(LimitHold):
        _settle_review(sr, _Down(CLEAN, error=hold))


HELD_CHECK1 = "| MUST-FIX | 1 another person's records | the 05/05/2026 visit is another patient's | entry 05/05/2026 |"


def test_a_held_reviews_resume_with_a_failed_look_exits_75_and_pays_no_review(job_dir, firm_config_path, data_root):
    """Held on a check 1 no entry or page can be found for; the entry reader's
    flag arrives before the resume, whose look fails, then succeeds."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    assert _settle_review(sr, FakeDoorway(_verdict(HELD_CHECK1))) == review.EXIT_HELD, log
    _reader(sr)
    before = _md(sr)
    d1 = _Down(error=CallDeadlineError("blip"))
    assert _settle_review(sr, d1) == review.EXIT_INFRA
    assert _md(sr) == before and _main(d1.calls) == []
    d2 = FakeDoorway(_look("other"))
    assert _settle_review(sr, d2) == review.EXIT_HELD, "the unplaceable check 1 still holds"
    assert _main(d2.calls) == [] and len(d2.calls) == 1
    assert "Continue therapy." not in _md(sr)


CHECK1_MISCITED = (
    "| MUST-FIX | 1 another person's records | the 03/30/2026 visit is another patient's "
    "| entry 03/30/2026 Example Clinic, Exhibit 1 p. 2 |"
)


def test_p5_a_check1_naming_the_clients_page_is_kept_and_filed(job_dir, firm_config_path, data_root):
    """It names p. 2, which the 01/20 entry cites: decided on p. 2, as main did."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    _pages(sr, PAGE)
    assert _settle_review(sr, FakeDoorway(_verdict(CHECK1_MISCITED), _look("client"))) == 0, log
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["must_fix"] == 0 and "Continue therapy." in _md(sr)


CHECK1_UNCITED = (
    "| MUST-FIX | 1 another person's records | the 03/30/2026 visit is another patient's "
    "| entry 03/30/2026 Example Clinic, Exhibit 1 p. 1 |"
)


def test_a_check1_naming_a_page_no_entry_cites_also_reads_its_entrys_pages(job_dir, firm_config_path, data_root):
    """p. 1 is cited only by the prior history; the 03/30 entry's own p. 3 is
    looked at too, and decides."""
    doc = DOC.replace("**Neck:** chronic pain before the incident. (Exhibit 1 - p. 1)", "**Neck:** chronic pain.")
    sr, log = _package(job_dir, firm_config_path, data_root, doc)
    _pages(sr, PAGE)
    d = FakeDoorway(_verdict(CHECK1_UNCITED), _look("client"), _look("other"))
    assert _settle_review(sr, d) == 0, log
    assert len(d.calls) == 3
    assert [c["messages"][0]["content"][0]["text"].rsplit(" ", 2)[-2:] for c in d.calls[1:]] == [
        ["Page", "1."],
        ["Page", "3."],
    ]
    assert "Continue therapy." not in _md(sr)


def test_a_page_with_no_picture_is_kept_and_says_so(job_dir, firm_config_path, data_root):
    doc = DOC.replace("Continue therapy. (Exhibit 1 - p. 3)", "Continue therapy. (Exhibit 1 - p. 9)")
    row = (
        "| MUST-FIX | 1 another person's records | the visit is another patient's | entry 03/30/2026, Exhibit 1 p. 9 |"
    )
    sr, log = _package(job_dir, firm_config_path, data_root, doc)
    _pages(sr, PAGE)
    d = FakeDoorway(_verdict(row))
    assert _settle_review(sr, d) == 0, log
    assert len(d.calls) == 1, "nothing to show, nothing sent"
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["must_fix"] == 0 and rec["findings"][0]["finding"] == review_evidence.NOT_SHOWN_NOTE
    assert "Continue therapy." in _md(sr) and _policy(sr)["identity_page_not_shown"] == 1


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
    d = FakeDoorway(_verdict(CHECK1_TWO_PAGES), _look("other"), _look("client"))
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
