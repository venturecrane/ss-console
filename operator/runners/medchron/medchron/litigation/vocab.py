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
NO_ANSWER = (NOT_SERVED, OUT_FOR_SERVICE, SERVED_NO_ANSWER, APPEARED_NO_ANSWER)

FIRM_ROLES = ("plaintiff", "defense")

#: Field groups: what a read is asked to (re)determine for one matter.
GROUP_CASE = "case"
GROUP_DEFENDANTS = "defendants"
GROUP_DISCOVERY = "discovery"
GROUPS = (GROUP_CASE, GROUP_DEFENDANTS, GROUP_DISCOVERY)
GROUP_FIELDS = {
    GROUP_CASE: ("case_name", "court", "case_number", "firm_role", "case_status", "complaint_filed", "next_court_date"),
    GROUP_DEFENDANTS: ("defendants",),
    GROUP_DISCOVERY: ("discovery_propounded", "discovery_served_on_client"),
}

#: The internal document reference the model cites by. It is never client
#: text: the leak scan refuses a workbook that carries one.
DOC_REF = "[doc {n}]"
