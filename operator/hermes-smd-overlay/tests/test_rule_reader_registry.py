"""A firm rule is called "in effect" only where something reads it (2026-11-04).

THE DEFECT. Audited on the A&P seat: an administrator installed a rule about
the firm's documents, the seat wrote it to ``specs/classes/work_product/``, and
the person who asked was sent "Your rule is in effect ... work of that kind is
written to it." The firm's documents are rendered by the jobs runner, and
nothing under ``operator/runners/jobs`` reads the spec store, so the sentence
was false the moment it was sent.

THE FIX AND WHAT PINS IT. :data:`shared.output_classes.LIVE_RULE_READER_CLASSES`
names the classes a live reader applies. The install letter says "in effect"
for those and only those; every other class is told the rule is recorded and
passed to SMD, and SMD is paged. The parity tests below are what keep the
registry from drifting into the same false claim one level up.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from shared import output_classes, rule_dispatch, spec_gate

RULE = "7f3a2c1d"
REQUESTER = "paralegal@firm.example"
ADMIN = "admin@firm.example"

_JOBS_RUNNER = Path(__file__).resolve().parents[2] / "runners" / "jobs"

#: Anything that would mean a lane reads the installed spec store.
_SPEC_STORE_READS = re.compile(
    r"spec_manifest|spec_stamp|SMD_SPEC_DIR|specs/classes|output-classes\.json|"
    r"ADJUSTMENTS_HEADING|entries_for_class"
)


class _Recorder:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return type("R", (), {"sent": True, "reason": "", "outcome_unknown": False})()


@pytest.fixture
def pages(monkeypatch):
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(
        rule_dispatch,
        "_page_unread_rule",
        lambda proposal_id, output_class: seen.append((proposal_id, output_class)),
    )
    return seen


def _install(output_class: str) -> dict:
    send = _Recorder()
    result = rule_dispatch.notify_outcome(
        kind="installed",
        proposal_id=RULE,
        text="Briefs open with the procedural posture.",
        requester=REQUESTER,
        by=ADMIN,
        send=send,
        output_class=output_class,
    )
    assert result.sent is True
    (call,) = send.calls
    return call


@pytest.mark.parametrize("output_class", ["work_product", "record", "", "letter"])
def test_a_rule_no_live_reader_applies_is_never_called_in_effect(output_class, pages):
    """FAILS ON THE PRE-FIX CODE: it sent "in effect" for every class alike."""
    call = _install(output_class)
    letter = f"{call['subject']}\n{call['text']}"
    assert "in effect from now" not in letter
    assert "is in effect" not in letter
    assert call["subject"] == f"Your rule is recorded [rule {RULE}]"
    assert "passed to SMD" in call["text"]
    assert call["to"] == [REQUESTER]
    assert pages == [(RULE, output_class)]


def test_the_recorded_letter_names_the_work_in_plain_words(pages):
    text = _install("work_product")["text"]
    assert output_classes.describe("work_product") in text
    assert "—" not in text  # voice rule: no em dashes in client copy


def test_the_recorded_letter_states_no_date(pages):
    text = _install("work_product")["text"]
    assert not re.search(r"\b(\d{4}-\d{2}-\d{2}|today|tomorrow|this week|within)\b", text)


@pytest.mark.parametrize("output_class", sorted(output_classes.LIVE_RULE_READER_CLASSES))
def test_a_rule_a_live_reader_applies_is_still_in_effect(output_class, pages):
    call = _install(output_class)
    assert call["subject"] == f"Your rule is in effect [rule {RULE}]"
    assert "in effect from now" in call["text"]
    assert pages == []


def test_the_registry_is_exactly_the_send_sites_class_map():
    """PARITY. Every class the registry calls live is resolved at a send site a
    turn writes through, and every such class is in the registry."""
    assert output_classes.LIVE_RULE_READER_CLASSES == frozenset(
        spec_gate.RECIPIENT_OUTPUT_CLASSES.values()
    )
    for output_class in output_classes.LIVE_RULE_READER_CLASSES:
        assert output_classes.is_output_class(output_class)
        assert any(
            spec_gate.resolve_output_class(value) == output_class
            for value in spec_gate.RECIPIENT_OUTPUT_CLASSES
        )


def test_lane_document_classes_are_not_in_the_registry():
    assert not output_classes.has_live_rule_reader("work_product")
    assert not output_classes.has_live_rule_reader("record")


def test_the_jobs_runner_still_reads_no_spec_store():
    """The other half of the parity. If a lane starts reading installed rules,
    this fails: add that lane's class to ``LIVE_RULE_READER_CLASSES`` in the
    same PR, so the firm is told the rule is in effect only once it is."""
    if not _JOBS_RUNNER.is_dir():
        pytest.skip("jobs runner not in this checkout")
    readers = [
        str(path.relative_to(_JOBS_RUNNER))
        for path in _JOBS_RUNNER.rglob("*.py")
        if "/tests/" not in str(path) and _SPEC_STORE_READS.search(path.read_text("utf-8"))
    ]
    assert readers == [], f"a lane now reads the spec store: {readers}"
