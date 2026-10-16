"""Hold classes: the firm-safe name of why a job stopped, and who owns it.

WHY (2026-10-13). A litigation job held at its gates woke the DELIVER turn with
counts and "stopped at gates". The skill said "surface and ask", so the
Operator improvised and emailed the firm to "resubmit the same request, or send
it to SMD". Nothing the firm could do would have moved that job: the hold was
caused by our own seed data. A hold our side caused is OUR failure; the firm
hears what is on the matter now, that nothing is needed from it, and that SMD
is finishing it. The firm is asked something only when the answer is genuinely
theirs (a record in the file that names someone else, two chronologies that
could each be the current one).

``classify`` turns a job's coded reason into two things:

* the **hold class**, a closed, firm-safe token the DELIVER wake carries as
  ``Hold class: <token>.`` and each skill maps to fixed wording. It never
  carries the reason's text, a gate name or a document's words.
* the **page code** (``gate.CODES``), or None, under which the daemon pages SMD
  through ``proof/watch.alert``. The technical reason rides only there, in the
  alert's detail and the job row SMD reads.
"""

from __future__ import annotations

#: The firm-safe tokens. Each skill (litigation-status, medical-chronology-
#: maintainer) carries wording for every one; ``tests/hold-class-wording``
#: pins that.
SMD_FINISHING = "smd_finishing"  # ours; nothing new is on the matter
SMD_FINISHING_FILED = "smd_finishing_filed"  # ours; the update is filed, the earlier version still beside it
ALREADY_CURRENT = "already_current"  # nothing to add: the delivered chronology already covers the new records
NOTHING_IN_SCOPE = "nothing_in_scope"  # no open litigation matter fits what was asked
ANOTHER_PERSON = "another_person"  # a record in the file names someone other than the client (ask)
WHICH_CHRONOLOGY = "which_chronology"  # two chronologies could each be the current one (ask)
REROUTED_TO_UPDATE = "rerouted_to_update"  # a RUN on a delivered matter; the agent resubmits it as the UPDATE
STAFF_UNRESOLVED = "staff_unresolved"  # a staff reassignment names someone the staff list cannot settle (ask)

CLASSES = frozenset(
    {
        SMD_FINISHING,
        SMD_FINISHING_FILED,
        ALREADY_CURRENT,
        NOTHING_IN_SCOPE,
        ANOTHER_PERSON,
        WHICH_CHRONOLOGY,
        REROUTED_TO_UPDATE,
        STAFF_UNRESOLVED,
    }
)

#: Classes whose answer is the firm's. Every other class is ours or an answer.
FIRM_QUESTION = frozenset({ANOTHER_PERSON, WHICH_CHRONOLOGY, STAFF_UNRESOLVED})

#: Reasons already paged under their own code by ``watch.settle``.
PROOF_PREFIXES = ("review_held", "readback_mismatch", "readback_unproven", "dry_run_reported")

_LIMIT_SETTINGS = ("per_job_cap_usd", "chronology_package_page_allowance_per_month", "monthly_budget_usd", "limit:")

#: (substring of an ``update_held:`` reason, class, page code). First match wins.
_UPDATE_HELD = (
    ("nothing new to", ALREADY_CURRENT, None),
    ("name a different person", ANOTHER_PERSON, "identity_held"),
    ("carry the newest date", WHICH_CHRONOLOGY, None),
    ("could not be retired", SMD_FINISHING_FILED, "upload_held"),
    ("still reads present after its delete", SMD_FINISHING_FILED, "upload_held"),
    ("is no longer in its folder", SMD_FINISHING, "upload_held"),
    ("not every new file is proven", SMD_FINISHING, "upload_held"),
)

_LITIGATION = {
    "scope_empty": (NOTHING_IN_SCOPE, None),
    "staff_unresolved": (STAFF_UNRESOLVED, None),
    "gate_refused": (SMD_FINISHING, "gate_held"),
    "parity_hold": (SMD_FINISHING, "gate_held"),
    "leak_found": (SMD_FINISHING, "gate_held"),
    "filing_refused": (SMD_FINISHING, "upload_held"),
}


def _code(reason: str) -> str:
    return reason.split(":", 1)[0].strip()


def _medchron(reason: str) -> tuple[str, str | None]:
    if reason.startswith("refused: list_matter:") and "already holds a delivered chronology" in reason:
        # ss-console#3177: the runner refused a RUN on a matter whose files hold
        # the delivered chronology, before anything was read or charged, and
        # named the update shape. The DELIVER turn resubmits it as the UPDATE;
        # nothing of ours is broken, so nothing pages.
        return REROUTED_TO_UPDATE, None
    if reason.startswith("refused: "):
        return SMD_FINISHING, "stage_refused"
    if reason.startswith(_LIMIT_SETTINGS):
        return SMD_FINISHING, "limit_held"
    if reason.startswith("update_held:"):
        head = reason.split("; last output:", 1)[0]
        for needle, cls, code in _UPDATE_HELD:
            if needle in head:
                return cls, code
        return SMD_FINISHING, "update_held"
    if reason.startswith("upload:"):
        return SMD_FINISHING, "upload_held"
    if reason.startswith("identity:"):
        return ANOTHER_PERSON, "identity_held"
    return SMD_FINISHING, "gate_held"


def classify(lane: str, reason: str | None) -> tuple[str, str | None]:
    """``(hold class, page code or None)`` for a HELD job's reason.

    On the chronology and litigation lanes a reason this module does not know
    is ours (``smd_finishing``) and pages: an unrecognised hold must never reach
    the firm as a question, and never go unseen by SMD."""
    text = str(reason or "").strip()
    if _code(text) in PROOF_PREFIXES or text.startswith(tuple(f"refused: {p}" for p in PROOF_PREFIXES)):
        return SMD_FINISHING, None  # watch.settle pages these under their own code
    if lane == "litigation":
        return _LITIGATION.get(_code(text), (SMD_FINISHING, "gate_held"))
    if lane == "medchron":
        return _medchron(text)
    # Demand, drafting and negotiation holds are not classified here yet: their
    # skills carry their own hold wording and their paging is unchanged.
    return SMD_FINISHING, None


def wake_line(lane: str, reason: str | None) -> str:
    """The DELIVER wake's firm-safe line: a token from ``CLASSES``, nothing else."""
    return f"Hold class: {classify(lane, reason)[0]}."


__all__ = [
    "ALREADY_CURRENT",
    "ANOTHER_PERSON",
    "CLASSES",
    "FIRM_QUESTION",
    "NOTHING_IN_SCOPE",
    "REROUTED_TO_UPDATE",
    "SMD_FINISHING",
    "SMD_FINISHING_FILED",
    "STAFF_UNRESOLVED",
    "WHICH_CHRONOLOGY",
    "classify",
    "wake_line",
]
