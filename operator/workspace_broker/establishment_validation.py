"""Input validation, normalization and read-back rendering for establishment.

Split out of ``establishment.py`` (2026-08-24). Every function here is pure: it
takes values and returns values or raises. There is no database handle, no
filesystem access and no clock — the single ``sqlite3`` reference in the module
is a type hint on ``_column``.

That purity is why this block moved whole. It is the part of the establishment
path that can be reasoned about, and tested, without a spool.
"""

from __future__ import annotations

import json
import re
import sqlite3
from hashlib import sha256
from typing import Any

from .establishment_constants import *  # vocabulary and tuning surface
from .establishment_constants import (  # noqa: F401 — `import *` skips _names
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

def ttl_for_kind(kind: str) -> int:
    """How long a row of this kind stays answerable.

    Read from the STORED kind, never from the caller, so no request can widen
    the window its own proposal lives in. A rule gets a week (a named
    administrator may be in trial); an act keeps the day it has always had.

    An OPERATIONS REQUEST gets the rule's week, and for the same reason rather
    than by analogy: it is emailed to a person at SMD who may be with a client
    all day, and a request that dies overnight is a request the firm never had.
    """
    return (
        RULE_TTL_SECONDS
        if kind in ("rule", OPS_REQUEST_KIND)
        else PROPOSAL_TTL_SECONDS
    )


class EstablishmentValidationError(ValueError):
    """An establishment request was malformed. Raised before anything is written."""


def _require_text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise EstablishmentValidationError(f"{field} must be a string")
    text = value.strip()
    if not text:
        raise EstablishmentValidationError(f"{field} must not be empty")
    if len(text) > limit:
        raise EstablishmentValidationError(
            f"{field} is {len(text)} characters; the ceiling is {limit}"
        )
    return text


def _optional_text(value: Any, field: str, limit: int) -> str | None:
    if value is None:
        return None
    return _require_text(value, field, limit)


def _require_class_slug(value: Any) -> str:
    slug = _require_text(value, "output_class", _MAX_CLASS_SLUG)
    if not set(slug) <= _CLASS_SLUG_CHARS:
        raise EstablishmentValidationError(
            "output_class must match [a-z0-9_-]; refusing to rewrite it"
        )
    return slug


def _require_property(value: Any) -> str:
    prop = _require_text(value, "property", _MAX_SHORT_TEXT)
    if prop not in SPEC_PROPERTIES:
        raise EstablishmentValidationError(
            f"property must be one of {sorted(SPEC_PROPERTIES)}; got {prop!r}"
        )
    return prop


def _require_id(value: Any, field: str) -> str:
    ident = _require_text(value, field, 64)
    if not _ID_PATTERN.match(ident):
        raise EstablishmentValidationError(
            f"{field} must match [a-z0-9][a-z0-9_-]{{7,63}}; refusing to rewrite it"
        )
    return ident


def _require_proposal_id(value: Any, field: str = "proposal_id") -> str:
    ident = _require_text(value, field, 64)
    if not _PROPOSAL_ID_PATTERN.match(ident):
        raise EstablishmentValidationError(
            f"{field} must be eight lowercase hex characters; refusing to rewrite it"
        )
    return ident


def require_address(value: Any, field: str) -> str:
    """One person's email address, lowercased. Refused, never repaired.

    Lifted verbatim out of ``_submit_person`` so the propose path, the pending
    lookup, and the submit path all decide "is this the same person" the same
    way. Two different normalizations here would mean a rule a person can
    propose and then cannot confirm.
    """
    raw = _require_text(value, field, _MAX_SHORT_TEXT)
    address = raw.strip().lower()
    local, sep, domain = address.partition("@")
    if not local or sep != "@" or "@" in domain or "." not in domain:
        raise EstablishmentValidationError(
            f"{field} must be a single person email address (local@domain)"
        )
    return address


def normalize_rule_text(value: Any) -> str:
    """The spoken rule, reduced to the one line a person can be shown.

    CRLF and lone CR fold to LF (the portal writer's precedent), and every
    remaining line break folds to a single space. The second step is not
    cosmetic: the readback is one quoted line, the rendered adjustment is one
    bullet, and a rule that renders differently from the sentence the person
    confirmed defeats the entire point of asking them. Normalizing here — before
    the hash, before the readback, before anything is stored — is what makes
    "what you confirmed is what was committed" true byte for byte.
    """
    if not isinstance(value, str):
        raise EstablishmentValidationError("text must be a string")
    text = re.sub(r"\s*\n\s*", " ", normalize_lf(value)).strip()
    if not text:
        raise EstablishmentValidationError("text must not be empty")
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_RULE_TEXT_BYTES:
        raise EstablishmentValidationError(
            f"text is {len(encoded)} bytes; the ceiling is {MAX_RULE_TEXT_BYTES}. "
            "A standing rule is a sentence; establish a longer one from documents"
        )
    return text


#: Anything that looks like a link in a quoted reason. Deliberately broad: this
#: is not a URL parser, it is a refusal to relay a clickable target.
_URL_PATTERN = re.compile(r"(?i)\b(?:https?://|www\.)\S+")


def normalize_outcome_reason(value: Any) -> str | None:
    """The reason SMD wrote for declining, reduced to one quotable line.

    Three transformations, and each is a rewrite rather than a refusal — which
    is the one place in this module that is the right call, for the same reason
    ``_bounded_str`` truncates a root-authored result instead of refusing it.
    This text is not an identifier and nothing binds to it: it is prose a person
    typed, which the seat quotes back to the person who asked. Refusing a
    300-character reason would leave the request unanswered and the requester in
    the silence this whole issue exists to end, which is strictly worse than
    quoting the first 300 characters of it.

    1. Every line break folds to a space (``normalize_rule_text``'s rule), so a
       reason renders as one quoted line rather than as somebody else's layout.
    2. Anything link-shaped is replaced with ``[link removed]``. The reason
       rides an email the OPERATOR sends under its own name to a person at the
       firm, and the answering address is trusted only by the ``[ops XXXX]``
       tag it quoted; a live link would make a spoofed answer into a phish
       carried by the firm's own assistant. The marker is left visible on
       purpose — a silently deleted link changes what the sentence says.
    3. Truncated to :data:`MAX_OUTCOME_REASON`.

    ``None`` in, ``None`` out: no reason is a normal answer to "done".
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise EstablishmentValidationError("reason must be a string when present")
    folded = re.sub(r"\s+", " ", normalize_lf(value)).strip()
    folded = _URL_PATTERN.sub("[link removed]", folded)
    folded = re.sub(r"\s+", " ", folded).strip()
    if not folded:
        return None
    return folded[:MAX_OUTCOME_REASON]


def readback_for(proposal_id: str, text: str, kind: str = "rule") -> str:
    """The canonical block the seat must send verbatim, rendered broker-side.

    Rendered HERE rather than composed by the model, and returned from
    ``establish_propose``, so the sentence the person is shown is the sentence
    in the row. The seat's containment gate refuses any send-shaped tool on a
    proposing turn unless this appears in the outgoing body (overlay PR 2), and
    that is what makes "you confirmed exactly this" checkable rather than
    asserted.

    Three tags, one shape. ``[rule XXXX]`` marks a sentence about how the firm's
    work reads; ``[act XXXX]`` marks one act the Operator is asking to perform;
    ``[ops XXXX]`` marks a change to how the seat runs, which only SMD makes.
    They are distinct words because the person answering them is agreeing to
    three different things, and the confirming matcher binds the tag to the row
    it came from. The ops tag is also the CAPABILITY on that row: quoting it is
    how an answer from SMD is bound to the request it answers, so a name or a
    reason that could contain a second tag is refused everywhere it is read.
    """
    if kind == "tool_call":
        tag = "act"
    elif kind == OPS_REQUEST_KIND:
        tag = "ops"
    else:
        tag = "rule"
    return f"[{tag} {proposal_id}] {text}"


def _require_display_name(value: Any, field: str) -> str:
    """One resolved NAME the readback renders, refused rather than repaired.

    The seat resolves the client contact and the matter type to names before
    proposing, because the admin reading the sentence cannot judge a UUID and
    an agreement to a UUID is not an agreement. The identity in the row is
    still the authored id; this is the human-legible half of the same fact, so
    it is bounded, single-line, and bracket-free (a name carrying ``[`` could
    render a second tag into the readback and bind a "yes" to the wrong row).
    """
    name = _require_text(value, field, _MAX_ACT_DISPLAY_NAME)
    if "\n" in name or "\r" in name:
        raise EstablishmentValidationError(f"{field} must be a single line")
    if "[" in name or "]" in name:
        raise EstablishmentValidationError(
            f"{field} must not contain a square bracket; the readback tag is what "
            "binds a confirmation to one proposal"
        )
    return name


def act_readback_text(
    tool: str, payload: dict[str, Any], *, contact_name: str, matter_type_name: str
) -> str:
    """The act, as one sentence a person can answer, rendered broker-side.

    Rendered from the STORED payload and the resolved names, never from caller
    prose: what the firm reads is what the row holds. Every value the act will
    carry appears in it, which is the whole test of a readback worth asking
    somebody to say yes to.
    """
    if tool != "mcp_smokeball_create_matter":
        raise EstablishmentValidationError(f"no readback is defined for {tool!r}")
    return (
        f'Create Smokeball matter "{payload["description"]}" '
        f'(number {payload["number"]}; client: {contact_name}; type: {matter_type_name}). '
        'Reply "yes, create it" to proceed.'
    )


def safe_slug(name: Any) -> str:
    """Derive the stored document name from the caller's raw name.

    A broker-side derivation (discipline 3), like the sha256: the raw name is
    validated for type and bound, the slug is computed here, and the raw bytes
    are never stored — so a hostile filename from a client system cannot ride
    into the spool, the audit ledger, or a later reply. A name that derives to
    nothing is refused (discipline 4), never invented.
    """
    raw = _require_text(name, "name", _MAX_NAME_INPUT)
    out: list[str] = []
    for ch in raw.lower():
        if ch in _NAME_SLUG_KEEP and ch != "-":
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    slug = "".join(out).strip("-._")[:_MAX_NAME_SLUG]
    if not slug:
        raise EstablishmentValidationError(
            "name derives to an empty slug; provide a name with [a-z0-9._-] content"
        )
    return slug


def normalize_lf(text: str) -> str:
    """Collapse CRLF and lone CR to LF.

    The portal writer's precedent (src/lib/operator/output-class-specs.ts):
    the stored bytes are LF-only, so the byte ceiling, the hash, and the
    installed file agree — and agree on LF.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _column(row: sqlite3.Row, name: str) -> Any:
    """One column, or None when the table predates it (ss-console#2546).

    The additive-ALTER idiom leaves a window in which a table created by an
    older build is read by a newer one, and a KeyError there would take out the
    whole establishment path rather than one field.
    """
    return row[name] if name in row.keys() else None


def proposal_state(row: dict[str, Any]) -> str:
    """One word for where a proposal stands, for the seat to branch on.

    Ordered by precedence rather than by recency, because the states are
    mutually exclusive by construction (every writer's WHERE clause requires the
    other two to be NULL) and an order makes a corrupted row read as the most
    conservative answer instead of as open.
    """
    if row.get("consumed_at") is not None:
        # An OPERATIONS row reaches this arm through ``ops_resolve`` with
        # outcome ``done``: nothing was committed by the firm, SMD made the
        # change. The word is shared because the seat branches on kind before it
        # branches on state, and inventing a fourth state here would be a fourth
        # thing every reader of this view has to know.
        return "committed"
    if row.get("declined_at") is not None:
        return "declined"
    if row.get("lapsed_at") is not None:
        return "lapsed"
    return "open"


def _hash_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _bounded_str(value: Any, limit: int = _MAX_SHORT_TEXT) -> str | None:
    """Bounded coercion for fields read off a ROOT-authored result file.

    Truncation (not refusal) is correct here and only here: the writer is the
    root intake, not the agent, and the bound is belt-and-braces against an
    intake bug — a refusal would strand a result the admin is owed.
    """
    if not isinstance(value, str) or not value:
        return None
    return value[:limit]


def build_result_row(run_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """Build the ESTABLISHMENT_RESULT audit row from a bounded field set.

    The retained record carries the verdict, the demoted rules with the
    documents that violated them (names, never text), and the recovery key.
    The corpus and any leak excerpts stay in the one-shot result payload,
    which is deleted after this row is appended. Demotion entries arrive as
    ``{rule_id, documents, detail}`` (the intake's selftest gate); ``detail``
    is deliberately NOT retained — it is compiler prose that may quote, and
    retained records carry names, ids, and counts only.
    """
    demotions: list[dict[str, Any]] = []
    raw_demotions = result.get("demotions")
    if isinstance(raw_demotions, list):
        for entry in raw_demotions[:50]:
            if not isinstance(entry, dict):
                continue
            rule_id = _bounded_str(entry.get("rule_id"))
            raw_docs = entry.get("documents")
            documents = []
            if isinstance(raw_docs, list):
                documents = [
                    d[:_MAX_SHORT_TEXT] for d in raw_docs[:MAX_DOCS_PER_SET] if isinstance(d, str)
                ]
            demotions.append({"rule_id": rule_id, "documents": documents})

    metadata = {
        "run_id": run_id,
        "verdict": _bounded_str(result.get("status")),
        "phase": _bounded_str(result.get("phase")),
        "scope": _bounded_str(result.get("scope")),
        "person": _bounded_str(result.get("person")),
        "output_class": _bounded_str(result.get("output_class")),
        "property": _bounded_str(result.get("property")),
        "demotions": demotions,
        "previous_key": _bounded_str(result.get("previous_key")),
    }
    return {
        "action_type": ESTABLISHMENT_RESULT_ACTION_TYPE,
        "actor": "operator",
        "actor_role": "agent",
        "metadata": json.dumps(metadata, sort_keys=True, separators=(",", ":")),
    }

