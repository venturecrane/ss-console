"""Broker-side validation and spool marshalling for conversational establishment
(ADR 0085, ss-console #2161/#2162).

WHAT ESTABLISHMENT IS. An Operator admin instructs the Operator to review a
named set of firm documents and establish (or update) the firm's voice or an
output shape from them. The agent stages the corpus it read, submits a derived
spec, and a ROOT intake daemon (overlay ``establish_intake``) runs the
distillation compiler gates before anything is installed. The agent's uid never
touches the spool, R2, or the spec tree — this module is the validation seam the
submission must cross, and the broker uid is the only principal that writes it.

THE FOUR DISCIPLINES, inherited from ``corrections.py`` verbatim:

1. **One pinned action_type per verb.** ``establish_submit`` appends only
   ``ESTABLISHMENT_SUBMITTED``; ``establish_status`` appends only
   ``ESTABLISHMENT_RESULT``. Neither verb can forge a row of any other kind.
2. **Rows and files are REBUILT from a bounded field set, never forwarded.**
   Every stored payload below is constructed field by field from values this
   module read, checked, and (where derivable) computed itself. A field the
   caller invents has nowhere to land.
3. **Server-side constants.** Every content hash is computed broker-side from
   the bytes actually stored. A caller-supplied ``sha256`` on a staged document
   is never read; the manifest hashes at submit time are checked against the
   broker's own recomputation, not trusted from the wire.
4. **Refuse, never sanitize.** A malformed field is a named refusal, not a
   quiet rewrite. The one derived identifier — the staged document's ``name``,
   which the design pins as safe-slugged — is a broker-side DERIVATION like the
   sha256 (the raw name is validated, the slug is computed here, and the raw
   bytes are never stored), not a sanitized passthrough.

WHY THE CORPUS RIDES THE SPOOL. The corpus is not on the seat filesystem (the
publisher ships only customer.yaml; the documents live in the client's systems
and reach the agent through connector reads). The broker frame is one line of
at most 1 MiB, so a multi-document corpus cannot arrive as one payload:
staging is structurally forced, and it is also what hash-binds the submitted
spec to exactly the corpus the agent read.

WHAT THE AUDIT ROWS NEVER CARRY. Corpus text and spec bodies stay in the spool
(purged by the root intake after the run) and in the one-shot result payload.
Retained ledger rows carry document names, hashes, rule ids, and counts — never
the client's prose. That is ADR 0083's retention posture applied to this path.

PROPOSE / READ BACK / CONFIRM (ss-console#2529, ADR 0085 §4 as amended
2026-08-21). A firm also establishes by talking: an admin writes one sentence
about how a kind of output should read, and any person writes one about their
own work. That sentence has no corpus, so it cannot cross the staged-document
path above, and the compilers that gate that path all refuse an empty corpus.
What replaces them is a readback the person answers:

    establish_propose   the sentence is stored PENDING, and the broker returns
                        the canonical block the seat must send verbatim
    establish_pending   what this sender may still confirm
    establish_submit    scope firm_adjust (or person) with the proposal id

THE ROW IS THE AUTHORITY ON WHAT WAS AGREED. At submit, the committed text and
subject come out of the pending row, never off the wire. A request carrying a
different text is refused rather than quietly overwritten, because the person
said yes to a specific sentence and the only way that yes means anything is if
the committed bytes are the bytes they were shown. Consumption is a conditional
UPDATE, so a proposal commits exactly once.

AN OPERATIONS REQUEST IS THE THIRD THING THIS TABLE HOLDS (ss-console#2546,
ADR 0085 as amended 2026-08-23). A routine, a schedule, a channel, a memory
setting, an autonomy level, an on/off — those are SMD's to change, not the
firm's, so the firm cannot confirm one and there is nothing here to commit.
What the row is for is the OTHER half of the loop: somebody asked, SMD was
emailed, and the person who asked has to hear the answer. So an ``ops_request``
row is recorded (``ops_propose``), tagged ``[ops XXXX]`` for SMD to quote back,
and ended by ``ops_resolve`` with one of three words — done, declined,
withdrawn. It is NEVER confirmable: ``establish_submit`` and
``establish_decline`` refuse the kind by name, ``consume`` refuses it in SQL,
and ``open_for`` (the list of what a sender may still confirm) does not return
it at all. Three independent refusals rather than one, because "the firm
accidentally installed a routine change by saying yes" is the failure worth
three.

WHAT THIS MODULE STILL CANNOT SEE. Whether the sender is an Operator admin.
``instructed_by`` remains provenance, never authorization, on every verb here
(the corrections ``stated_by`` posture); the admin gate is seat-side, against
the authored allow list in customer.yaml, which this uid cannot read.
"""


from __future__ import annotations

# ---------------------------------------------------------------------------
# THIS MODULE IS THE IMPORT SURFACE, NOT THE IMPLEMENTATION.
#
# `establishment.py` was 3,509 lines and had grown 692 -> 2,752 logical lines in
# the nine days before 2026-08-24. It was split along the seam its own structure
# already had:
#
#   establishment_constants.py   audit action types, sizes, TTLs, DDL
#   establishment_validation.py  pure validators, normalizers, read-back text
#   pending_rule_store.py        PendingRuleStore — the proposals table
#   establishment_store.py       EstablishmentStore — the spool and its lifecycle
#
# Everything those modules expose is re-exported here, so every existing import
# keeps working untouched: `from .establishment import EstablishmentStore` in
# server.py, the three test modules' symbol lists, and the module-object form
# (`from workspace_broker import establishment`, then `establishment.MAX_...`).
#
# The re-export is not maintained by hand. `tests/test_establishment_surface.py`
# holds a recorded fixture of every public name with its type, class members and
# constant values, and fails if this module's surface drifts from it in either
# direction. Name-set equality would not have been enough: it cannot fail on a
# constant whose literal was mistyped during the move, or a method dropped from a
# relocated class, which are the two ways a split like this actually goes wrong.
# ---------------------------------------------------------------------------

from .establishment_constants import *  # vocabulary and tuning surface
from .establishment_validation import *  # validators and renderers
from .pending_rule_store import PendingRuleStore
from .establishment_store import EstablishmentStore

# The private names too. Nothing outside this package imports them today, but
# `import *` skips underscore names and the recorded surface fixture is asserted
# in BOTH directions — so re-exporting them keeps the guarantee total rather than
# "public names only, and trust me about the rest".
from .establishment_constants import (  # noqa: F401 - re-exported so the surface lockdown covers private names too (see above)
    _CLASS_SLUG_CHARS,
    _ID_PATTERN,
    _MAX_ACT_DISPLAY_NAME,
    _MAX_ASSERTIONS,
    _MAX_ASSERTIONS_BYTES,
    _MAX_CLASS_SLUG,
    _MAX_NAME_INPUT,
    _MAX_NAME_SLUG,
    _MAX_SHORT_TEXT,
    _NAME_SLUG_KEEP,
    _PROPOSAL_ID_PATTERN,
)
from .establishment_validation import (  # noqa: F401 - re-exported so the surface lockdown covers private names too (see above)
    _URL_PATTERN,
    _bounded_str,
    _column,
    _hash_text,
    _optional_text,
    _require_class_slug,
    _require_display_name,
    _require_id,
    _require_property,
    _require_proposal_id,
    _require_text,
)

# Rebuilt 2026-08-24 from the module's actual public surface. The previous
# list had drifted in BOTH directions: it omitted five names the tests import
# (MAX_OUTCOME_REASON, NOTIFY_CLAIM_STALE_SECONDS, and the three OPS_REQUEST_*
# action types, all added by ss#2546) while listing twelve nothing imports. It
# is derived from tests/fixtures/establishment_surface.json, which is the same
# artifact test_establishment_surface.py asserts against, so the two cannot
# drift apart without a test failing.
__all__ = [
    "ACT_COMMITTED_ACTION_TYPE",
    "ACT_CONFIG_KEYS",
    "ACT_NAME_KEYS",
    "ACT_PROPOSED_ACTION_TYPE",
    "ACT_TOOLS",
    "CREATE_PENDING_RULES_INDEX_SQL",
    "CREATE_PENDING_RULES_SQL",
    "ESTABLISHMENT_RESULT_ACTION_TYPE",
    "ESTABLISHMENT_SUBMITTED_ACTION_TYPE",
    "EstablishmentStore",
    "EstablishmentValidationError",
    "MAX_DOCS_PER_SET",
    "MAX_DOC_TEXT_BYTES",
    "MAX_OUTCOME_REASON",
    "MAX_RULE_TEXT_BYTES",
    "MAX_SET_BYTES",
    "MAX_SPEC_BODY_BYTES",
    "NOTIFY_CLAIM_STALE_SECONDS",
    "OPS_OUTCOMES",
    "OPS_REQUEST_KIND",
    "OPS_REQUEST_LAPSED_ACTION_TYPE",
    "OPS_REQUEST_RECORDED_ACTION_TYPE",
    "OPS_REQUEST_RESOLVED_ACTION_TYPE",
    "PENDING_RULES_COLUMN_ALTERS",
    "PROPOSAL_ID_HEX_BYTES",
    "PROPOSAL_KINDS",
    "PROPOSAL_SCOPES",
    "PROPOSAL_TTL_SECONDS",
    "PendingRuleStore",
    "RESULT_TTL_SECONDS",
    "RULE_DECLINED_ACTION_TYPE",
    "RULE_LAPSED_ACTION_TYPE",
    "RULE_PROPOSED_ACTION_TYPE",
    "RULE_TTL_SECONDS",
    "SPEC_PROPERTIES",
    "STAGING_TTL_SECONDS",
    "STATUS_INSTALLED",
    "SUBMIT_PHASES",
    "TERMINAL_RETENTION_SECONDS",
    "act_readback_text",
    "build_result_row",
    "normalize_lf",
    "normalize_outcome_reason",
    "normalize_rule_text",
    "proposal_state",
    "readback_for",
    "require_address",
    "safe_slug",
    "ttl_for_kind",
]
