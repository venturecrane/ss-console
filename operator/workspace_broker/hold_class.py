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

CHRONOLOGY LANE, 2026-10-24 (the Captain): the firm hears from the Operator
about a chronology ONLY when it is delivered. A held chronology sends the firm
nothing, so on that lane the class no longer picks firm wording: it picks the
page (every chronology hold pages SMD, ``already_current`` and
``which_chronology`` included, because once the firm stopped hearing about them
nobody else would have) and the one hold that still wakes the DELIVER turn, for
work rather than to notify anyone: ``rerouted_to_update`` (``watch.wake_owed``).
A record that names another person no longer holds a chronology at all: it is
left out by rule and disclosed in the delivered package
(``gates/foreign_identity.withhold_by_rule``).

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
#: maintainer, case-file-manager) carries wording for every one;
#: ``tests/hold-class-wording`` pins that.
SMD_FINISHING = "smd_finishing"  # ours; nothing new is on the matter
SMD_FINISHING_FILED = "smd_finishing_filed"  # ours; the update is filed, the earlier version still beside it
ALREADY_CURRENT = "already_current"  # legacy hold reason; since 10-25 "nothing new" DELIVERS (update/outcome)
NOTHING_IN_SCOPE = "nothing_in_scope"  # no open litigation matter fits what was asked
ANOTHER_PERSON = "another_person"  # a record in the file names someone other than the client (ask)
WHICH_CHRONOLOGY = "which_chronology"  # two chronologies could each be the current one (ask)
REROUTED_TO_UPDATE = "rerouted_to_update"  # a RUN on a delivered matter; the agent resubmits it as the UPDATE
STAFF_UNRESOLVED = "staff_unresolved"  # a staff reassignment names someone the staff list cannot settle (ask)
REQUESTER_UNRESOLVED = "requester_unresolved"  # "my cases" from an address no staff record carries (ask)

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
        REQUESTER_UNRESOLVED,
    }
)

#: Classes whose answer is the firm's. Every other class is ours or an answer.
FIRM_QUESTION = frozenset({ANOTHER_PERSON, WHICH_CHRONOLOGY, STAFF_UNRESOLVED, REQUESTER_UNRESOLVED})

#: Reasons already paged under their own code by ``watch.settle``.
PROOF_PREFIXES = ("review_held", "readback_mismatch", "readback_unproven", "dry_run_reported")

_LIMIT_SETTINGS = ("per_job_cap_usd", "chronology_package_page_allowance_per_month", "monthly_budget_usd", "limit:")

#: (substring of an ``update_held:`` reason, class, page code). First match wins.
#: "nothing new to" (2026-10-25): an update that concludes nothing new now ends
#: DELIVERED already current with nothing filed (``update/outcome``) and never
#: logs this reason; the row stays so a job held by an earlier runner keeps its
#: class and page. One that could not PROVE nothing new holds as ours instead
#: ("found nothing to add, and ..."), paged under ``update_held``.
_UPDATE_HELD = (
    ("nothing new to", ALREADY_CURRENT, "update_held"),
    ("name a different person", ANOTHER_PERSON, "identity_held"),
    ("carry the newest date", WHICH_CHRONOLOGY, "update_held"),
    ("could not be retired", SMD_FINISHING_FILED, "upload_held"),
    ("still reads present after its delete", SMD_FINISHING_FILED, "upload_held"),
    ("is no longer in its folder", SMD_FINISHING, "upload_held"),
    ("not every new file is proven", SMD_FINISHING, "upload_held"),
)

_LITIGATION = {
    "scope_empty": (NOTHING_IN_SCOPE, None),
    "staff_unresolved": (STAFF_UNRESOLVED, None),
    # 2026-10-24: "my cases" from an address that matches no staff record in
    # Smokeball. Whose files to list is the firm's answer; nothing pages.
    "requester_unresolved": (REQUESTER_UNRESOLVED, None),
    "gate_refused": (SMD_FINISHING, "gate_held"),
    "parity_hold": (SMD_FINISHING, "gate_held"),
    "leak_found": (SMD_FINISHING, "gate_held"),
    "filing_refused": (SMD_FINISHING, "upload_held"),
    # 2026-10-24: the billing cycle's included status lists are used (or the
    # authored cap cannot be read). Nothing ran and nothing was spent; SMD is
    # paged to quote the list before it starts.
    "list_allowance": (SMD_FINISHING, "limit_held"),
}

#: The case file manager (lane ``casefile``). Any other code it emits
#: (envelope_invalid, config_missing, inventory_unreadable, fetch_unfinished,
#: disk_short, read_incomplete, readback_mismatch, stage_unfinished, limit,
#: no_verdict, unexpected, rehearsal, apply_refused, library_unresolved) is ours and pages
#: ``gate_held``, the same default as litigation.
_CASEFILE = {
    "scope_empty": (NOTHING_IN_SCOPE, None),
    "scope_unresolved": (STAFF_UNRESOLVED, None),
    "filing_refused": (SMD_FINISHING, "upload_held"),
    "all_matters_held": (SMD_FINISHING, "gate_held"),
    # An APPLY that found no approved line to write: SMD looks at why.
    "nothing_approved": (SMD_FINISHING, "gate_held"),
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

    On the chronology, litigation and casefile lanes a reason this module does not know
    is ours (``smd_finishing``) and pages: an unrecognised hold must never reach
    the firm as a question, and never go unseen by SMD."""
    text = str(reason or "").strip()
    if _code(text) in PROOF_PREFIXES or text.startswith(tuple(f"refused: {p}" for p in PROOF_PREFIXES)):
        return SMD_FINISHING, None  # watch.settle pages these under their own code
    if lane == "litigation":
        return _LITIGATION.get(_code(text), (SMD_FINISHING, "gate_held"))
    if lane == "casefile":
        return _CASEFILE.get(_code(text), (SMD_FINISHING, "gate_held"))
    if lane == "medchron":
        return _medchron(text)
    # Demand, drafting, negotiation and note holds are not classified here yet:
    # their skills carry their own hold wording and their paging is unchanged.
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
    "REQUESTER_UNRESOLVED",
    "REROUTED_TO_UPDATE",
    "SMD_FINISHING",
    "SMD_FINISHING_FILED",
    "STAFF_UNRESOLVED",
    "WHICH_CHRONOLOGY",
    "classify",
    "wake_line",
]
