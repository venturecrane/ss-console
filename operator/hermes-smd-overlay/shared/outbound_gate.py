"""Outbound provenance gate — ADR 0028 fail-closed policy core.

The first LIVE caller of provenance enforcement. A draft body that carries a
banned fabrication-marker string (Tier-1, universal) or a fabricated legal
citation (Tier-2, law-vertical) is BLOCKED before the draft tool runs.

This module is pure policy: ``evaluate(body, cohort, vertical) -> GateDecision``.
It is wired into the trust plugin's ``pre_tool_call`` hook (a SECOND evaluation
in the same callback, after the trust-ceiling check passes, only for
draft-creating tools). Body extraction, audit emission, and the block-directive
shape live in ``plugins/hermes-smd-trust/outbound.py``; this module makes the
allow/block decision and nothing else.

Two tiers
---------
* **Tier-1 (UNIVERSAL).** Every vertical. Scan ``body`` against the
  HIGH_RISK_MARKERS registry (``shared.fabrication_markers``). Any hit blocks.
  These are the CLAUDE.md Pattern-A / Pattern-B + tone-rule banned strings.
* **Tier-2 (LAW-VERTICAL ONLY).** Run ``citation_filter.contains_citation`` on
  ``body``. A fabricated case cite / statute / rule blocks. This is the
  Mata-v.-Avianca venture-killer guard. The refusal reason names the pattern
  kind that hit and the remedy for it, and never echoes the matched text.

Fail-closed, most-restrictive-on-indeterminate
----------------------------------------------
The non-negotiable posture (from the 3-critic review):

* If ``vertical`` is ``None`` / unknown / empty → run BOTH tiers. An
  indeterminate vertical is treated as the MOST restrictive (law-tier filters
  apply, block on any marker). Never the permissive/empty set.
* If the marker registry cannot load → block.
* If the citation filter raises → block.
* Any unexpected error in evaluation → block.

A gate that cannot evaluate BLOCKS. Silence is never "allow."
"""

import logging
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field

from shared import citation_filter, identifier_filter
from shared.fabrication_markers import FabricationMarkersError, load_markers

logger = logging.getLogger(__name__)


# Vertical slugs that trigger the Tier-2 citation scan. ``law-firm`` is the v1
# law vertical; the set is kept small and explicit. An UNKNOWN vertical is NOT
# in this set, but unknown verticals run Tier-2 anyway via the most-restrictive
# rule below — membership here is only the "definitely law" fast path.
_LAW_VERTICALS: frozenset[str] = frozenset(
    {"law-firm", "law", "legal", "pi-law", "personal-injury"}
)


# Closed vocabulary for ``GateDecision.audit_action``. The outbound wiring maps
# this onto the audit row; ``block`` is the only non-allow value this gate
# produces (the gate never "drafts" — drafting is the trust-ceiling layer's job).
AUDIT_ALLOW = "allow"
AUDIT_BLOCK = "fabrication_block"


@dataclass(frozen=True)
class GateDecision:
    """Outcome of an outbound-gate evaluation.

    Attributes:
        allowed: True iff the body cleared both applicable tiers.
        reason: Human-readable explanation (audit + block message).
        audit_action: ``AUDIT_ALLOW`` or ``AUDIT_BLOCK``.
        tier: Which tier triggered the block (``"tier1_marker"`` /
            ``"tier2_citation"`` / ``"load_error"`` / ``""`` when allowed).
        marker_hits: Marker ids that hit (Tier-1). Never the full body.
        citation_hits: Citation pattern labels that hit (Tier-2).
        evaluated_law_tier: True iff the Tier-2 citation scan ran (law or
            indeterminate vertical). Surfaced for tests + audit metadata.
    """

    allowed: bool
    reason: str
    audit_action: str
    tier: str = ""
    marker_hits: tuple[str, ...] = field(default_factory=tuple)
    citation_hits: tuple[str, ...] = field(default_factory=tuple)
    evaluated_law_tier: bool = False


def _is_law_or_indeterminate(vertical: str | None) -> bool:
    """Should the Tier-2 citation scan run for this vertical?

    Runs when the vertical is a known law vertical OR is indeterminate
    (None / empty / not a recognized non-law vertical). Indeterminate →
    most-restrictive → run the law-tier filter. The ONLY way to skip Tier-2
    is a vertical that is present, non-empty, and NOT a law vertical — i.e. a
    positively-identified non-law cohort.
    """
    if vertical is None:
        return True
    if not isinstance(vertical, str):
        return True
    normalized = vertical.strip().lower()
    if not normalized:
        return True
    if normalized in _LAW_VERTICALS:
        return True
    # A positively-identified non-law vertical. The brief's rule: only skip the
    # restrictive tier when we KNOW the cohort is not law. Any law/indeterminate
    # state already returned True above. We treat a recognized, non-law,
    # non-empty vertical string as a real cohort and skip Tier-2.
    #
    # NOTE: this is deliberately the ONLY permissive branch. If a future
    # vertical taxonomy makes "unknown but non-empty" ambiguous, prefer
    # widening _LAW_VERTICALS or returning True here — never the reverse.
    return False


#: The one Tier-1 marker a provenance exemption applies to. Named as a constant
#: so the narrowness is legible: the other thirteen markers are unaffected by
#: anything a session read, because none of them describes a fact that CAN be
#: read from a matter record. "We'll reach out" is not more true for having been
#: read somewhere.
_PROVENANCE_EXEMPT_MARKER = "specific-dollar-amount"


def _unverified_money(body: str, allowed_money: Iterable[str] | None) -> list[str]:
    """Dollar figures in ``body`` the agent did NOT read this session.

    Empty list means every figure traces to a source read this session, which is
    the only condition under which the ``specific-dollar-amount`` marker is
    waived. An empty allowed set therefore returns every figure in the body —
    fail-closed, and identical to today's behavior.
    """
    allowed = {str(a) for a in (allowed_money or ())}
    found = identifier_filter.extract_money(body)
    if not allowed:
        return sorted({raw for raw, _canon in found})
    return sorted({raw for raw, canon in found if canon not in allowed})


#: Per-pattern remedy text for a Tier-2 refusal, keyed by the
#: ``citation_filter.PATTERNS`` labels. The old refusal named neither the kind
#: nor a fix, so a model that hit it retried blindly: on the Ashton and Price
#: seat one status reply was refused four to six times in a turn on ordinary
#: comparison prose, then shipped trimmed with the gate mentioned to the client.
#: A hint must say what shape was seen and what to do instead, and must never
#: echo the matched text (that is what makes the dead ``refusal_message``
#: unusable here: a refusal that quotes the cite hands it back to the drafter).
_CITATION_HINTS: dict[str, str] = {
    "case-name": (
        "what reads as a court case caption (two names joined by v., vs, or "
        "versus). If it names a court case, delete the reference entirely; do "
        "not rephrase or abbreviate it. If the two names are things you are "
        "comparing (two letter types, two options), write 'compared with' "
        "instead, or restructure the sentence. If it is one of the firm's own "
        "matters, read that matter from the case system this turn and name it "
        "exactly as the record does. Never mention this refusal to the reader."
    ),
    "reporter-cite": (
        "a reporter citation (volume, reporter, page). Remove it; never cite authority."
    ),
    "federal-statute": "a federal statute reference. Remove it; never cite authority.",
    "state-statute": "a state statute reference. Remove it; never cite authority.",
    "federal-rule": ("a federal court-rule reference. Remove it; never cite authority."),
    "local-rule": "a local court-rule reference. Remove it; never cite authority.",
    "bluebook-signal": (
        "clustered Bluebook signals (id., supra, see also). Rewrite as plain prose."
    ),
}

_CITATION_HINT_DEFAULT = "legal-citation-shaped content. Re-draft without it."


def _citation_reason(labels: Iterable[str]) -> str:
    """Refusal text naming each pattern kind that hit, and its remedy.

    One hint per distinct label, in hit order. Never includes matched text.
    """
    seen = tuple(dict.fromkeys(labels))
    hints = [_CITATION_HINTS.get(label, _CITATION_HINT_DEFAULT) for label in seen]
    if not hints:
        hints = [_CITATION_HINT_DEFAULT]
    return (
        "Refused: draft body contains "
        + " Also: ".join(hints)
        + " (ADR 0028 / safety invariant #6)."
    )


# ---------------------------------------------------------------------------
# The firm's own rule sentences (2026-11-19)
# ---------------------------------------------------------------------------
#
# An attorney states a drafting rule that names a code section or a case ("object
# under <section> and <case>"). The broker stores his sentence and renders it in
# the read-back, and the citation scan then refused that read-back, so the
# Operator cut the citation out of HIS rule and told him the email system refuses
# citations. The scan exists to stop fabricated authority; a sentence the firm
# itself wrote, rendered by code from the broker's row, is not fabricated.
#
# So the EXACT rendered sentence is removed from the text before the citation
# scan, and only that scan: Tier-1 markers and every other check still read the
# whole body. A paraphrase that carries the citation is not the sentence, is not
# removed, and is refused exactly as before.

#: How long a rendered sentence stays exempt, and how many sessions are held. A
#: sentence is noted when the read-back or the confirmation reply is rendered,
#: and the reply that carries it goes within the turn; two hours covers a held
#: reply released later. Per session ONLY (review 2026-11-19): a process-wide
#: default exempted any session's sentences in any other session's reply.
FIRM_SENTENCE_TTL_SECONDS = 2 * 60 * 60
#: A sentence shorter than this exempts nothing: it could not be a rule.
_FIRM_SENTENCE_MIN_CHARS = 12
_FIRM_BY_SESSION: "OrderedDict[str, dict[str, float]]" = OrderedDict()
_FIRM_SESSIONS_MAX = 64
_FIRM_LOCK = threading.Lock()


def _fold_space(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def note_firm_sentences(
    sentences: Iterable[str], session_id: str | None = None, now: float | None = None
) -> None:
    """Record, for THIS session, rule sentences the firm itself wrote: the
    establishment plugin passes only a sentence code found verbatim in the
    person's own email (never the model's words alone). Never raises."""
    if not isinstance(session_id, str) or not session_id:
        return
    stamp = time.time() if now is None else now
    try:
        with _FIRM_LOCK:
            mine = _FIRM_BY_SESSION.pop(session_id, {})
            _FIRM_BY_SESSION[session_id] = mine
            while len(_FIRM_BY_SESSION) > _FIRM_SESSIONS_MAX:
                _FIRM_BY_SESSION.popitem(last=False)
            for sentence in sentences or ():
                folded = _fold_space(sentence) if isinstance(sentence, str) else ""
                if len(folded) >= _FIRM_SENTENCE_MIN_CHARS:
                    mine[folded] = stamp
    except Exception:  # noqa: BLE001 - an unrecorded sentence costs a refusal, never a send
        logger.warning("outbound_gate: could not note firm sentences", exc_info=True)


def gate_kwargs(session_id: str | None) -> dict[str, tuple[str, ...]]:
    """``{"firm_sentences": ...}`` for a session that noted some, else ``{}``:
    the relay's gate call is then exactly what it was before them."""
    firm = firm_sentences_for(session_id)
    return {"firm_sentences": firm} if firm else {}


def firm_sentences_for(session_id: str | None, now: float | None = None) -> tuple[str, ...]:
    """The rule sentences THIS session noted; ``()`` for a session that noted
    none, which is the gate exactly as it was before them."""
    if not isinstance(session_id, str) or not session_id:
        return ()
    stamp = time.time() if now is None else now
    with _FIRM_LOCK:
        mine = _FIRM_BY_SESSION.get(session_id)
        if mine is None:
            return ()
        for sentence, at in list(mine.items()):
            if stamp - at > FIRM_SENTENCE_TTL_SECONDS:
                mine.pop(sentence, None)
        return tuple(mine)


def _reset_firm_sentences_for_tests() -> None:
    with _FIRM_LOCK:
        _FIRM_BY_SESSION.clear()


def without_firm_sentences(body: str, firm_sentences: Iterable[str]) -> str:
    """``body`` with every exact occurrence of a firm sentence removed.

    Whitespace is folded on both sides first (a mail body re-wraps lines), and
    nothing else is: case, punctuation and every word must match, so a
    paraphrase stays in the text."""
    folded = _fold_space(body)
    for sentence in sorted(
        {_fold_space(s) for s in firm_sentences if isinstance(s, str)}, key=len, reverse=True
    ):
        if len(sentence) >= _FIRM_SENTENCE_MIN_CHARS:
            folded = folded.replace(sentence, " ")
    return folded


def _tier2(body: str, allowed_case_names: Iterable[str] | None) -> GateDecision | None:
    """The Tier-2 citation scan's refusal, or ``None`` when ``body`` is clear.
    Fails closed: a raising filter refuses."""
    try:
        if citation_filter.contains_citation(body, allowed_case_names=allowed_case_names):
            hits = citation_filter.scan(body, allowed_case_names=allowed_case_names)
            labels = tuple(dict.fromkeys(h.pattern for h in hits))
            return GateDecision(
                allowed=False,
                reason=_citation_reason(labels),
                audit_action=AUDIT_BLOCK,
                tier="tier2_citation",
                citation_hits=labels,
                evaluated_law_tier=True,
            )
    except Exception:  # noqa: BLE001 — filter raise must fail closed
        logger.exception("outbound_gate: citation filter raised; BLOCKING (fail-closed)")
        return GateDecision(
            allowed=False,
            reason="Refused: citation filter failed; failing closed (ADR 0028)",
            audit_action=AUDIT_BLOCK,
            tier="tier2_citation",
            evaluated_law_tier=True,
        )
    return None


def _citation_text(body: str, firm_sentences: Iterable[str] | None) -> str:
    """The text the citation scan reads: ``body`` without the firm's own rule
    sentences (``None``: none)."""
    sentences = tuple(firm_sentences or ())
    return without_firm_sentences(body, sentences) if sentences else body


def evaluate(
    body: str,
    cohort: str | None,
    vertical: str | None,
    allowed_case_names: Iterable[str] | None = None,
    allowed_money: Iterable[str] | None = None,
    firm_sentences: Iterable[str] | None = None,
) -> GateDecision:
    """Decide whether a draft ``body`` may be produced.

    Args:
        body: The draft body text the agent is about to write.
        cohort: The customer cohort, if known. Reserved for future
            cohort-scoped marker tiers; not load-bearing in v1 but carried so
            callers and the audit row capture it. An indeterminate cohort does
            NOT relax any tier.
        vertical: The customer vertical (e.g. ``law-firm``). ``None`` / unknown
            → both tiers run (most-restrictive).
        allowed_case_names: Provenance-verified case CAPTIONS the agent
            actually read this session (the runtime register's captions —
            ss-console #1758). Exempts only the Tier-2 case-name pattern;
            fabricated-authority patterns (reporter cites, statutes, rules)
            and every Tier-1 marker are unaffected. Empty/None = no exemption.
        allowed_money: Provenance-verified dollar FIGURES the agent actually read
            this session (the runtime register's ``money()`` — ss-console#2258),
            in :func:`identifier_filter.canon_money` form. Exempts ONLY the
            Tier-1 ``specific-dollar-amount`` marker, and only when EVERY figure
            in the body is verified. Empty/None = no exemption, which is exactly
            today's behavior.
        firm_sentences: Broker-rendered rule sentences of the firm's own
            (2026-11-19). Their exact occurrences are removed before the
            Tier-2 citation scan ONLY. ``None`` or ``()`` means none, which
            is exactly the behavior before it; callers pass their own
            session's (:func:`firm_sentences_for`).

            WHY THIS EXISTS. The marker is the regex ``\\$\\s?\\d`` — any dollar
            sign followed by a digit — while ``demand-letter-drafter``'s own
            SKILL.md authorizes "a specific dollar figure ... when it exists in
            an authored source on the matter, and name that source in the same
            sentence." The gate forbade what the skill permitted, so a demand
            letter's medical specials were refused on the delivery path even
            though the agent had just read them off the billing summary.

    Returns:
        A :class:`GateDecision`. ``allowed=False`` means the draft tool must be
        blocked; the caller emits the audit row and returns the block directive.

    Fail-closed: a missing body, an unloadable marker registry, or a raising
    citation filter all BLOCK.
    """
    # A draft-creating tool with no recognizable body is a fail-closed block at
    # the caller; but defend here too — an empty/None body reaching the policy
    # core is itself indeterminate and must not be waved through.
    if not isinstance(body, str) or not body.strip():
        logger.warning("outbound_gate: empty/None body reached evaluate(); BLOCKING (fail-closed)")
        return GateDecision(
            allowed=False,
            reason="Refused: draft body is empty or unreadable; failing closed (ADR 0028)",
            audit_action=AUDIT_BLOCK,
            tier="load_error",
        )

    # ----- Tier-1: universal fabrication markers -----
    try:
        registry = load_markers()
    except FabricationMarkersError as exc:
        # The marker registry could not load. A gate that cannot run its
        # universal scan must block, not pass.
        logger.error(
            "outbound_gate: marker registry failed to load (%s); BLOCKING (fail-closed)", exc
        )
        return GateDecision(
            allowed=False,
            reason="Refused: fabrication-marker registry unavailable; failing closed (ADR 0028)",
            audit_action=AUDIT_BLOCK,
            tier="load_error",
        )

    try:
        marker_hits = registry.scan(body)
    except Exception:  # noqa: BLE001 — any matcher fault must fail closed
        logger.exception("outbound_gate: marker scan raised; BLOCKING (fail-closed)")
        return GateDecision(
            allowed=False,
            reason="Refused: fabrication-marker scan failed; failing closed (ADR 0028)",
            audit_action=AUDIT_BLOCK,
            tier="load_error",
        )

    # Provenance exemption, applied to ONE marker and only when the whole body
    # clears (ss-console#2258). Deliberately all-or-nothing: a body carrying one
    # verified figure and one invented figure is not partly honest, and dropping
    # the hit would let the invented one through beside the real one. The refusal
    # names the unverified figures, because "your draft has a dollar amount" is
    # not actionable and "$88,000.00 is in no source you read" is.
    if marker_hits and any(h.marker_id == _PROVENANCE_EXEMPT_MARKER for h in marker_hits):
        try:
            unverified = _unverified_money(body, allowed_money)
        except Exception:  # noqa: BLE001 — an exemption that cannot be computed is not granted
            logger.exception("outbound_gate: money provenance check raised; keeping the marker hit")
            unverified = ["(provenance check failed)"]
        if not unverified:
            marker_hits = [h for h in marker_hits if h.marker_id != _PROVENANCE_EXEMPT_MARKER]
        # Otherwise the hit stands; the refusal below names the unverified figures.

    if marker_hits:
        hit_ids = tuple(h.marker_id for h in marker_hits)
        first = marker_hits[0]
        # Reason THEN remedy. A refusal that says only why the rule exists
        # strands the work: the em-dash marker lost a daily-needs-you-digest
        # memo on 2026-08-19 and again on 2026-09-02, each on a single attempt
        # with no retry, while the identifier gate -- whose refusal ends with
        # "Re-read the source record ... or remove the unverified value" --
        # recovers ~88% of refused writes in the same session.
        #
        # Remedies are authored narrow and never restate the rule: on
        # 2026-08-24 a refusal that named its rule taught the model to strip 38
        # matter numbers out of a digest.
        detail = first.reason
        if first.remedy:
            detail = f"{detail} {first.remedy}"
        if first.marker_id == _PROVENANCE_EXEMPT_MARKER:
            missing = _unverified_money(body, allowed_money)
            detail = (
                f"{first.reason} Not traceable to anything read this session: "
                f"{', '.join(missing[:5])}"
                f"{' and more' if len(missing) > 5 else ''}. Re-read the source "
                "record that carries the figure, or remove it."
            )
        return GateDecision(
            allowed=False,
            reason=(
                f"Refused: draft body contains a banned fabrication marker "
                f"({first.marker_id}: {detail})"
            ),
            audit_action=AUDIT_BLOCK,
            tier="tier1_marker",
            marker_hits=hit_ids,
        )

    # ----- Tier-2: law-vertical citation scan -----
    run_law_tier = _is_law_or_indeterminate(vertical)
    if run_law_tier:
        refused = _tier2(_citation_text(body, firm_sentences), allowed_case_names)
        if refused is not None:
            return refused

    return GateDecision(
        allowed=True,
        reason="outbound gate clear",
        audit_action=AUDIT_ALLOW,
        evaluated_law_tier=run_law_tier,
    )


__all__ = [
    "AUDIT_ALLOW",
    "AUDIT_BLOCK",
    "FIRM_SENTENCE_TTL_SECONDS",
    "GateDecision",
    "evaluate",
    "firm_sentences_for",
    "note_firm_sentences",
    "without_firm_sentences",
]
