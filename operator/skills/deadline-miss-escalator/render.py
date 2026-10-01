"""Deterministic renderer for the deadline-miss escalator's triaged alert.

WHY THIS FILE EXISTS (the 2026-08-24..31 outbound review). The digest projection
(``pre_run.project_digest``) already computed every value and every count, and
the model still re-composed the WORDS each morning: format drift across runs,
field names reaching the reader, a band rendered flat, run-on paragraphs. The
durable fix removes the model from routine-email composition: this module
renders ``references/output-format.md`` literally, in code, from the projected
digest — the turn composes nothing.

EVERY PHRASE HERE IS AUTHORED CLIENT-FACING CONTENT under the no-fabrication
policy (CLAUDE.md): the words come from this file's constants (reviewed
template text mirroring ``references/output-format.md``) and the values come
from the digest projection, which reads them off the firm's own records. An
unknown signal renders NOTHING — never a sentinel, never invented urgency.

No em dashes anywhere; a matter by its number and the client's surname read
off its title, never a caption; plain words, never a citation, never our own
vocabulary ("court-date", "task-deadline") (the law-seat first-draft rules;
the spec gate still checks the rendered text).

Stdlib only, loaded by absolute path from ``pre_run.py`` like the vendored
ledger (the scheduler may stage ``pre_run.py`` alone; the skill dir on the
volume carries the siblings). The item words (``head_words``, ``what_words``,
``when_words``) live in the sibling ``digest_items.py``, loaded by path here,
because the subject line is built from the same words.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path


def _load_digest_items():
    name = "escalator_render_digest_items"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent / "digest_items.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("digest_items.py is missing beside render.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_DI = _load_digest_items()

# canonical_body_sha256, the ONE body hash, lives in skill_helpers.py (shared
# with the verification tracker; arbiter fixture operator/contracts/fixtures/
# body-canon-vectors.json). dispatch_envelope.py stamps it.


# ---------------------------------------------------------------------------
# The signal -> phrase map (output-format.md rule 1, as computable here).
#
# CLOSED SET. Only signals the projection detects IN CODE render a consequence
# line; the rich phrases output-format.md once suggested (deemed-admission,
# disbursement blocker, opposing-counsel held) have no code-detectable
# authored source and therefore render NOTHING — deleting them from the
# model's vocabulary is the point. Overdue age gets no extra line: the
# overdue-by-N is already in the item line, and rule 1 says most-overdue is
# the plain default.
# ---------------------------------------------------------------------------

#: Authored client-facing phrases (no-fabrication policy). Keyed by the
#: projection's ``priority_marker`` value, which ``parse_pull`` extracts from
#: the Smokeball task subject's own words.
_PRIORITY_PHRASES = {
    "CRITICAL": "the task is marked CRITICAL in Smokeball",
    "URGENT": "the task is marked URGENT in Smokeball",
    "HIGH PRIORITY": "the task is marked HIGH PRIORITY in Smokeball",
}

#: What a calendar date inside the firm's date-prep window still lacks: no
#: date-prep brief has gone out for it (``prep_note_missing``, set by
#: ``casework_filter.py`` on seats that authored ``case_manager.date_prep``).
#: Replaced "a court date the firm authored" (2026-09-28): that told the reader
#: what the item WAS, in our words, and asked nothing of them.
_PREP_NOTE_PHRASE = "No prep note has gone out for this yet."

#: A person answered "done" to an earlier digest and the Smokeball write never
#: landed (``write_pending_stale``, set by ``casework_filter.py``). The line
#: owns the miss instead of listing the task as if nobody had answered.
_WRITE_PENDING_PHRASE = "I recorded your done on this earlier but could not update Smokeball; it is still open."


def consequence_line(item: dict) -> str | None:
    """The one plain line of what an item still needs, or None.

    Authored signal only: a recorded done that never reached Smokeball, a
    task-priority marker the record carries, else a date in the prep window
    with no brief out. Anything else renders nothing (rule 7: no invented
    urgency)."""
    if item.get("write_pending_stale") is True:
        return _WRITE_PENDING_PHRASE
    marker = item.get("priority_marker")
    if isinstance(marker, str) and marker in _PRIORITY_PHRASES:
        return _PRIORITY_PHRASES[marker]
    if item.get("prep_note_missing") is True:
        return _PREP_NOTE_PHRASE
    return None


# ---------------------------------------------------------------------------
# Value formatting — every helper renders a READ value or a typed absence.
# ---------------------------------------------------------------------------


def _matter_head(item_or_group: dict) -> str:
    """ "2026-PI-105 Okafor", "matter 201520 Crawford", or the exact authored
    absence phrase (``digest_items.head_words``).

    Every numbered line starts either with the word "matter" or with a number
    of the ``NNNN-`` shape: those are the two forms the overlay's reply parser
    (``reply_items._DIGEST_ITEM_LINE``) uses to recognize quoted digest text in
    a reply and refuse to read numbers from it."""
    return _DI.head_words(item_or_group)


def _item_line(item: dict) -> str:
    """``<matter head>: <what>, <when> (<relative>)``, the shared core of a
    needs-you and blanket line: "2026-PI-105 Okafor: Final Status Conference,
    Fri Oct 2, 2026 (in 4 days)". What the item is comes from the record (the
    task subject or calendar title, masked at parse time), else a plain noun;
    the date is the authored one, spelled out."""
    return f"{_matter_head(item)}: {_DI.what_words(item)}, {_DI.when_words(item)}"


def _day_of(ts: str | None) -> str | None:
    """The YYYY-MM-DD day of an ISO timestamp, or None. The under-active band
    renders the day the Operator last raised, which is what the handoff seeds."""
    if not isinstance(ts, str) or len(ts) < 10:
        return None
    day = ts[:10]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return day
    return None


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


# ---------------------------------------------------------------------------
# The rekey notice (Q6 — ACK identity correction, authored, self-extinguishing)
# ---------------------------------------------------------------------------

_REKEY_NOTICE = (
    "Item identity was corrected for {n} calendar item{s}; previously "
    "acknowledged items may resurface once, listed under their matter below."
)


# ---------------------------------------------------------------------------
# render_digest — references/output-format.md, literally.
# ---------------------------------------------------------------------------

#: The footer of a numbered digest. No literal example numbers: a reader who
#: copies an example would answer an item they never read.
REPLY_FOOTER = (
    "Reply to this email with the numbers you have, or say all; each one goes "
    "quiet for {ack_snooze_days} days and stays open. Say which ones are done "
    "and I'll close them in Smokeball. This is an internal note; no client was "
    "contacted."
)

#: The footer of a body with no answerable number (every unit past the append
#: cap, or rendered with no rows behind it). No invitation to reply.
UNNUMBERED_FOOTER = "Finishing an item in Smokeball clears it. This is an internal note; no client was contacted."


def _preamble(items: list[dict]) -> str | None:
    """The line under the needs-you heading, or None.

    It states the ORDER only when the order carries information: a priority
    marker on any item means the list is ranked by what the record says;
    otherwise differing dates mean most-overdue first; items that share every
    signal get no preamble, because "most consequential first" over
    indistinguishable items claims a ranking that did not happen."""
    if any(item.get("priority_marker") for item in items):
        return "Ranked by what the record says, most consequential first."
    if len({item.get("authored_date") for item in items}) > 1:
        return "Most overdue first."
    return None


def _marker(unit: dict) -> str:
    """``N.`` for a unit that carries the number its ledger rows carry, else a
    plain bullet. The number comes from ``digest_items.number_firing`` and is
    the one the reader answers with; a unit past the append cap, or a body
    rendered with no rows behind it, has none and must not invite a reply."""
    n = unit.get("n")
    return f"{n}." if isinstance(n, int) and not isinstance(n, bool) else "-"


def _numbered(digest: dict) -> bool:
    """Whether any line in this body carries an answerable number."""
    units = list(digest.get("needs_you") or []) + list(digest.get("blanket_ack_only") or [])
    units += list((digest.get("admin_confirms") or {}).get("matters") or [])
    return any(_marker(u) != "-" for u in units)


def _needs_you_block(items: list[dict]) -> list[str]:
    lines = [f"## Needs you today ({len(items)})", ""]
    if not items:
        return lines
    preamble = _preamble(items)
    if preamble:
        lines += [preamble, ""]
    for item in items:
        lines.append(f"{_marker(item)} {_item_line(item)}")
        reason = consequence_line(item)
        if reason:
            lines.append(f"   {reason}")
    return lines + [""]


def _overflow_block(band: dict | None) -> list[str]:
    """The overflow band (digest key ``admin_confirms``): stable firing items
    past the top five, collapsed per matter. Named for what it is, "Also
    open": nothing in the record says these are routine, and the 2026-09-22
    pilot email filed a recipient's overdue deadlines under "routine
    confirmations" because they ranked sixth seat-wide."""
    if not (isinstance(band, dict) and band.get("matters")):
        return []
    total = int(band.get("total") or 0)
    lines = [
        f"## Also open ({total} across {_plural(int(band.get('matter_count') or 0), 'matter')})",
        "",
        "More open items past the top five, one line per matter. Answering a "
        "matter's number covers all of its items; each one is listed in Smokeball.",
        "",
    ]
    for group in band["matters"]:
        count = int(group.get("count") or 0)
        more = f"{count} more open item" + ("" if count == 1 else "s")
        lines.append(f"{_marker(group)} {_matter_head(group)}: {more}")
    return lines + [""]


def _task_review_block(review: dict | None) -> list[str]:
    """One line for the overdue tasks the task review holds (case-manager
    seats only; the key is absent everywhere else). The day is the firm's own
    schedule for the review, read from its cron entry, or no day at all."""
    if not isinstance(review, dict):
        return []
    count = int(review.get("count") or 0)
    if count <= 0:
        return []
    day = review.get("day")
    where = f"{day}'s task review" if isinstance(day, str) and day else "the next task review"
    noun = "overdue task is" if count == 1 else "overdue tasks are"
    return [f"{count} more {noun} in {where}.", ""]


def _done_since_block(rows: list[dict]) -> list[str]:
    """Work the Operator finished without asking, told once (case-manager
    seats with ``quiet`` authored; the key is absent everywhere else). The
    lines are ``done_since.py``'s, rendered from the casework ledger rows."""
    lines = [str(r.get("line") or "").strip().rstrip(". ") for r in rows or []]
    lines = [line for line in lines if line]
    if not lines:
        return []
    return ["Done since last time: " + "; ".join(lines) + ".", ""]


def _elsewhere_block(band: dict | None) -> list[str]:
    if not (isinstance(band, dict) and band.get("matters")):
        return []
    total = int(band.get("total") or 0)
    lines = [
        f"## Under active escalation elsewhere ({total} across "
        f"{_plural(int(band.get('matter_count') or 0), 'matter')})",
        "",
        "Already raised, shown so it is not double-counted. No action here.",
        "",
    ]
    for group in band["matters"]:
        raised = _day_of(group.get("last_raised"))
        tail = f" (last raised {raised})" if raised else ""
        count = _plural(int(group.get("count") or 0), "item")
        lines.append(f"- {_matter_head(group)}: {count} under active escalation{tail}.")
    return lines + [""]


def _clearance_block(items: list[dict]) -> list[str]:
    if not items:
        return []
    lines = [
        f"## Awaiting clearance ({len(items)})",
        "",
        "Held matters with an approaching date. Surfaced for a person to clear; never a client-facing step.",
        "",
    ]
    for item in items:
        lines.append(f"- {_item_line(item)}. The matter is on CONFLICT-HOLD.")
    return lines + [""]


def _blanket_runs(items: list[dict]) -> list[list[dict]]:
    """Blanket items per matter, in first-appearance order. ``number_firing``
    already made each matter's items contiguous and gave them one number; this
    only re-forms the runs for rendering."""
    runs: dict[object, list[dict]] = {}
    for item in items:
        runs.setdefault(item.get("matter_id"), []).append(item)
    return list(runs.values())


def _blanket_block(items: list[dict]) -> list[str]:
    """Items with no stable task id in Smokeball: they cannot be told apart by
    a task, so they are answered per matter. The group line says exactly that,
    and the items are listed under it so the reader sees what a number covers."""
    if not items:
        return []
    lines = [
        f"## Open, not tied to one task ({len(items)})",
        "",
        "Items Smokeball holds without a task of their own, one line per matter. Answering a "
        "matter's number covers every item listed under it.",
        "",
    ]
    for run in _blanket_runs(items):
        count = f"{len(run)} open item" + ("" if len(run) == 1 else "s")
        lines.append(f"{_marker(run[0])} {_matter_head(run[0])}: {count} not tied to a task")
        lines += [f"   - {_item_line(item)}" for item in run]
    return lines + [""]


def _probe_line(probe: dict | None) -> list[str]:
    if not (isinstance(probe, dict) and (probe.get("excluded") or probe.get("stale"))):
        return []
    note = f"Probe artifacts excluded from this digest: {int(probe.get('excluded') or 0)}."
    stale_ids = [str(x) for x in (probe.get("stale_task_ids") or [])]
    if stale_ids:
        note += " Stale probe task ids awaiting teardown: " + ", ".join(stale_ids) + "."
    return ["", note]


def render_digest(
    digest: dict,
    *,
    ack_snooze_days: int,
    rekey_count: int = 0,
) -> str:
    """Render the projected digest into the triaged alert body.

    The digest supplies the VALUES and the MEMBERSHIP; the band helpers above
    supply the WORDS and the MARKUP (output-format.md). Counts are copied,
    never recomputed; empty sections are omitted whole (rule 9); the footer is
    a sibling of the lists, never nested (2026-08-14). The subject is NOT
    included: the caller sends it as the message subject, verbatim from
    ``digest['subject']``."""
    lines: list[str] = []
    if rekey_count > 0:
        lines += [_REKEY_NOTICE.format(n=rekey_count, s="" if rekey_count == 1 else "s"), ""]
    # Done, then needs you (case-manager spec, rule 4).
    lines += _done_since_block(digest.get("done_since") or [])
    lines += _needs_you_block(digest.get("needs_you") or [])
    lines += _overflow_block(digest.get("admin_confirms"))
    lines += _task_review_block(digest.get("task_review"))
    lines += _elsewhere_block(digest.get("under_active_escalation_elsewhere"))
    lines += _clearance_block(digest.get("awaiting_clearance") or [])
    lines += _blanket_block(digest.get("blanket_ack_only") or [])
    # The single footer, a SIBLING of the lists (rule 4; the 2026-08-14 HTML
    # rendered it as a list child). It invites a reply only when a number in
    # the body has a ledger row behind it; otherwise a reply would find
    # nothing to quiet, so it says only how an item closes.
    lines.append(REPLY_FOOTER.format(ack_snooze_days=ack_snooze_days) if _numbered(digest) else UNNUMBERED_FOOTER)
    lines += _probe_line(digest.get("probe_artifacts"))
    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# render_skeleton — the fallback body: counts only, ZERO identifiers and ZERO
# dates, so it passes every gate on a run where the full body cannot.
# ---------------------------------------------------------------------------


def render_skeleton(digest: dict) -> str:
    """Authored minimal body. Identifier-free by construction: no matter
    numbers, no dates, no item numbers, no task ids, only counts. It carries
    no numbered items, so it does not invite a reply. Tested by regex
    assertion in test_render_routing.py."""
    need = len(digest.get("needs_you") or []) + len(digest.get("blanket_ack_only") or [])
    more = int((digest.get("admin_confirms") or {}).get("total") or 0)
    lines = [
        "## Deadline digest (details unavailable)",
        "",
        f"{_plural(need, 'item')} {'needs' if need == 1 else 'need'} a person now and "
        f"{_plural(more, 'more open item')} {'is' if more == 1 else 'are'} tracked, but the "
        "detailed digest could not be delivered this run. Open Smokeball or "
        "the tracker view for the items; the next run will retry the full "
        "digest.",
        "",
        "This is an internal alert to a person at the firm; no client message has been sent.",
    ]
    return "\n".join(lines) + "\n"


#: The one-line failure note the turn may send when its Script Output shows
#: ``dispatch_expected: true`` and no dispatch note was injected (the deploy
#: skew window), or on a delivery fault. Authored, no slots; the in-turn
#: rendered-body check accepts exactly this text.
FAILURE_NOTE = (
    "The deadline digest run failed and needs attention; no digest was "
    "delivered this run. The items are in Smokeball and the tracker view."
)

#: Subject for the failure note when the GATE dispatches it out of turn rather
#: than instructing the turn to send it (2026-09-02). Deliberately carries no
#: date and no counts: the run that sends this could read nothing, so every
#: number in it would be invented, and a date in a deadline subject line is the
#: one thing a reader is most likely to mistake for a deadline.
FAILURE_NOTE_SUBJECT = "[Deadlines] run failed, no digest delivered"
