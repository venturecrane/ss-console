"""Audit action types, sizes, TTLs, vocabularies and schema DDL for establishment.

Split out of ``establishment.py`` (2026-08-24). Nothing here does I/O or holds
state; it is the vocabulary and tuning surface, plus the `CREATE`/`ALTER`
statements the pending-rules table is built from.

WHY THE ACTION TYPES LIVE HERE. They are the one part of this module whose
location is externally pinned: ``operator/contracts/audit-action-type-producers.json``
names a ``producerFile`` per type, and the gate enforcing it
(``tests/operator-audit-producers.test.ts``) requires the token to appear as a
quoted literal in that file — an import does not satisfy it. Declaring all ten in
one module gives the manifest one honest answer. The manifest's ``reason`` prose
continues to name the broker verb that appends each row, which is the semantics;
``producerFile`` is the locator.
"""

from __future__ import annotations

import re


# Pinned audit action types — exactly one per writing verb (discipline 1).
ESTABLISHMENT_SUBMITTED_ACTION_TYPE = "ESTABLISHMENT_SUBMITTED"
ESTABLISHMENT_RESULT_ACTION_TYPE = "ESTABLISHMENT_RESULT"
RULE_PROPOSED_ACTION_TYPE = "RULE_PROPOSED"
# ss-console#2546. The two ways a proposal ends without being committed, each
# pinned to exactly one writing verb so neither can forge the other:
# establish_decline writes RULE_DECLINED, establish_lapse_notified writes
# RULE_LAPSED.
RULE_DECLINED_ACTION_TYPE = "RULE_DECLINED"
RULE_LAPSED_ACTION_TYPE = "RULE_LAPSED"
# ss-console#2536. The same propose-read-back-confirm channel, carrying a TOOL
# CALL instead of a sentence. One pinned type per verb, exactly as above:
# ``act_propose`` appends only ACT_PROPOSED, ``act_commit`` only ACT_COMMITTED.
ACT_PROPOSED_ACTION_TYPE = "ACT_PROPOSED"
ACT_COMMITTED_ACTION_TYPE = "ACT_COMMITTED"
# ss-console#2546 (the operations half). Three types, one per writing verb, and
# they are DELIBERATELY not the RULE_* ones: a rule is a sentence the firm may
# apply itself, an operations request is a change only SMD makes, and a ledger
# that called them by the same name would make the audit answer to "who decided
# this" unreadable. ops_propose appends only OPS_REQUEST_RECORDED, ops_resolve
# only OPS_REQUEST_RESOLVED, and the lapse report only OPS_REQUEST_LAPSED.
OPS_REQUEST_RECORDED_ACTION_TYPE = "OPS_REQUEST_RECORDED"
OPS_REQUEST_RESOLVED_ACTION_TYPE = "OPS_REQUEST_RESOLVED"
OPS_REQUEST_LAPSED_ACTION_TYPE = "OPS_REQUEST_LAPSED"

# The two spec properties an output class carries (ADR 0083 §2-3). Mirrors
# SPEC_PROPERTIES in corrections.py and src/lib/operator/output-class-specs.ts.
SPEC_PROPERTIES: frozenset[str] = frozenset({"voice", "format"})

# The two submission phases (design §3 steps 4-5): ``analyze`` runs the profile
# and fixed-strings compilers over the corpus; ``install`` carries the drafted
# spec through the write gates.
SUBMIT_PHASES: frozenset[str] = frozenset({"analyze", "install"})

# Output-class slug charset. Mirrors corrections.py and the console writer's
# CLASS_SLUG_PATTERN; refused rather than sanitized (discipline 4).
_CLASS_SLUG_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_-")
_MAX_CLASS_SLUG = 64

# Staging ceilings (design §3 step 3). The per-document text ceiling matches
# the broker's whole-frame ceiling (server.py MAX_REQUEST_BYTES) — a larger
# document could never arrive anyway; stating it here makes the refusal named
# instead of a transport error.
MAX_DOC_TEXT_BYTES = 1_048_576
MAX_DOCS_PER_SET = 64
MAX_SET_BYTES = 16 * 1_048_576
STAGING_TTL_SECONDS = 30 * 60

# Results are one-shot reads; the TTL sweep is the backstop for a result the
# agent never came back for.
RESULT_TTL_SECONDS = 30 * 60

#: The one result status that means the firm's rule is actually in force.
#: Mirrors ``establish_intake.intake.STATUS_INSTALLED``; the two are one
#: string across a root/broker boundary, so it is named on both sides rather
#: than spelled inline.
STATUS_INSTALLED = "installed"

# Spec-body ceiling — applier parity (spec_applier holds a 256 KiB body
# ceiling; a body the applier would refuse must be refused here, not queued).
MAX_SPEC_BODY_BYTES = 262_144

# Assertions ride to the selftest compiler, which owns their schema and refuses
# malformed rules (exit 1). The broker's job is shape and bound, not schema.
_MAX_ASSERTIONS = 100
_MAX_ASSERTIONS_BYTES = 65_536

_MAX_SHORT_TEXT = 200
_MAX_NAME_INPUT = 200
_MAX_NAME_SLUG = 64

# Identifier charset — mirrors the intake's ``_SAFE_SEGMENT`` exactly
# (overlay establish_intake/intake.py): lowercase first char, [a-z0-9_-]
# after, ≤64. The broker mints ids as lowercase hex (token_hex) so they
# always match; a caller-echoed id outside the charset is refused. Excludes
# ``/`` and ``.`` so an identifier can never traverse.
_ID_PATTERN = re.compile(r"\A[a-z0-9][a-z0-9_-]{7,63}\Z")

_NAME_SLUG_KEEP = set("abcdefghijklmnopqrstuvwxyz0123456789._-")

# --- proposed rules (ss-console#2529) --------------------------------------

# The two scopes a spoken rule can carry. ``firm_adjust`` is one sentence about
# how a kind of firm output reads; ``person`` is one about the speaker's own
# work. There is deliberately no third: a rule about somebody ELSE's work is a
# firm rule, and it should be said as one.
PROPOSAL_SCOPES: frozenset[str] = frozenset({"person", "firm_adjust", "act", "ops"})

# ``ops`` is the fourth, and like ``act`` it is not a rule: it is one change to
# how the seat OPERATES (a routine, a schedule, a channel, a memory setting, an
# autonomy level, an on/off), which ADR 0085's 2026-08-22 amendment places with
# SMD rather than with the firm. It shares this table for the reason ``act``
# does — what has to survive from the asking turn to the answering one is a tag,
# a sentence, and a bounded memory — and it shares no path with either rule
# scope: ``establish_submit`` compares against ``person`` / ``firm_adjust`` and
# ``act_commit`` against ``act``, so a submit naming an ops row is refused by
# name in every direction.

# ``act`` is the third, and it is not a rule at all: it is one tool call the
# firm is shown before it happens. It shares this table because the thing that
# has to survive from the proposing turn to the confirming one is identical (a
# tag, a sentence, a 24-hour memory, a consume-once commit), and a second store
# would be a second set of the same bugs. It never shares the SUBMIT path: an
# act row's scope is ``act``, and every establish_submit scope check compares
# against ``person`` / ``firm_adjust``, so a submit naming an act proposal is
# refused by name rather than by luck.

# A proposal id is eight lowercase hex characters: short enough for a person to
# quote back in an email, long enough that a second live proposal is not going
# to collide with it. It becomes the adjustment's id, and the applier pins the
# same shape (overlay spec_applier/applier.py).
PROPOSAL_ID_HEX_BYTES = 4
_PROPOSAL_ID_PATTERN = re.compile(r"\A[0-9a-f]{8}\Z")

# A day. Long enough that a rule stated on Friday afternoon can be confirmed on
# Monday morning; short enough that a stale "yes" on a forgotten thread commits
# nothing. Past it the Operator asks the person to state the rule again, which
# costs one sentence and re-establishes that they still mean it.
PROPOSAL_TTL_SECONDS = 86_400

# A week, for a RULE only (ss-console#2546). The 24 h bound above was written
# for a rule an admin states about their own firm and answers in the same
# conversation. It is the wrong bound for the loop this issue closes: a
# paralegal's rule is emailed to a named administrator, who may be in trial, and
# a request that dies overnight is a request the firm never had. Seven days is
# long enough to cross a week, short enough that a rule nobody answered lapses
# while the person who asked still remembers asking.
#
# ACTS KEEP 24 HOURS. An act is one tool call the Operator is holding, and the
# Captain's authorization for the confirm ceiling was given under that bound;
# widening it here would widen a commitment nobody widened. Which TTL applies is
# read from the row's ``kind``, never from the caller (``ttl_for_kind``).
RULE_TTL_SECONDS = 7 * 86_400

# How long a row is kept after it reaches a TERMINAL state — committed,
# declined, or lapsed. It is kept at all so a late answer gets the true sentence
# ("that rule was already committed", "an administrator declined it") rather
# than "unknown proposal", which reads to the firm like the rule was lost.
# Matched to RULE_TTL_SECONDS so the tombstone outlives the window in which a
# person could still be quoting the tag.
TERMINAL_RETENTION_SECONDS = 7 * 86_400

# ss-console#2546. HOW LONG ONE PROCESS MAY HOLD THE RIGHT TO SEND A ROW'S
# OUTCOME LETTER before another may take it. The claim exists because the seat
# runs the establishment plugin in TWO processes -- `hermes -p operator gateway
# run` (pid 658) and its child `hermes-smd-webhook-gate` (pid 1115), observed on
# pilot-smokeball 2026-08-23 (vfy_01M0QK1927KP54R7J13J2TH3WZ) -- each with its
# own sweeper thread. An in-process claim is therefore two claims, and on
# fc8f88c1 the requester was mailed the same outcome letter twice, 12 s apart.
# The broker is the one process both share, so the claim lives here.
#
# It EXPIRES rather than persisting, because a process that claimed a row and
# then died must not freeze that row's letter forever: unsent is the worse
# failure of the two, and it is the failure this whole issue exists to end. The
# window is wide enough to cover a mail send and narrow enough that a crashed
# sender costs one sweep interval.
NOTIFY_CLAIM_STALE_SECONDS = 120.0

# Ceiling on a spoken rule. It is a sentence, not a document; the applier holds
# the identical bound, so a rule this accepts is one the seat can render.
MAX_RULE_TEXT_BYTES = 2000

# --- proposed ACTS (ss-console#2536) ---------------------------------------

# The row kinds this table holds. ``rule`` is a sentence about how the firm's
# work reads; ``tool_call`` is one act the Operator is asking to perform. The
# default is ``rule`` so a table written before this change reads back as what
# it holds.
PROPOSAL_KINDS: frozenset[str] = frozenset({"rule", "tool_call", "ops_request"})

#: The kind an operations request carries, named once so the three refusals that
#: key on it (submit, decline, consume) cannot drift apart by a typo.
OPS_REQUEST_KIND = "ops_request"

#: The three ways an operations request ends, and there is deliberately no
#: fourth. ``done`` is SMD having made the change; ``declined`` is SMD saying no,
#: with the reason they wrote; ``withdrawn`` is the seat itself giving the row
#: back because it could not get the request out of the building — the one
#: outcome that sends the requester nothing, because nothing was ever asked.
OPS_OUTCOMES: frozenset[str] = frozenset({"done", "declined", "withdrawn"})

#: Ceiling on the quoted reason an outcome carries. It is one line of somebody
#: else's prose riding into an email the Operator sends under its own name, so
#: it is bounded, folded to one line, and stripped of links before it is stored.
MAX_OUTCOME_REASON = 300

# THE CLOSED VOCABULARY OF ACTS. A tool absent from this map cannot be
# proposed, whatever the caller says, and each entry pins the EXACT field set
# its payload carries. There is deliberately no wildcard and no "extra fields
# are fine" branch: the readback the firm reads is rendered from these fields,
# so a field the readback does not render is a field the firm did not agree to.
ACT_TOOLS: dict[str, tuple[str, ...]] = {
    "mcp_smokeball_create_matter": (
        "description",
        "matter_type_id",
        "client_contact_id",
        "number",
    ),
}

# The two display names the read-back shows the administrator, authored beside
# the identifiers in the same block so the sentence a person says yes to and the
# bytes the act carries come from one file. The overlay hook sends the block
# whole (identifiers and names together, hermes-smd-overlay#303/#305); the tool
# itself takes only ACT_TOOLS' fields. Names are accepted in the payload, at the
# request top level (``contact_name`` / ``matter_type_name``), or from the
# authored block, in that order of precedence, and must agree with the block
# when both are present.
ACT_NAME_KEYS: dict[str, tuple[str, ...]] = {
    "mcp_smokeball_create_matter": ("client_contact_name", "matter_type_name"),
}

# Where the authored act payload is read from on a live seat. The broker holds
# its own handle on this file (``SMD_CUSTOMER_YAML``, server.py) and re-reads it
# per proposal rather than caching: the file is root-owned and can be re-applied
# under a running broker, and a cached copy would let a config the firm has
# already changed keep authorizing acts.
ACT_CONFIG_KEYS: tuple[str, ...] = (
    "self_initiation",
    "document_library",
    "operator_matter",
)

# A display name resolved by the seat and rendered into the readback. Bounded,
# and refused rather than sanitized when it carries a bracket or a line break:
# the tag ``[act 1234abcd]`` is what binds a person's "yes" to one row, so a
# name that could contain a second tag could bind it to another row.
_MAX_ACT_DISPLAY_NAME = 120

CREATE_PENDING_RULES_SQL = (
    "CREATE TABLE IF NOT EXISTS pending_rules ("
    "proposal_id TEXT PRIMARY KEY, "
    "scope TEXT NOT NULL, "
    "subject_json TEXT NOT NULL, "
    "text TEXT NOT NULL, "
    "text_sha256 TEXT NOT NULL, "
    "instructed_by TEXT NOT NULL, "
    "for_admin INTEGER NOT NULL DEFAULT 0, "
    "created_at REAL NOT NULL, "
    "expires_at REAL NOT NULL, "
    "consumed_at REAL, "
    "consumed_run_id TEXT, "
    "kind TEXT NOT NULL DEFAULT 'rule', "
    "payload_json TEXT, "
    # ss-console#2546: the two non-committing ends of a proposal, and the mark
    # that the person who asked has been told about one of them.
    "declined_at REAL, "
    "declined_by TEXT, "
    "lapsed_at REAL, "
    "lapse_notified_at REAL, "
    # ss-console#2546 follow-up: the moment a COMMITTED rule was observed
    # installed. Its own column rather than an inference from consumed_at,
    # because committed and installed are hours apart in the failure case and
    # only one of them entitles anybody to say "in effect".
    "installed_at REAL, "
    # ss-console#2546 (the operations half). WHO at SMD answered an operations
    # request, and WHAT THEY WROTE when the answer was no. ``resolved_by`` is
    # separate from ``declined_by`` on purpose: that column means "an
    # administrator of the FIRM refused a rule", this one means "SMD answered a
    # request about the seat", and collapsing them would make the ledger's
    # answer to "who decided this" depend on which verb happened to run.
    "resolved_by TEXT, "
    "outcome_reason TEXT, "
    # The mark that SMD has already been asked, once, to answer in the two words
    # the parser reads. Without it an unparseable reply would be re-asked on
    # every turn that touched the row.
    "ask_sent_at REAL, "
    # ss-console#2546 (the duplicate-letter fix). WHICH observer currently holds
    # the right to send this row's outcome letter, and WHEN it took that right.
    # ``lapse_notified_at`` is the durable mark and is unchanged; this is the
    # short-lived claim that stops two processes both reading the row as
    # unreported, both sending, and only then racing to mark it.
    "notify_claimed_at REAL, "
    "notify_claimed_by TEXT"
    ")"
)
# Additive upgrade for a table created by ss-console#2529, applied at
# ensure_schema and each tolerated when the column already exists (the
# audit_ledger CHAIN_COLUMN_ALTERS shape). A seat that proposed a rule last
# week keeps that row, and reads it back as kind ``rule``.
PENDING_RULES_COLUMN_ALTERS: tuple[str, ...] = (
    "ALTER TABLE pending_rules ADD COLUMN kind TEXT NOT NULL DEFAULT 'rule'",
    "ALTER TABLE pending_rules ADD COLUMN payload_json TEXT",
    # ss-console#2546, same additive idiom: a seat carrying rows proposed under
    # #2529 or #2536 keeps them, and each reads back as open (all four are NULL
    # on an existing row, which is what an unanswered proposal is).
    "ALTER TABLE pending_rules ADD COLUMN declined_at REAL",
    "ALTER TABLE pending_rules ADD COLUMN declined_by TEXT",
    "ALTER TABLE pending_rules ADD COLUMN lapsed_at REAL",
    "ALTER TABLE pending_rules ADD COLUMN lapse_notified_at REAL",
    # ss-console#2546 follow-up. Absent reads as NULL, which is "committed but
    # nobody has observed it install" -- the conservative answer.
    "ALTER TABLE pending_rules ADD COLUMN installed_at REAL",
    # ss-console#2546 (the operations half), same additive idiom. A seat holding
    # rules and acts proposed last week keeps every one of them; all three read
    # back as NULL, which on a rule or an act is exactly right (no SMD answered
    # them, because they were never SMD's to answer).
    "ALTER TABLE pending_rules ADD COLUMN resolved_by TEXT",
    "ALTER TABLE pending_rules ADD COLUMN outcome_reason TEXT",
    "ALTER TABLE pending_rules ADD COLUMN ask_sent_at REAL",
    # ss-console#2546 (the duplicate-letter fix), same additive idiom. Absent
    # reads as NULL, which is "unclaimed" -- exactly right for every row that
    # existed before the claim did.
    "ALTER TABLE pending_rules ADD COLUMN notify_claimed_at REAL",
    "ALTER TABLE pending_rules ADD COLUMN notify_claimed_by TEXT",
)
CREATE_PENDING_RULES_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_pending_rules_open "
    "ON pending_rules(instructed_by, expires_at) WHERE consumed_at IS NULL"
)
