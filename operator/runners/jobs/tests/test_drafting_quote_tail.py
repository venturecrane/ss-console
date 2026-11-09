"""quote_tail: words after a quotation that copy the record but change it.

Synthetic text in the shape of the 2026-11-09 case (a pleaded defense quoted
verbatim, its next words carried on outside the quote marks with "alleged"
dropped)."""

from __future__ import annotations

from jobs.drafting import compose, quote_tail
from jobs.drafting.repair_guard import rows

RECORD = (
    "THIRTY-THIRD AFFIRMATIVE DEFENSE. Defendant did not own, lease, occupy, possess, maintain, "
    "manage, or control the premises where the alleged incident occurred. Defendant reserves "
    "the right to amend."
)
CLAUSE = "did not own, lease, occupy, possess, maintain, manage, or control the premises"


def _req(tail: str) -> str:
    return f'## INTERROGATORY NO. 46\n\nState all facts supporting your contention that you "{CLAUSE}"{tail}.\n'


def test_dropped_word_after_the_quote_is_a_finding():
    rows_ = quote_tail.findings(RECORD, _req(" where the INCIDENT occurred"))
    assert len(rows_) == 1
    assert rows_[0]["draft"] == "where the INCIDENT occurred"
    assert "alleged" in rows_[0]["record"]


def test_the_corrected_text_passes():
    assert quote_tail.findings(RECORD, _req(" where the alleged incident occurred")) == []


def test_the_drafts_own_words_after_a_quote_pass():
    assert quote_tail.findings(RECORD, _req(", and IDENTIFY each PERSON with knowledge of those facts")) == []
    assert quote_tail.findings(RECORD, _req("")) == []


def test_an_added_word_and_a_swapped_word_are_findings():
    assert quote_tail.findings(RECORD, _req(" where the alleged dangerous incident occurred"))
    assert quote_tail.findings(RECORD, _req(" where the subject incident occurred"))


def test_a_quote_not_in_the_record_is_left_to_the_verbatim_check():
    doc = '## X\n\nAdmit that you "never inspected any part of the school grounds" where the incident occurred.\n'
    assert quote_tail.findings(RECORD, doc) == []


def test_the_finding_locates_its_request_for_repair_and_the_guard():
    doc = _req(" where the INCIDENT occurred")
    mech_lines = quote_tail.lines(quote_tail.findings(RECORD, doc))
    audit = (
        "# Audit v1\n\nTallies: {}\n\n## MECHANICAL: quotations not found verbatim in the corpus\n"
        + "\n".join(mech_lines)
        + "\n\n## AUDIT: INTERROGATORY NO. 46\n\nSUPPORTED=1 DRIFTS=0 INVENTED=0 ARITHMETIC=0\n"
    )
    flagged, _ = compose.repair_scope(doc, audit, "## d | a.pdf\n\nx\n")
    assert len(flagged) == 1
    assert "| DRIFTS |" in flagged[0][2]
    assert "alleged" in flagged[0][2]
    mech = [f for f, _ln in rows(audit) if f[0] == "MECHANICAL"]
    assert len(mech) == 1
    assert quote_tail.anchor(mech[0][2]) == CLAUSE[:60]
