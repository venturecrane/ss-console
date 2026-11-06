"""The litigation job's closed vocabularies: case statuses, defendant statuses,
the field groups a read covers, and the result schema's paths.

Product vocabulary, not firm config: every gate, the workbook's tabs and the
parity check key on these exact strings. ``litigation-firm.yaml`` names the
list it uses (``case_statuses``) and the loader refuses one that drops or
invents a value, so a firm cannot silently retire a status the workbook counts.
"""

from __future__ import annotations

ACTIVE = "Active"
SETTLED_OWED = "Settled, dismissal not yet filed"
DISMISSED = "Dismissed/closed"
JUDGMENT = "Judgment entered"
STAYED = "Stayed/arbitration"
NOT_FILED = "Not filed"
UNCLEAR = "Unclear"
CASE_STATUSES = (ACTIVE, SETTLED_OWED, DISMISSED, JUDGMENT, STAYED, NOT_FILED, UNCLEAR)
#: Statuses whose matter can still owe an action (needs-action tab, settlement scan).
OPEN_STATUSES = (ACTIVE, STAYED, NOT_FILED, UNCLEAR)

NOT_SERVED = "Not served"
SERVICE_REJECTED = "Service rejected (not served)"
OUT_FOR_SERVICE = "Out for service"
SERVED_NO_ANSWER = "Served, no answer in file"
APPEARED_NO_ANSWER = "Appeared, no answer in file"
ANSWERED = "Answered"
ANSWERED_ORIGINAL = "Answered original complaint, not the amended complaint"
DEFAULT = "Default entered"
D_DISMISSED = "Dismissed"
D_SETTLED = "Settled"
DROPPED = "Dropped by amendment"
UIM = "UIM arbitration respondent (not a court defendant)"
FIRM_CLIENT = "Firm's client (firm is defense counsel)"
D_UNCLEAR = "Unclear"
DEFENDANT_STATUSES = (
    NOT_SERVED,
    SERVICE_REJECTED,
    OUT_FOR_SERVICE,
    SERVED_NO_ANSWER,
    APPEARED_NO_ANSWER,
    ANSWERED,
    ANSWERED_ORIGINAL,
    DEFAULT,
    D_DISMISSED,
    D_SETTLED,
    DROPPED,
    UIM,
    FIRM_CLIENT,
    D_UNCLEAR,
)
#: A defendant in one of these needs nothing from the firm right now.
DEFENDANT_DONE = (ANSWERED, D_DISMISSED, D_SETTLED, DROPPED, UIM, DEFAULT)
#: Statuses that assert there is NO answer: an answer date contradicts them.
NO_ANSWER = (NOT_SERVED, SERVICE_REJECTED, OUT_FOR_SERVICE, SERVED_NO_ANSWER, APPEARED_NO_ANSWER)
#: What a read reports for a defendant: each dated, each with its document.
#: Code derives the status from these (``defstatus.derive``); no read returns one.
DEFENDANT_EVENTS = (
    "served",
    "answered",
    "sent_for_service",
    "appeared",
    "service_rejected",
    "dismissed",
    "settled",
    "dropped",
    "default_entered",
    "default_set_aside",
)
#: The events the stored shape had no field for until the derivation. While the
#: seat's switch is ``observe`` a read's answers for these (and a role) ride apart
#: from the list's values, in ``shadow`` on the defendant (``statusshadow``).
SHADOW_EVENTS = tuple(f for f in DEFENDANT_EVENTS if f not in ("served", "answered", "sent_for_service"))
#: A party's standing in the case, which no dated event carries.
ROLE_UIM = "uim_respondent"
ROLE_FIRM_CLIENT = "firm_client"
DEFENDANT_ROLES = (ROLE_UIM, ROLE_FIRM_CLIENT)
#: ``status_from`` on a stored defendant: ``derived`` came from its events;
#: ``seed`` is a hand run's text; no key is a status a read asserted before the
#: derivation existed, on a report already stored on a seat (``defstatus``).
FROM_DERIVED = "derived"
FROM_SEED = "seed"

FIRM_ROLES = ("plaintiff", "defense")

#: Field groups: what a read is asked to (re)determine for one matter.
GROUP_CASE = "case"
GROUP_DEFENDANTS = "defendants"
GROUP_DISCOVERY = "discovery"
GROUPS = (GROUP_CASE, GROUP_DEFENDANTS, GROUP_DISCOVERY)
#: Parity paths of the discovery group (``parity.points``).
DISCOVERY_PATHS = ("discovery_propounded[", "discovery_served_on_client[")
GROUP_FIELDS = {
    GROUP_CASE: ("case_name", "court", "case_number", "firm_role", "case_status", "complaint_filed", "next_court_date"),
    GROUP_DEFENDANTS: ("defendants",),
    GROUP_DISCOVERY: ("discovery_propounded", "discovery_served_on_client"),
}

#: The internal document reference the model cites by. It is never client
#: text: the leak scan refuses a workbook that carries one.
DOC_REF = "[doc {n}]"
