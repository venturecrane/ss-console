"""Body-hash verification + cross-run invariants for the send reconcile.

The fourth phase of ``operator/bin/reconcile-sends.py`` (the 2026-08-24..31
outbound-quality review: format drift, unstable ACK codes, recipient flapping).
The first three passes ask "is every send ACCOUNTED for?"; this one asks, of the
accounted sends, "is each one the body the routine AUTHORED?".

TWO CHECKS, DIFFERENT STRENGTHS (cross-workstream contract, item 1):

* PRIMARY -- the deterministic hash join. The skill's pre_run stamps
  ``canonical_body_sha256`` of every rendered body onto its EMITTED_WAKE row
  (``body_sha256: [{body_sha256_full, body_sha256_skeleton}]``); the overlay
  stamps ``rendered_body_sha256`` + ``body_variant`` onto
  CONFIRM_SEND_DISPATCHED at transmit. Both stamps are written seat-side, so
  the comparison needs no body fetch and no normalization judgment. A dispatch
  hash matching neither wake hash is ``BODY_DIVERGED`` -- a finding. A skeleton
  match grades ``degraded``, never a finding: the fallback ladder fired as
  designed.

* SECONDARY -- the transient channel fetch. The message body is pulled from the
  mailbox (AgentMail ``text`` / msgraph ``body``), canon-hashed, and compared
  FIRST to the wake stamp, then to the dispatch row's ``plain_body_sha256``.
  The second comparison is the calibration this check was waiting on. The wake
  stamps hash the RAW markdown a renderer authored; AgentMail stores the
  POST-``render_plain`` text, so every templated send mismatched the wake stamp
  and HELD -- the designed pre-calibration state, and loud on every run (live
  evidence: workflow run 33524166727, vfy_01M1ERB6DD7G8Q2XSQ2MAVQ68Z and
  vfy_01M1ERF2TSW86GSAZT2YY16X7S -- the sent text/plain equals the overlay's
  ``render_plain(markdown)`` byte-for-byte after canonicalization). The overlay
  now stamps that exact attached text as ``plain_body_sha256`` on
  CONFIRM_SEND_DISPATCHED, so the channel body has a same-representation
  counterpart to be graded against, and a real divergence is a FINDING.

  ABSENCE IS A FACT, NOT A GAP. The overlay OMITS ``plain_body_sha256`` rather
  than duplicating the raw hash under a second name whenever no down-render
  ran -- a prose reply, a composer-supplied html body, a non-send tool
  (hermes-smd-overlay#338 stamps it iff ``_attach_html_body`` actually attached
  the pair). So on a seat that stamps, absence MEANS "the channel text is still
  the bytes the gate allowed", and the right counterpart is
  ``rendered_body_sha256``. Grading it any other way would leave every
  prose-reply send permanently held.

  MERGE-ORDER SAFETY, AND THE ONE THING THE TWO CASES SHARE. Absence carries
  that meaning only where the stamp COULD have appeared. On a seat whose pinned
  ``OVERLAY_REF`` predates #338 the key never appears at all, a templated send
  is still down-rendered with nothing recording it, and grading its channel body
  against the raw markdown would file a false ``BODY_DIVERGED`` on every run.
  The discriminator is therefore the DEPLOY EDGE: the earliest dispatch row on
  the inbox that carries a plain stamp (``send_attribution.plain_stamp_edge``).
  A row before the edge, or any row on an inbox with no edge, is pre-deploy =>
  keep ``channel_mismatch_hold``; a row at or after it was written by an overlay
  that stamps => absence is deliberate => grade against
  ``rendered_body_sha256``. Per ROW, not per inbox: the overlay version is a
  property of the seat, but the seat's version CHANGES, and a window that spans
  the reprovision holds rows from both sides of it. The first version of this
  discriminator was a per-inbox "any row carries the stamp" probe, and on
  2026-09-04 (pilot-smokeball, ``--days 7``) it read the 09-01 escalator send --
  pre-reprovision, unstamped, conformant -- as a deliberate omission and filed
  BODY_DIVERGED against it. The invariant both halves serve: no false findings
  before the pin lands, no permanent holds after it. ``body_unavailable`` stays
  a HOLD throughout -- a body we could not fetch is a transport fact, not a
  divergence.

  NOT EVERY SEND IS STAMPED, BY DESIGN. The overlay stamps at exactly one site
  (``_dispatch_internal_message``); ``_dispatch_approved_send`` -- the
  model-composed approved sends -- stamps neither hash, because those bodies are
  covered by the cross-run invariants below rather than by body-hash
  verification. This phase must not expect stamps on that path.

ATTRIBUTION -- WHICH ROUTINE A SEND BELONGS TO -- lives in
``lib/send_attribution.py`` (claims review 2026-09-04, B3 + B7). The primary
check pairs a dispatch with its wake SKILL-FIRST (the broker now writes the
``skill_name`` column off the overlay's cron-resolved routine) and HASH-SECOND
(an unlabelled dispatch whose rendered hash a hash-verified wake stamped is
that wake's send), and every graded pair records which (``attribution``),
counted per inbox as ``attributed_by_skill`` / ``attributed_by_hash``. The
secondary check attributes each mailbox message by IDENTITY (its id against the
dispatch rows' join keys) before it claims by window, and a divergence on a
message no row identifies is a HOLD, never a finding.

LEAK SAFETY IS STRUCTURAL, NOT DISCIPLINARY. Both repos are PUBLIC. A client
email body must never appear in CI logs, artifacts, committed files, or issue
bodies. So :class:`BodyVerdict` and :class:`InvariantFinding` carry hashes,
timestamps, and rule names ONLY -- no field exists that could hold body text,
and ``operator/bin/tests/test_send_verify.py`` pins that shape (a regression
that adds a ``body``/``text``/``content``/``html`` field fails there before it
ships). The fetched body lives in one local variable that flows into
``canonical_body_sha256`` and nowhere else.

HOLDS ARE THE DESIGNED INTERIM STATE. Until the render cluster (WS-RENDER)
deploys, no wake row carries a hash and no dispatch row carries
``rendered_body_sha256``, so this phase evaluates nothing and stays quiet. As
seats pick the stamps up, a templated send missing its counterpart stamp is a
HOLD line ("templated send with no wake hash") -- red, filed nowhere -- because
the stamp's absence is a deployment-skew fact, not an accusation.

A HOLD IS INTERIM OR IT IS NOT A HOLD. The word means "unevaluated today,
evaluable once someone acts" -- deploy the pin, fix the transport. A row
dispatched BEFORE the inbox's plain-stamp edge on a seat that has since crossed
it is different in kind: no action by anyone will ever produce its counterpart
stamp, and the scheduled run scans the whole mailbox with no window, so a hold
on it is a red that cannot go green. That is a check that cannot fail, mirrored
(Law 12), and it mutes the control (2026-09-01..09-09: eleven consecutive red
scheduled runs on the single 09-01 escalator send, seventeen unresolved
critical notifications, nobody acting). Such a row grades ``pre_stamp_edge``:
neither hold nor finding, counted and printed indented under the inbox so the
number stays visible, never a ``HOLD`` line. The no-edge case keeps its hold --
the stamp may still arrive.

CROSS-RUN INVARIANTS cover the ``compositional`` skills the hash join cannot:
the same routine must not flap recipients across runs, and the same item_key
must keep its ACK code. The two-tier grading (first-seen values PROPOSE rows
for a reviewed send-invariants.json PR; only a conflict with a COMMITTED value
is a finding) lives in ``lib/send_invariants.py``, re-exported here.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

# One physical line on purpose: this module sits at the size ratchet's ceiling
# (tests/operator-module-size.test.ts counts physical non-comment lines).
from send_attribution import (
    ATTRIBUTED_BY_HASH,
    _usable_ids,
    attribution_counts,
    claim_dispatch_stamp,
    claim_wake,
    message_attributor,
    plain_stamp_edge,
    stamps_plain_at,
)  # noqa: F401 -- attribution_counts re-exported
from send_invariants import (  # noqa: F401 -- re-exports; callers and tests read unchanged
    DEFAULT_INVARIANTS_PATH,
    InvariantFinding,
    InvariantProposal,
    _item_hash,
    _recipient_hash,
    ack_invariant,
    load_invariants,
    recipient_invariant,
)

_OPERATOR_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
SEND_RENDER_PATH = os.path.join(_OPERATOR_DIR, "contracts", "send-render.yaml")
CANON_VECTORS_PATH = os.path.join(
    _OPERATOR_DIR, "contracts", "fixtures", "body-canon-vectors.json"
)

#: How long after a wake a dispatch may land and still be that wake's send.
#: The terminal-state contract's ``scheduled_outbound`` window
#: (operator/contracts/terminal-states.yaml routine_classes), consumed
#: one-to-one like the reconciler's ``_claim``.
VERIFY_WINDOW_S = 3600

#: Render modes whose bodies are hash-verifiable. ``compositional`` is covered
#: by the invariants instead.
_HASH_VERIFIED_MODES = ("templated", "slot-templated")

VERDICT_MATCH = "match"
VERDICT_DEGRADED = "degraded"  # skeleton fallback delivered; designed behavior
VERDICT_DIVERGED = "BODY_DIVERGED"  # finding
VERDICT_NO_WAKE_HASH = "no_wake_hash"  # hold
VERDICT_NO_DISPATCH_STAMP = "no_dispatch_stamp"  # hold
VERDICT_BODY_UNAVAILABLE = "body_unavailable"  # hold
VERDICT_CHANNEL_MISMATCH = "channel_mismatch_hold"  # hold until rehearsal calibrates
VERDICT_PRE_EDGE = (
    "pre_stamp_edge"  # neither hold nor finding: unverifiable by construction
)

_HOLD_VERDICTS = (
    VERDICT_NO_WAKE_HASH,
    VERDICT_NO_DISPATCH_STAMP,
    VERDICT_BODY_UNAVAILABLE,
    VERDICT_CHANNEL_MISMATCH,
)

#: The MATCH detail names the attribution only when it was the fallback: a
#: hash-attributed pair is a pair the column SHOULD have made (the seat's
#: overlay predates it), and the line says so. Counted too (attribution_counts).
_ATTRIBUTION_DETAIL = {ATTRIBUTED_BY_HASH: "attributed by hash"}


class SendRenderError(RuntimeError):
    """A malformed send-render contract. Refuse to evaluate; never guess."""


def canonical_body_sha256(text: str) -> str:
    """THE hash function -- identical at all three stamp sites.

    sha256 over utf-8 of (CRLF -> LF, per-line trailing SPACE/TAB stripped,
    trailing newlines stripped). Space and tab ONLY -- ``rstrip(" \\t")``,
    never a bare ``rstrip()``: bare rstrip eats every Unicode whitespace
    (nbsp, form feed, vertical tab), and a mail client converting a body's
    trailing nbsp would then hash differently here than at the render-side
    stamp sites, false-HOLDing the channel check (render-pair review of
    ss#2664; the ``nbsp_tail_survives`` vector pins it). The arbiter is the
    shared vector fixture
    (``operator/contracts/fixtures/body-canon-vectors.json``), loaded verbatim
    by this repo's tests and the overlay's; an implementation change that
    drifts from the vectors fails both suites, which is the whole point of one
    fixture instead of two prose descriptions.
    """
    normalized = text.replace("\r\n", "\n")
    lines = [line.rstrip(" \t") for line in normalized.split("\n")]
    return hashlib.sha256("\n".join(lines).rstrip("\n").encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RenderDecl:
    skill: str
    render: str  # templated | slot-templated | compositional
    template: Optional[str] = None

    @property
    def hash_verified(self) -> bool:
        return self.render in _HASH_VERIFIED_MODES


def load_send_render(path: str = SEND_RENDER_PATH) -> dict[str, RenderDecl]:
    """Parse the contract. Parsed, never grepped; malformed RAISES.

    A missing contract file also raises: unlike the invariants file (where the
    safe failure is over-reporting), a verifier that silently ran with an empty
    declaration set would grade every templated send as unverifiable and call
    that clean.
    """
    import yaml  # deferred; reconcile-sends only needs it on this path

    with open(path, encoding="utf-8") as handle:
        parsed = yaml.safe_load(handle)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("skills"), dict):
        raise SendRenderError(f"{path}: expected a mapping with a `skills` mapping")
    out: dict[str, RenderDecl] = {}
    for skill, entry in parsed["skills"].items():
        if not isinstance(entry, dict):
            raise SendRenderError(f"{path}: skills.{skill} must be a mapping")
        render = entry.get("render")
        if render not in ("templated", "slot-templated", "compositional"):
            raise SendRenderError(
                f"{path}: skills.{skill}.render must be templated | slot-templated | "
                f"compositional (got {render!r})"
            )
        template = entry.get("template")
        if render in _HASH_VERIFIED_MODES and not isinstance(template, str):
            raise SendRenderError(
                f"{path}: skills.{skill} declares render: {render} but names no template"
            )
        out[str(skill)] = RenderDecl(
            skill=str(skill), render=str(render), template=template
        )
    return out


# ---------------------------------------------------------------------------
# stamps read off the audit rows
# ---------------------------------------------------------------------------


@dataclass
class WakeStamp:
    """One EMITTED_WAKE row's body stamps. Hashes only, ever."""

    ts: datetime
    skill_name: str
    hashes_full: list[str] = field(default_factory=list)
    hashes_skeleton: list[str] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)  # [{item_key, ack_code}]
    row_id: Optional[str] = None
    consumed: int = 0  # dispatch pairings consumed against this wake

    @property
    def dispatch_capacity(self) -> int:
        return max(len(self.hashes_full), 1)


@dataclass
class DispatchStamp:
    """One CONFIRM_SEND_DISPATCHED row's render stamps. Hashes only, ever --
    this dataclass is as body-free as the verdicts, and for the same reason.

    ``rendered_body_sha256`` hashes the RAW markdown the renderer authored (the
    primary check's half of the join). ``plain_body_sha256`` hashes the exact
    ``render_plain`` text the overlay attached to the channel, which is what a
    mailbox fetch can actually be compared against; ``""`` means the seat's
    pinned overlay does not stamp it yet, and the channel check holds instead of
    grading.
    """

    ts: datetime
    skill_name: str  # the COLUMN; "" on a seat whose overlay predates it (B3)
    rendered_body_sha256: str
    body_variant: str  # full | skeleton | "" when unstamped
    row_id: Optional[str] = None
    plain_body_sha256: str = ""  # "" == overlay predates the stamp; hold, never find
    plain_consumed: bool = False  # one stamp vouches for exactly one channel body
    join_keys: frozenset = (
        frozenset()
    )  # message ids / audit token: the identity join (B7)


def _parse_ts(value) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def _metadata(row: dict) -> dict:
    raw = row.get("metadata")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _hash_entries(meta: dict) -> tuple[list[str], list[str]]:
    """The wake row's ``body_sha256`` list -> (full hashes, skeleton hashes).

    Tolerant of the entry being a bare hex string (a single-dispatch shorthand
    an emitter might reasonably write) but never of inventing one: anything not
    a string or a mapping contributes nothing.
    """
    full: list[str] = []
    skeleton: list[str] = []
    entries = meta.get("body_sha256")
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, str) and entry:
            full.append(entry)
        elif isinstance(entry, dict):
            if (
                isinstance(entry.get("body_sha256_full"), str)
                and entry["body_sha256_full"]
            ):
                full.append(entry["body_sha256_full"])
            if (
                isinstance(entry.get("body_sha256_skeleton"), str)
                and entry["body_sha256_skeleton"]
            ):
                skeleton.append(entry["body_sha256_skeleton"])
    return full, skeleton


def index_wakes(rows: list[dict]) -> list[WakeStamp]:
    """Every EMITTED_WAKE row, as a stamp. Rows with no hashes still index --
    the ``items`` half feeds ``ack_invariant`` whether or not the render
    cluster has landed body hashes on this seat yet."""
    out: list[WakeStamp] = []
    for row in rows:
        if row.get("action_type") != "EMITTED_WAKE" or not row.get("ts"):
            continue
        meta = _metadata(row)
        full, skeleton = _hash_entries(meta)
        raw_items = meta.get("items")
        items = [
            entry
            for entry in (raw_items if isinstance(raw_items, list) else [])
            if isinstance(entry, dict)
            and entry.get("item_key")
            and entry.get("ack_code")
        ]
        out.append(
            WakeStamp(
                ts=_parse_ts(row["ts"]),
                skill_name=str(row.get("skill_name") or ""),
                hashes_full=full,
                hashes_skeleton=skeleton,
                items=items,
                row_id=row.get("id"),
            )
        )
    return sorted(out, key=lambda stamp: stamp.ts)


def index_dispatches(rows: list[dict]) -> list[DispatchStamp]:
    """Every CONFIRM_SEND_DISPATCHED row that SENT (outcome == sent).

    ``refused`` / ``transport_error`` siblings delivered nothing, so there is
    no body to verify. A sent row with no ``rendered_body_sha256`` still
    indexes (variant ``""``): for a templated skill that absence is itself the
    hold this phase reports.
    """
    out: list[DispatchStamp] = []
    for row in rows:
        if row.get("action_type") != "CONFIRM_SEND_DISPATCHED" or not row.get("ts"):
            continue
        meta = _metadata(row)
        if meta.get("outcome") != "sent":
            continue
        rendered = meta.get("rendered_body_sha256")
        plain = meta.get("plain_body_sha256")
        out.append(
            DispatchStamp(
                ts=_parse_ts(row["ts"]),
                skill_name=str(row.get("skill_name") or ""),
                rendered_body_sha256=rendered if isinstance(rendered, str) else "",
                body_variant=str(meta.get("body_variant") or ""),
                row_id=row.get("id"),
                # Absent on any seat whose pinned overlay predates the plain
                # stamp. Parsed, never defaulted to something truthy: "" is the
                # signal the channel check reads to keep holding.
                plain_body_sha256=plain if isinstance(plain, str) else "",
                join_keys=frozenset(_usable_ids(meta)),
            )
        )
    return sorted(out, key=lambda stamp: stamp.ts)


# ---------------------------------------------------------------------------
# verdicts -- structurally body-free
# ---------------------------------------------------------------------------


@dataclass
class BodyVerdict:
    """One verified (or unverifiable) send. NO body field exists on purpose:
    the leak-safety is structural, and test_send_verify.py pins it."""

    skill_name: str
    verdict: str
    wake_ts: Optional[str] = None
    dispatch_ts: Optional[str] = None
    message_id: Optional[str] = None
    expected_sha256: Optional[str] = None
    actual_sha256: Optional[str] = None
    detail: Optional[str] = None
    attribution: str = (
        ""  # skill | hash | "" -- how the pair was made (send_attribution)
    )

    @property
    def is_finding(self) -> bool:
        return self.verdict == VERDICT_DIVERGED

    @property
    def is_hold(self) -> bool:
        return self.verdict in _HOLD_VERDICTS

    @property
    def is_degraded(self) -> bool:
        return self.verdict == VERDICT_DEGRADED

    @property
    def is_pre_edge(self) -> bool:
        return self.verdict == VERDICT_PRE_EDGE


def verify_hash_join(
    wakes: list[WakeStamp],
    dispatches: list[DispatchStamp],
    declares: dict[str, RenderDecl],
    *,
    window_s: int = VERIFY_WINDOW_S,
) -> list[BodyVerdict]:
    """The PRIMARY check: dispatch stamp vs wake stamp, per skill, consumed.

    Only skills declared ``templated`` / ``slot-templated`` are graded -- a
    compositional skill has no authored hash to diverge from. A wake stamp is
    consumed per dispatch entry it carried (``dispatch_capacity``), so one wake
    cannot launder an unbounded run of dispatches.

    The pairing itself is ``send_attribution.claim_wake``: skill-first off the
    ``skill_name`` column, hash-second for an unlabelled dispatch whose rendered
    hash a wake stamped (a seat whose pinned overlay predates the column). An
    unlabelled dispatch no wake recognises gets no verdict -- establishment ops
    notes are exactly that shape and must not redden every run.
    """
    verdicts: list[BodyVerdict] = []
    for dispatch in dispatches:
        wake, attribution = claim_wake(wakes, dispatch, declares, window_s)
        if wake is None and not attribution:
            continue
        if wake is None:
            verdicts.append(
                BodyVerdict(
                    skill_name=dispatch.skill_name,
                    verdict=VERDICT_NO_WAKE_HASH,
                    dispatch_ts=dispatch.ts.isoformat(),
                    actual_sha256=dispatch.rendered_body_sha256 or None,
                    detail="templated send with no wake hash in window",
                )
            )
            continue
        verdicts.append(_grade_pair(wake, dispatch, attribution))
    return verdicts


def _grade_pair(
    wake: WakeStamp, dispatch: DispatchStamp, attribution: str
) -> BodyVerdict:
    common = {
        # The wake's name when the dispatch carried none (attributed by hash).
        "skill_name": dispatch.skill_name or wake.skill_name,
        "wake_ts": wake.ts.isoformat(),
        "dispatch_ts": dispatch.ts.isoformat(),
        "actual_sha256": dispatch.rendered_body_sha256 or None,
        "attribution": attribution,
    }
    if not dispatch.rendered_body_sha256:
        return BodyVerdict(
            verdict=VERDICT_NO_DISPATCH_STAMP,
            expected_sha256=wake.hashes_full[0],
            detail="dispatch row carries no rendered_body_sha256",
            **common,
        )
    if dispatch.rendered_body_sha256 in wake.hashes_full:
        return BodyVerdict(
            verdict=VERDICT_MATCH, detail=_ATTRIBUTION_DETAIL.get(attribution), **common
        )
    if dispatch.rendered_body_sha256 in wake.hashes_skeleton:
        # The authored fallback ladder delivered the identifier-free skeleton.
        # Designed behavior under a render fault -- reported, never a finding.
        return BodyVerdict(
            verdict=VERDICT_DEGRADED, detail="skeleton fallback delivered", **common
        )
    return BodyVerdict(
        verdict=VERDICT_DIVERGED,
        expected_sha256=wake.hashes_full[0],
        detail="dispatch hash matches neither full nor skeleton wake hash",
        **common,
    )


def verify_channel_bodies(
    sent: list[dict],
    wakes: list[WakeStamp],
    declares: dict[str, RenderDecl],
    fetch_body: Callable[[dict], Optional[str]],
    *,
    dispatches: Optional[list[DispatchStamp]] = None,
    window_s: int = VERIFY_WINDOW_S,
    attribute: Optional[Callable[[dict], Optional[str]]] = None,
) -> list[BodyVerdict]:
    """The SECONDARY check: what the mailbox actually holds, canon-hashed.

    For each wake stamp of a hash-verified skill, mailbox messages inside the
    window are claimed one-to-one (oldest first) and their fetched bodies
    canon-compared to the wake's full/skeleton hashes. A body that matches
    neither is then compared to the dispatch row's ``plain_body_sha256`` -- the
    hash of the exact ``render_plain`` text the overlay attached -- because the
    wake stamps hash raw markdown and the mailbox stores the plain rendering, so
    the wake stamp alone can never match a templated send's channel body.

    IDENTITY, NOT PROXIMITY (B7). ``attribute`` is the tri-state callable from
    ``send_attribution.message_attributor`` (built here from ``dispatches`` when
    not supplied): a message whose dispatch row names THIS wake's skill is
    claimable; one whose row names another skill, or no skill at all (an
    in-turn send inside a tracker's hour -- the motion-calendar case), is never
    claimed by this wake; one no row joins is claimed by window as before but
    graded hold-only, because a finding accuses a routine and nothing ties that
    message to one.

    ``dispatches`` is optional: without it (and on any seat whose overlay does
    not stamp the plain hash yet) the check keeps its pre-calibration
    ``channel_mismatch_hold``. The body itself exists only inside this function.
    """
    verdicts: list[BodyVerdict] = []
    claimed: set[int] = set()
    stamps = dispatches or []
    attribute = attribute or message_attributor(stamps, wakes, declares, window_s)
    # THE DISCRIMINATOR for what an absent plain stamp means: the DEPLOY EDGE,
    # the earliest row on the inbox that carries the stamp. Per ROW, not per
    # inbox -- a window that spans the seat's reprovision holds rows from both
    # sides of it, and a per-inbox boolean misgraded every pre-edge row as a
    # deliberate omission (send_attribution.plain_stamp_edge has the incident).
    # Fail-safe direction unchanged: no edge => never a finding on this path.
    plain_edge = plain_stamp_edge(stamps)
    ordered = sorted(sent, key=lambda m: str(m.get("timestamp") or ""))
    for wake in wakes:
        decl = declares.get(wake.skill_name)
        if decl is None or not decl.hash_verified or not wake.hashes_full:
            continue
        for capacity_used, (index, message, identified) in enumerate(
            _messages_in_window(ordered, wake, window_s, claimed, attribute)
        ):
            if capacity_used >= wake.dispatch_capacity:
                break
            claimed.add(index)
            verdicts.append(
                _grade_channel_body(
                    wake, message, fetch_body, stamps, window_s, plain_edge, identified
                )
            )
    return verdicts


def _messages_in_window(ordered, wake, window_s, claimed, attribute):
    """Unclaimed messages in the wake's window this wake may claim, with whether
    a dispatch row IDENTIFIED each one (tri-state: the wake's own skill -> yes;
    another skill or "" -> not this wake's, skipped; None -> unidentified)."""
    for index, message in enumerate(ordered):
        if index in claimed or not message.get("timestamp"):
            continue
        owner = attribute(message)
        if owner is not None and owner != wake.skill_name:
            continue
        stamp = _parse_ts(message["timestamp"])
        if wake.ts <= stamp <= wake.ts + timedelta(seconds=window_s):
            yield index, message, owner is not None


def _grade_channel_body(
    wake, message, fetch_body, dispatches, window_s, plain_edge, identified
) -> BodyVerdict:
    common = {
        "skill_name": wake.skill_name,
        "wake_ts": wake.ts.isoformat(),
        "message_id": str(message.get("message_id") or "") or None,
        "expected_sha256": wake.hashes_full[0],
    }
    try:
        body = fetch_body(message)
    except Exception as exc:  # noqa: BLE001 -- transport discipline: any failure HOLDS
        return BodyVerdict(
            verdict=VERDICT_BODY_UNAVAILABLE,
            detail=f"body fetch failed: {exc}",
            **common,
        )
    if not isinstance(body, str) or not body:
        return BodyVerdict(
            verdict=VERDICT_BODY_UNAVAILABLE,
            detail="channel returned no text body",
            **common,
        )
    digest = canonical_body_sha256(body)
    if digest in wake.hashes_full:
        return BodyVerdict(verdict=VERDICT_MATCH, actual_sha256=digest, **common)
    if digest in wake.hashes_skeleton:
        return BodyVerdict(
            verdict=VERDICT_DEGRADED,
            actual_sha256=digest,
            detail="channel body matches the skeleton fallback",
            **common,
        )
    # Raw-markdown wake stamps cannot match a down-rendered channel body, so the
    # dispatch row is the only counterpart that can settle this. Which of its
    # two hashes to use is decided by the overlay's own semantics, below.
    stamp = claim_dispatch_stamp(dispatches, wake, window_s, message)
    if stamp is None:
        return BodyVerdict(
            verdict=VERDICT_CHANNEL_MISMATCH,
            actual_sha256=digest,
            detail=(
                "channel body hash differs from wake stamp and no stamped dispatch row "
                "in window is left to compare against (hold, not finding)"
            ),
            **common,
        )
    common["dispatch_ts"] = stamp.ts.isoformat()
    if stamp.plain_body_sha256:
        # A down-render happened and the overlay hashed exactly what it handed
        # the channel. This is the comparison the check was always trying to
        # make; the plain stamp, not the raw wake hash, is what it expected.
        common["expected_sha256"] = stamp.plain_body_sha256
        return _graded(
            digest == stamp.plain_body_sha256,
            digest,
            "the dispatch plain_body_sha256",
            common,
            identified,
        )
    if not stamps_plain_at(plain_edge, stamp):
        return _behind_the_edge(plain_edge, digest, common)
    # POST-DEPLOY ROW. The overlay that wrote this row stamps plain hashes, so it
    # omitted this one deliberately: no down-render ran (prose reply,
    # composer-supplied html), which means the channel text IS the bytes the gate
    # allowed. Absence is a fact, not a gap; `rendered_body_sha256` is the counterpart.
    common["expected_sha256"] = stamp.rendered_body_sha256
    return _graded(
        digest == stamp.rendered_body_sha256,
        digest,
        "the dispatch rendered_body_sha256 (no down-render on this send)",
        common,
        identified,
    )


def _behind_the_edge(plain_edge, digest: str, common: dict) -> BodyVerdict:
    """A PRE-DEPLOY ROW: written by an overlay that predates
    hermes-smd-overlay#338, so absence of the plain stamp carries no
    information, the send may well have been down-rendered with nothing
    recording it, and grading it against the raw markdown would file a false
    BODY_DIVERGED. Never a finding. Which NON-finding it is depends on whether
    the seat has crossed the edge yet:

    * NO EDGE on the inbox -- the seat may still be on the old pin, and the
      stamp may still arrive once it is reprovisioned. Unevaluated today,
      evaluable tomorrow: a HOLD, red, the pressure to deploy.
    * AN EDGE EXISTS and this row is before it -- the seat now stamps, and
      this row is permanently on the far side of the boundary. No reprovision,
      no rehearsal, no fetch can ever produce its counterpart. Reddening the
      run on it every day is a check that cannot go green, which measures
      nothing (Law 12) and mutes the control: 2026-09-01..09-09 the scheduled
      run went red eleven times running on the single 09-01 escalator send,
      seventeen critical notifications accrued, and nobody acted. Reported as
      ``pre_stamp_edge`` -- counted, visible, indented -- and never a hold.
    """
    if plain_edge is None:
        return BodyVerdict(
            verdict=VERDICT_CHANNEL_MISMATCH,
            actual_sha256=digest,
            detail=(
                "channel body hash differs from wake stamp and no dispatch row on this "
                "inbox carries plain_body_sha256 yet (overlay predates the plain stamp; "
                "hold, not finding)"
            ),
            **common,
        )
    return BodyVerdict(
        verdict=VERDICT_PRE_EDGE,
        actual_sha256=digest,
        detail=(
            "channel body hash differs from wake stamp and this dispatch row predates "
            "the inbox's first plain_body_sha256 stamp; the seat stamps now, so nothing "
            "can ever grade this row (unverifiable by construction; neither hold nor finding)"
        ),
        **common,
    )


def _graded(
    matched: bool, digest: str, against: str, common: dict, identified: bool
) -> BodyVerdict:
    """MATCH or the finding, said once. Calibration is done: with a
    same-representation counterpart in hand, a mismatch can no longer be
    explained away by an uncalibrated channel transform, so it is a FINDING --
    PROVIDED a dispatch row identified the message (B7). A divergence on a
    message no row ties to this routine is the old proximity guess, and a guess
    holds; it never accuses."""
    if matched:
        return BodyVerdict(
            verdict=VERDICT_MATCH,
            actual_sha256=digest,
            detail=f"channel body matches {against}",
            **common,
        )
    if not identified:
        return BodyVerdict(
            verdict=VERDICT_CHANNEL_MISMATCH,
            actual_sha256=digest,
            detail=f"channel body hash differs from {against}, but no dispatch row identifies this message (claimed by window; hold, not finding)",
            **common,
        )
    return BodyVerdict(
        verdict=VERDICT_DIVERGED,
        actual_sha256=digest,
        detail=f"channel body hash differs from {against}",
        **common,
    )


# ---------------------------------------------------------------------------
# the verifier the reconciler calls (one object, so the caller stays small)
# ---------------------------------------------------------------------------


class SendVerifier:
    """Constructed once in reconcile-sends' main(); applied per inbox."""

    def __init__(self, declares: dict[str, RenderDecl], invariants: dict) -> None:
        self._declares = declares
        self._invariants = invariants

    def verify_inbox(
        self,
        sent: list[dict],
        rows: list[dict],
        fetch_body: Optional[Callable[[dict], Optional[str]]] = None,
    ) -> tuple[list[BodyVerdict], list[InvariantFinding], list[InvariantProposal]]:
        wakes = index_wakes(rows)
        dispatches = index_dispatches(rows)
        verdicts = verify_hash_join(wakes, dispatches, self._declares)
        if fetch_body is not None:
            verdicts += verify_channel_bodies(
                sent, wakes, self._declares, fetch_body, dispatches=dispatches
            )
        # Two tiers (send_invariants.py): conflicts with COMMITTED expectations
        # are findings; first-seen values are proposals for a reviewed
        # send-invariants.json PR, never findings.
        findings, proposals = recipient_invariant(
            rows, self._declares, self._invariants
        )
        ack_findings, ack_proposals = ack_invariant(
            wakes, self._declares, self._invariants
        )
        return verdicts, findings + ack_findings, proposals + ack_proposals


def verifier_from_contract(
    render_path: str = SEND_RENDER_PATH, invariants_path: str = DEFAULT_INVARIANTS_PATH
) -> SendVerifier:
    return SendVerifier(load_send_render(render_path), load_invariants(invariants_path))


# ---------------------------------------------------------------------------
# report integration helpers (hashes and rule names only; never a body)
# ---------------------------------------------------------------------------


from send_report import as_dicts, digest_keys, has_findings, has_holds, render_lines  # noqa: E402,F401 -- re-exports; callers and tests read unchanged

__all__ = [
    "BodyVerdict",
    "DispatchStamp",
    "InvariantFinding",
    "InvariantProposal",
    "RenderDecl",
    "SendRenderError",
    "SendVerifier",
    "WakeStamp",
    "ack_invariant",
    "as_dicts",
    "attribution_counts",
    "canonical_body_sha256",
    "digest_keys",
    "has_findings",
    "has_holds",
    "index_dispatches",
    "index_wakes",
    "load_invariants",
    "load_send_render",
    "recipient_invariant",
    "render_lines",
    "verifier_from_contract",
    "verify_channel_bodies",
    "verify_hash_join",
]
