"""Case-alert recipient routing — references/case-alert-routing.md in code.

CANONICAL COPY. This file is the one source; the byte-identical vendored copy
in ``operator/skills/client-verification-tracker/routing.py`` is pinned by
``test_routing_sync.py`` (the escalation_ledger vendoring precedent). Edit
here, then restamp the copy.

The algorithm (case-alert-routing.md, verbatim in code):

* Absent ``escalation.case_alert_routing`` block, or ``mode: central`` ->
  every matter routes to ``escalation.red_flag_recipients``, leg ``central``
  (today's behavior, unchanged).
* ``mode: matter_staff`` -> each matter routes to its resolved, usable,
  ROSTER-GRANTED staff; unresolvable matters take the authored fallback; an
  unauthored fallback is the fail-closed floor (NO delivery for that matter,
  listed ``unroutable`` so the gap is surfaced, never silently dropped).

THE ROSTER IS THE HARD RULE (step 4), and it is the recipient classifier's
OWN authored surface: ``scope.inbound_allow_from`` in customer.yaml, matched
with the classifier's exact grant semantics (an exact address entry, or an
``@domain`` entry granting the whole domain; case-insensitive; malformed
entries grant nothing). Probed 2026-08-31: the overlay's
``shared/recipient_classifier.py`` is the authority for that key and those
semantics — this module mirrors them and MUST NOT invent a new grant key. An
address Smokeball returns that no authored grant covers is UNRESOLVABLE ->
fallback. Never grow the roster from runtime data.

Stdlib only. ``resolve_case_alert_routing`` is pure (staff data is passed
in); the staff PULL that feeds it (``pull_matter_staff`` + its authored
budget) lives here too, so both vendoring skills share one pull and one
resolution — a CVT that routed with an empty staff map under matter_staff
sent every alert down the fallback leg and memoed staffed matters as
"unassigned" (the WS-RENDER review's finding 2).
"""

from __future__ import annotations

import json
import os
import subprocess
import unicodedata
from dataclasses import dataclass
from typing import Sequence

#: The unknown-matter sentinel ``pre_run._matter_id_of`` emits. It names no
#: real matter, so it has no staff to resolve and can take no memo: it routes
#: like a CENTRAL item (the red-flag recipients are the people who triage
#: unidentifiable records), falling back like any other matter, and the
#: envelope builders exclude it from memo/unroutable name lists.
UNKNOWN_MATTER = "unknown-matter"

LEG_CENTRAL = "central"
LEG_RESPONSIBLE = "matter_staff_responsible"
LEG_ASSISTING = "matter_staff_assisting"
LEG_FALLBACK = "fallback"


@dataclass(frozen=True)
class RoutedRecipient:
    emails: tuple[str, ...]
    routing_leg: str  # central | matter_staff_responsible | matter_staff_assisting | fallback


@dataclass(frozen=True)
class RoutingResult:
    routed: dict[str, RoutedRecipient]  # matter_id -> recipients
    unroutable: tuple[str, ...]  # matter_ids hitting the fail-closed floor


def _canonical_address(raw: object) -> str | None:
    """The classifier's strict bare-address canonicalization, mirrored.

    NFC + lowercase; no display names, brackets, quotes, whitespace or lists;
    exactly one ``@``; a dotted domain with no empty labels. Anything else is
    None — a lenient parse here would be a spoofing surface."""
    if not isinstance(raw, str):
        return None
    s = unicodedata.normalize("NFC", raw).strip().lower()
    if not s or any(ch in s for ch in ("<", ">", '"', " ", "\t", ",", ";", "\n", "\r")):
        return None
    if s.count("@") != 1:
        return None
    local, _, domain = s.partition("@")
    if not local or not domain:
        return None
    labels = domain.split(".")
    if len(labels) < 2 or any(label == "" for label in labels):
        return None
    return f"{local}@{domain}"


def _canonical_grant(entry: object) -> str | None:
    """One roster grant: ``@domain`` or a single address; malformed -> None."""
    if not isinstance(entry, str):
        return None
    s = unicodedata.normalize("NFC", entry).strip().lower()
    if not s or any(ch in s for ch in ("<", ">", '"', " ", "\t", ",", ";", "\n", "\r")):
        return None
    if s.startswith("@"):
        domain = s[1:]
        labels = domain.split(".")
        if len(labels) < 2 or any(label == "" for label in labels):
            return None
        return f"@{domain}"
    return _canonical_address(s)


def _granted(address: str, grants: Sequence[str]) -> bool:
    """The hard rule: a resolved address delivers only under an authored grant."""
    canon = _canonical_address(address)
    if canon is None:
        return False
    _, _, domain = canon.partition("@")
    for entry in grants:
        grant = _canonical_grant(entry)
        if grant is None:
            continue
        if grant.startswith("@"):
            if domain == grant[1:]:
                return True
        elif canon == grant:
            return True
    return False


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str) and v.strip()]


def _usable_staff_email(record: object) -> str | None:
    """A staff record's delivery-address candidate, or None when UNPOPULATED.

    Disabled or departed staff (``enabled: false`` / ``former: true`` — the
    actual staff-record fields) are UNPOPULATED, same as absent (steps 1-2)."""
    if not isinstance(record, dict):
        return None
    if record.get("enabled") is False or record.get("former") is True:
        return None
    email = record.get("email")
    if isinstance(email, str) and email.strip():
        return email.strip()
    return None


def resolve_case_alert_routing(
    customer_yaml: dict,
    matter_staff: dict[str, dict],
    matter_ids: Sequence[str],
) -> RoutingResult:
    """Resolve each matter's alert recipients per case-alert-routing.md.

    ``matter_staff`` maps matter_id -> ``{"responsible": <staff record|None>,
    "assisting": [<staff record>...]}`` as pulled by the caller (absent entry =
    UNPOPULATED). ``customer_yaml`` is the parsed trusted-volume config."""
    data = customer_yaml if isinstance(customer_yaml, dict) else {}
    esc = data.get("escalation") if isinstance(data.get("escalation"), dict) else {}
    red_flag = _string_list(esc.get("red_flag_recipients"))
    routing = esc.get("case_alert_routing")
    routing = routing if isinstance(routing, dict) else {}
    mode = routing.get("mode")
    scope = data.get("scope") if isinstance(data.get("scope"), dict) else {}
    grants = _string_list(scope.get("inbound_allow_from"))
    fallback = _string_list(routing.get("fallback_recipients"))

    routed: dict[str, RoutedRecipient] = {}
    unroutable: list[str] = []

    if mode != "matter_staff":
        # Absent block or central: today's behavior. No authored red-flag
        # recipient is the escalator's own fail-closed notify rule — the
        # matter is unroutable and the caller surfaces the gap.
        for matter_id in matter_ids:
            if red_flag:
                routed[matter_id] = RoutedRecipient(tuple(red_flag), LEG_CENTRAL)
            else:
                unroutable.append(matter_id)
        return RoutingResult(routed, tuple(unroutable))

    for matter_id in matter_ids:
        if matter_id == UNKNOWN_MATTER:
            # No real matter -> no staff to resolve. Deliver to the central
            # triage recipients (else fallback); never a per-matter leg, and
            # never a memo target (there is no matter to flag).
            if red_flag:
                routed[matter_id] = RoutedRecipient(tuple(red_flag), LEG_CENTRAL)
            elif fallback:
                routed[matter_id] = RoutedRecipient(tuple(fallback), LEG_FALLBACK)
            else:
                unroutable.append(matter_id)
            continue
        staff = matter_staff.get(matter_id)
        staff = staff if isinstance(staff, dict) else {}
        responsible = _usable_staff_email(staff.get("responsible"))
        assisting = [
            email for record in (staff.get("assisting") or []) if (email := _usable_staff_email(record)) is not None
        ]
        # Recipient set (step 3): the responsible attorney always. Assisting
        # staff join only where the skill's own body routes paralegal-class
        # work to them; the deadline alert is attorney-class, so the assisting
        # leg fires only when there is NO responsible attorney and at least
        # one usable assisting person (a resolved person beats the fallback).
        recipients: list[str] = []
        leg = LEG_RESPONSIBLE
        if responsible is not None and _granted(responsible, grants):
            recipients = [responsible]
        elif responsible is None:
            granted_assisting = [a for a in assisting if _granted(a, grants)]
            if granted_assisting:
                recipients = granted_assisting
                leg = LEG_ASSISTING
        # A responsible attorney whose address no grant covers is UNRESOLVABLE
        # (step 4) — the fallback path, never an ungranted delivery.
        if recipients:
            routed[matter_id] = RoutedRecipient(tuple(recipients), leg)
        elif fallback:
            routed[matter_id] = RoutedRecipient(tuple(fallback), LEG_FALLBACK)
        else:
            # Step 6, the fail-closed floor: no delivery for this matter; the
            # caller lists it unroutable and the woken turn flags the matter
            # in place (create_memo) so a person sees the routing gap.
            unroutable.append(matter_id)

    return RoutingResult(routed, tuple(unroutable))


# ---------------------------------------------------------------------------
# The staff pull that feeds matter_staff resolution. Shared by both vendoring
# skills (one pull, one resolution); connector-venv subprocess, matter ids
# over STDIN, bounded by the authored ``escalation.staff_lookup_budget``.
# ---------------------------------------------------------------------------

_CONNECTOR_PYTHON_DEFAULT = "/opt/connectors/smokeball/.venv/bin/python"
_STAFF_PULL_TIMEOUT_SECONDS = 60
DEFAULT_STAFF_LOOKUP_BUDGET = 50

# Runs inside the connector venv. Matter ids arrive on STDIN as a JSON list —
# never argv (the pre_run nosemgrep contract keeps argv free of pulled data).
# Absent/disabled/former staff are reported as-is; the pure resolution above
# decides usability. Errors are per-matter and wholesale, never fatal: a staff
# pull that dies must not kill the alert (central/fallback still route).
_STAFF_PULL_SNIPPET = """\
import json
import sys

from smokeball_connector.client import build_client_from_env

matter_ids = json.load(sys.stdin)
client = build_client_from_env()
out = {}
for matter_id in matter_ids:
    entry = {"responsible": None, "assisting": []}
    try:
        matter = client.get(f"/matters/{matter_id}")
        if not isinstance(matter, dict):
            matter = {}
        staff_ids = []
        rid = matter.get("personResponsibleStaffId")
        if isinstance(rid, str) and rid:
            staff_ids.append(("responsible", rid))
        for key in ("personAssistingStaffIds", "personAssistingStaffId", "personAssistingStaffs"):
            raw = matter.get(key)
            if isinstance(raw, str) and raw:
                staff_ids.append(("assisting", raw))
            elif isinstance(raw, list):
                for sid in raw:
                    if isinstance(sid, str) and sid:
                        staff_ids.append(("assisting", sid))
        for kind, sid in staff_ids:
            try:
                staff = client.get(f"/staff/{sid}")
            except Exception as exc:
                entry.setdefault("errors", []).append(str(exc)[:200])
                continue
            if not isinstance(staff, dict):
                continue
            record = {
                "email": staff.get("email"),
                "enabled": staff.get("enabled"),
                "former": staff.get("former"),
            }
            if kind == "responsible":
                entry["responsible"] = record
            else:
                entry["assisting"].append(record)
    except Exception as exc:
        entry["error"] = str(exc)[:200]
    out[matter_id] = entry
print(json.dumps(out, default=str))
"""


def staff_lookup_budget(customer_yaml: dict) -> int:
    """The authored ``escalation.staff_lookup_budget``, else the default.
    Zero is legitimate (disables the pull — the staging lever); junk takes
    the default."""
    data = customer_yaml if isinstance(customer_yaml, dict) else {}
    esc = data.get("escalation") if isinstance(data.get("escalation"), dict) else {}
    raw = esc.get("staff_lookup_budget")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return DEFAULT_STAFF_LOOKUP_BUDGET
    return raw


def pull_matter_staff(matter_ids: Sequence[str], budget: int) -> dict[str, dict]:
    """Pull staff assignments for ``matter_ids`` (first ``budget`` of them) in
    the connector venv. Any failure returns what resolved; an absent entry is
    UNPOPULATED and routes to the fallback path — fail toward a person."""
    ids = [m for m in matter_ids if isinstance(m, str) and m and m != UNKNOWN_MATTER]
    ids = ids[: max(0, budget)]
    if not ids:
        return {}
    connector_python = os.environ.get("SMD_CONNECTOR_VENV_PYTHON", _CONNECTOR_PYTHON_DEFAULT)
    try:
        result = subprocess.run(  # noqa: S603 - connector-venv interpreter and a module-constant snippet, no shell; the ids ride stdin
            # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args — argv[0] is the module-constant connector-venv interpreter, overridable only via SMD_CONNECTOR_VENV_PYTHON from the Machine's own boot env (same trust domain; the test seam — the pre_run pulls carry the identical justification). The snippet is a module constant; the matter ids ride STDIN, never argv.
            [connector_python, "-c", _STAFF_PULL_SNIPPET],
            input=json.dumps(ids),
            capture_output=True,
            text=True,
            timeout=_STAFF_PULL_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            return {}
        raw = json.loads((result.stdout or "").strip().splitlines()[-1])
        return raw if isinstance(raw, dict) else {}
    except Exception:  # noqa: BLE001 — a staff pull must never kill the alert
        return {}
