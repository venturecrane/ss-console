"""The ten mechanical gates, run against a filled draft (ss-console#2258).

THESE TESTS RUN THE REAL CHECKER. ``drafting_gate_check.py`` is stdlib-only with
no install step, so there is no reason to fake it — and every reason not to. The
defect this module closes is a gate that was believed to run and did not; a test
suite that mocked the subprocess would be asserting the same belief.

The disposition table is the contract, and the row that matters most is exit 2.
The checker exits 2 (not 1) when it finds no readable sources, and a Smokeball
matter is PDFs — so "no readable sources" is the LIKELY runtime case. A rule of
"refuse on FAIL" reads exit 2 as not-a-FAIL and delivers an unchecked draft,
which is exactly the failure being closed. Every non-pass row has a test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smokeball_connector import record_check
from smokeball_connector.record_check import RecordCheckResult, run_record_check

# The real checker, from this repo. Present in a checkout and on a seat; the
# module's env override is what points production at the seat copy.
_REAL_CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"

_SOURCE = (
    "BILLING SUMMARY BY PROVIDER\n\n"
    "Kaiser Permanente Sacramento, emergency department: $6,240.00\n"
    "Sierra Imaging Associates, MRI lumbar spine: $3,150.00\n\n"
    'The treating physician wrote "the disc extrusion displaces the traversing '
    'right L5 nerve root" in the report of December 8, 2025.\n'
)

_CLEAN_DRAFT = (
    "# DEMAND LETTER DRAFT\n\n"
    "The MRI billed $3,150.00 per the billing summary on the matter.\n\n"
    "{{ATTORNEY: decision reserved - the demand figure}}\n"
)

_FABRICATED_QUOTE_DRAFT = (
    "# DEMAND LETTER DRAFT\n\n"
    'The treating physician wrote "the injury was catastrophic and permanent" '
    "in the report of December 8, 2025.\n"
)


@pytest.fixture(autouse=True)
def _point_at_the_real_checker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(record_check.CHECKER_PATH_ENV, str(_REAL_CHECKER))


def test_the_real_checker_is_actually_present() -> None:
    """If this fails, every other test in this file is testing the
    checker-absent path and would pass for the wrong reason."""
    assert _REAL_CHECKER.is_file(), f"checker not found at {_REAL_CHECKER}"


# ---- The pass row -----------------------------------------------------------


def test_a_draft_whose_quotes_trace_passes() -> None:
    result = run_record_check(_CLEAN_DRAFT, [("Billing Summary", _SOURCE)])
    assert result.passed, result.refusals
    assert result.disposition == "pass"
    assert result.checked_sources == 1


# ---- Every non-pass row -----------------------------------------------------


def test_a_quotation_in_no_source_is_REFUSED_with_the_finding() -> None:
    """Gate 2a. This is the check the pilot's draft never ran against."""
    result = run_record_check(_FABRICATED_QUOTE_DRAFT, [("Billing Summary", _SOURCE)])
    assert not result.passed
    assert result.disposition == "fail_findings"
    assert result.refusals, "a FAIL must name its finding, not just refuse"
    assert any("catastrophic" in r for r in result.refusals)


def test_no_sources_REFUSES_rather_than_delivering() -> None:
    """THE row. An empty source list means the checker would exit 2, and a
    'refuse on FAIL' rule would read that as not-a-FAIL and deliver."""
    result = run_record_check(_CLEAN_DRAFT, [])
    assert not result.passed
    assert result.disposition == "no_sources"
    assert "has not been checked" in " ".join(result.refusals)


def test_a_source_that_would_not_extract_REFUSES_and_names_it() -> None:
    """Coverage, not presence. A partial record turns a correctly quoted
    passage into a reported fabrication, which teaches the model to stop
    quoting the record."""
    result = run_record_check(
        _CLEAN_DRAFT,
        [("Billing Summary", _SOURCE)],
        unextractable=["2025-11-02 Traffic Collision Report - CHP 11-79284"],
    )
    assert not result.passed
    assert result.disposition == "source_unextractable"
    assert "CHP 11-79284" in " ".join(result.refusals)
    assert "partial record" in " ".join(result.refusals)


def test_a_missing_checker_REFUSES_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Variant C of the drafting discipline: no gate available on either path
    means nothing surfaces. A seat missing the checker is a provisioning fault,
    and the refusal says so rather than blaming the draft."""
    monkeypatch.setenv(record_check.CHECKER_PATH_ENV, "/nonexistent/drafting_gate_check.py")
    result = run_record_check(_CLEAN_DRAFT, [("Billing Summary", _SOURCE)])
    assert not result.passed
    assert result.disposition == "checker_absent"
    assert "provisioning fault" in " ".join(result.refusals)


def test_a_timeout_REFUSES() -> None:
    """A check that did not finish is not a check that passed."""
    result = run_record_check(_CLEAN_DRAFT, [("Billing Summary", _SOURCE)], timeout=0)
    assert not result.passed
    assert result.disposition == "timeout"


def test_a_checker_that_cannot_be_launched_REFUSES(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a, **_kw):
        raise OSError("no fork for you")

    monkeypatch.setattr(record_check.subprocess, "run", _boom)
    result = run_record_check(_CLEAN_DRAFT, [("Billing Summary", _SOURCE)])
    assert not result.passed
    assert result.disposition == "checker_error"


def test_an_unparseable_report_REFUSES(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreadable verdict is not a pass."""

    class _Proc:
        returncode = 0
        stdout = "not json at all"
        stderr = ""

    monkeypatch.setattr(record_check.subprocess, "run", lambda *a, **k: _Proc())
    result = run_record_check(_CLEAN_DRAFT, [("Billing Summary", _SOURCE)])
    assert not result.passed
    assert result.disposition == "unparseable_report"


def test_exit_two_REFUSES_even_with_no_fail_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The row that a 'refuse on FAIL' rule gets wrong. Exit 2 carries no
    findings at all — the checker never got far enough to produce any — so a
    caller that branched on findings alone would deliver."""

    class _Proc:
        returncode = 2
        stdout = ""
        stderr = "no readable source files under: /tmp/x/sources"

    monkeypatch.setattr(record_check.subprocess, "run", lambda *a, **k: _Proc())
    result = run_record_check(_CLEAN_DRAFT, [("Billing Summary", _SOURCE)])
    assert not result.passed
    assert result.disposition == "checker_usage_error"
    assert "no readable source files" in " ".join(result.refusals)


def test_a_nonzero_exit_with_no_findings_still_REFUSES(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Proc:
        returncode = 7
        stdout = '{"findings": []}'
        stderr = ""

    monkeypatch.setattr(record_check.subprocess, "run", lambda *a, **k: _Proc())
    result = run_record_check(_CLEAN_DRAFT, [("Billing Summary", _SOURCE)])
    assert not result.passed
    assert "exit 7" in " ".join(result.refusals)


# ---- WARN and INFO ride forward --------------------------------------------


def test_warnings_survive_a_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """An attorney should see them, and a WARN that vanishes is worse than a
    noisy one."""

    class _Proc:
        returncode = 0
        stdout = (
            '{"findings": [{"gate": "2a", "severity": "WARN", "line": 4, '
            '"message": "an ellipsis omits words", "detail": "check the omission"},'
            '{"gate": "2b", "severity": "INFO", "line": null, '
            '"message": "pairing could not be evaluated", "detail": null}]}'
        )
        stderr = ""

    monkeypatch.setattr(record_check.subprocess, "run", lambda *a, **k: _Proc())
    result = run_record_check(_CLEAN_DRAFT, [("Billing Summary", _SOURCE)])
    assert result.passed
    assert any("ellipsis" in w for w in result.warnings)
    assert any("pairing" in i for i in result.infos)


def test_warnings_survive_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Proc:
        returncode = 1
        stdout = (
            '{"findings": [{"gate": "2a", "severity": "FAIL", "line": 2, '
            '"message": "quote not in any source", "detail": null},'
            '{"gate": "2a", "severity": "WARN", "line": 4, '
            '"message": "an ellipsis omits words", "detail": null}]}'
        )
        stderr = ""

    monkeypatch.setattr(record_check.subprocess, "run", lambda *a, **k: _Proc())
    result = run_record_check(_CLEAN_DRAFT, [("Billing Summary", _SOURCE)])
    assert not result.passed
    assert any("quote not in any source" in r for r in result.refusals)
    assert any("ellipsis" in w for w in result.warnings)


# ---- The held-out list ------------------------------------------------------


def test_held_out_documents_are_passed_to_the_privilege_gate() -> None:
    """Gate 1's leakage check finally has its input. The pilot's draft built a
    hold-out list unasked and nothing consumed it.

    The draft here quotes the held-out document, which is the leak the gate
    exists to catch.
    """
    privileged = 'Attorney work product: "our exposure on causation is the weak point".\n'
    draft = '# DEMAND LETTER DRAFT\n\nCounsel notes "our exposure on causation is the weak point" in the file.\n'
    result = run_record_check(
        draft,
        [("Billing Summary", _SOURCE), ("Attorney Notes", privileged)],
        held_out_names={"Attorney Notes"},
    )
    assert not result.passed, "quoting a held-out document must not pass"
    # Assert GATE 1 specifically. A held-out document is not in --sources, so a
    # draft quoting it also trips gate 2a ("not contiguous in any source") — and
    # a test that only checked `not passed` would pass identically with the
    # held-out list never wired at all. That is the check-that-cannot-fail shape
    # this whole phase exists to close, so it is not repeated here.
    assert any(r.startswith("[1]") for r in result.refusals), (
        f"gate 1 (privilege leakage) did not fire; refusals were {result.refusals}"
    )
    assert any("Attorney_Notes" in r for r in result.refusals), "the leaked document is named"


def test_the_held_out_list_is_what_makes_gate_1_fire() -> None:
    """The falsifier for the test above. Same draft, same documents, but nothing
    declared held out: gate 1 must NOT fire, because there is no privilege
    boundary to cross. If this also produced a [1] finding, the previous test
    would be proving nothing about the held-out wiring."""
    privileged = 'Attorney work product: "our exposure on causation is the weak point".\n'
    draft = '# DEMAND LETTER DRAFT\n\nCounsel notes "our exposure on causation is the weak point" in the file.\n'
    result = run_record_check(draft, [("Billing Summary", _SOURCE), ("Attorney Notes", privileged)])
    assert not any(r.startswith("[1]") for r in result.refusals), (
        "gate 1 fired without a held-out list, so the list is not what drives it"
    )


def test_a_result_is_a_frozen_record() -> None:
    """The caller branches on `passed` alone; nothing downstream may mutate a
    verdict into a different one."""
    result = run_record_check(_CLEAN_DRAFT, [("Billing Summary", _SOURCE)])
    assert isinstance(result, RecordCheckResult)
    with pytest.raises(Exception):
        result.passed = False  # type: ignore[misc]
