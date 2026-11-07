"""The read-back: each filed firm document scored against its own record, in
code, with no model call (design 2026-11-06, piece 10).

WHY. The live proof of the document library is the first real request, not a
paid test run. Every drafting and demand job writes ``library-resolution.json``
beside what it made (``operator_connector_sdk.library.caller``): which Word
file, whose name, any firm edit used. This scores the FILED bytes against that
record, five tests, each answered with what was measured:

* (a) ``made_in_file``: the filed document's package measures to the recorded
  accepted file or recorded fallback (``measure.compare``);
* (b) ``attorney``: its signature or caption block is the roster's entry for the
  recorded attorney, or the list the lane read before the roster
  (``attorney.py``);
* (c) ``reader``: the record names a reader and the profile applied. No record
  carries one yet (design piece 7), so it is ``not_recorded``, never a failure;
* (d) ``firm_edit``: when the record names a firm's own edit, the filed
  document measures to the version used (or, refused, to the accepted copy);
* (e) ``rules``: every recorded rule in force that code can check holds in the
  filed document; the rest are listed by id, not scored (``rules.py``).

EVERY VERDICT is one of :data:`VERDICTS` with the values measured. ``fail`` is
a measured disagreement; ``not_recorded`` is a record or a file the test needs
and the seat does not have; ``not_applicable`` is a job the test does not ask
about. Nothing is guessed.

WHERE IT RUNS. The root daemon, after a delivered drafting or demand job's child
(``lane.after``), writes ``library-score.json`` beside the record. Behind the
seat's ``logic_in_code.library_readback`` switch: ``observe`` (every seat's
default) writes the score and pages nothing; ``enforce`` also pages SMD once per
job on a failed test (``library_readback_failed``). Nothing is ever sent to the
firm. SMD reads a seat's state with ``jobs library-readback status``
(``operator/bin/library-readback.sh``) and scores a saved job folder at $0 with
``jobs library-readback replay``.
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = 1
#: The score's file name, beside the record in a job's data folder.
SCORE_NAME = "library-score.json"
#: The proof alert an enforcing seat pages SMD with (``proof/gate.py`` CODES).
CODE = "library_readback_failed"

PASS = "pass"
FAIL = "fail"
NOT_APPLICABLE = "not_applicable"
NOT_RECORDED = "not_recorded"
VERDICTS = (PASS, FAIL, NOT_APPLICABLE, NOT_RECORDED)

MADE_IN_FILE = "a_made_in_file"
ATTORNEY = "b_attorney"
READER = "c_reader"
FIRM_EDIT = "d_firm_edit"
RULES = "e_rules"
TESTS = (MADE_IN_FILE, ATTORNEY, READER, FIRM_EDIT, RULES)


def verdict(value: str, why: str = "", measured: dict[str, Any] | None = None) -> dict[str, Any]:
    """One test's answer for one document."""
    if value not in VERDICTS:
        raise ValueError(f"unknown verdict {value!r}")
    return {"verdict": value, "why": why, "measured": dict(measured or {})}


def overall(answers: list[dict[str, Any]]) -> str:
    """One test across a job's filed documents: any fail fails; then any pass;
    then any not_recorded; else not_applicable."""
    seen = {a["verdict"] for a in answers}
    for value in (FAIL, PASS, NOT_RECORDED):
        if value in seen:
            return value
    return NOT_APPLICABLE


__all__ = [
    "ATTORNEY",
    "CODE",
    "FAIL",
    "FIRM_EDIT",
    "MADE_IN_FILE",
    "NOT_APPLICABLE",
    "NOT_RECORDED",
    "PASS",
    "READER",
    "RULES",
    "SCHEMA_VERSION",
    "SCORE_NAME",
    "TESTS",
    "VERDICTS",
    "overall",
    "verdict",
]
