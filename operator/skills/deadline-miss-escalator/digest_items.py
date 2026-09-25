"""Digest-item shaping shared by ``pre_run.py`` and ``dispatch_envelope.py``.

Two jobs, both about the dicts the digest projection carries:

1. **A task label that is safe to show** (``display_label``). The 2026-09-24
   review of the pilot's Deadlines emails: every line said "matter X,
   task-deadline D" and nothing else, so a reader could not tell which task was
   late without opening Smokeball. The Smokeball task subject says which, but
   it is free text a person typed, and the send gate refuses a body that
   carries an identifier, date, dollar figure or case caption it cannot trace
   to a read. A refused full body falls back to the counts-only skeleton, which
   is worse than no label. So the label is built HERE, at parse time, as a
   reduced copy of the subject: shapes the gate checks are masked, captions
   refuse the whole label, and the raw subject never leaves ``parse_pull``.

2. **One needs-you order, and per-recipient banding** (``needs_you_key``,
   ``rebalance_bands``). "Needs you today" used to be the top five across the
   whole seat, split by recipient afterwards, so a recipient whose items ranked
   sixth or lower got "0 need you" and their overdue deadlines filed as
   overflow (the 2026-09-22 pilot email). The seat-wide projection and the
   per-recipient split now sort with the same key, and each recipient's items
   are re-banded after the split.

Stdlib only, path-loaded sibling (the scheduler may stage ``pre_run.py`` alone;
the skill dir on the volume carries this file).
"""

from __future__ import annotations

import re

#: Top of the needs-you band per alert (output-format rule 2).
NEEDS_YOU_MAX = 5

#: Longest label rendered, in characters, before the trailing ellipsis.
LABEL_MAX_CHARS = 100

_MASK = "\u2026"  # the single-character ellipsis
_PROVENANCE_MARK = "[operator]"

# ---------------------------------------------------------------------------
# Shapes the overlay's send gate checks, vendored as regex copies.
#
# PARITY NOTE. These mirror, and must stay at least as wide as, the patterns in
# venturecrane/hermes-smd-overlay (read 2026-09-24 at 9f6b5ea):
#
#   shared/identifier_filter.py:128  _A_NUMBER_RE
#   shared/identifier_filter.py:130  _RECEIPT_RE
#   shared/identifier_filter.py:132  _SSN_RE
#   shared/identifier_filter.py:148  _CASE_RE (docket + matter-number shapes)
#   shared/identifier_filter.py:157  _DATE_RES (numeric, ISO, month-name)
#   shared/identifier_filter.py:225  _BARE_MATTER_NUMBER_RE (known-number scan)
#   shared/identifier_filter.py:262  MONEY_RE
#   shared/fabrication_markers.json  "specific-dollar-amount" (\$\s?\d)
#   shared/citation_filter.py:45     CASE_NAME_RE / CASE_NAME_VERSUS_RE, and
#   shared/citation_filter.py:91-118 REPORTER_CITE_RE / STATUTE_RE / RULE_RE /
#                                    BLUEBOOK_SIGNALS_RE (screened, not copied)
#
# The overlay cannot be imported here (this runs under the connector venv).
# Wider is the safe direction: a token masked here that the gate would have
# passed costs a word of the label; a token this misses costs the full body.
# ---------------------------------------------------------------------------

_MONTHS = (
    r"(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
)

_MASKED_SHAPES: tuple[re.Pattern[str], ...] = (
    # Money first, so "$1,200.50" masks whole rather than as a bare-digit run.
    re.compile(r"\$\s*\d[\d,]*(?:\.\d{1,2})?"),
    re.compile(r"\b\d[\d,]*(?:\.\d{2})?\s?(?:usd|dollars?)\b", re.IGNORECASE),
    # Dates.
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:T[\d:.]+Z?)?"),
    re.compile(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b"),
    re.compile(rf"\b{_MONTHS}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b", re.IGNORECASE),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTHS}\.?(?:\s+\d{{4}})?\b", re.IGNORECASE),
    # Case, docket and matter numbers.
    re.compile(
        r"\b(?:\d{1,2}:\d{2}-[a-z]{2}-\d{3,6}|No\.?\s?\d{2,4}-\d{2,6}|\d{4}-[A-Z]{2}-\d{3,4}|[A-Z]{2}-\d{4}-\d{4})\b",
        re.IGNORECASE,
    ),
    # A-number, USCIS receipt, SSN.
    re.compile(r"\bA#?[-\s]?(?:\d[-\s]?){8,9}\b"),
    re.compile(r"\b[A-Z]{3}\d{10}\b"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    # Any remaining run of three or more digits: the gate's known-number scan
    # reads bare digit runs as matter numbers, and a foreign matter's number
    # beside this line's date is a mispairing.
    re.compile(r"\d{3,}"),
)

#: A caption or a legal citation refuses the whole label: "Smith v. Jones"
#: names parties (a masked caption still reads as one), and the law-vertical
#: citation screen refuses a body carrying a reporter, statute or rule cite.
#: Compact on purpose: the separator words, the section sign, the statute and
#: rule abbreviations, the Bluebook signals, and the generic reporter shape
#: (number, dotted abbreviation, number).
_CAPTION_RE = re.compile(
    r"(?:^|\s)(?:v|vs|versus)\.?\s|\bin\s+re\b|\u00a7"
    r"|\bU\.?\s?S\.?\s?C\b|\bC\.\s?F\.\s?R\b|\bA\.\s?R\.\s?S\b|\bFed\.?\s?R\b|\b(?:FRCP|FRCrP|FRAP|FRE)\b"
    r"|\bL\.?\s?R\.?\s?Civ\b|\b(?:supra|infra|id\.|cf\.)"
    r"|\b\d{1,4}\s+[A-Z][A-Za-z.' &]{0,24}\.\s?(?:\d[a-z]{1,2}\s+)?\d{1,5}\b",
    re.IGNORECASE,
)

#: Tier-1 fabrication markers other than the dollar sign (masked above) and the
#: em dash (normalized below). Any hit refuses the whole label, because the
#: gate refuses the whole body for one. Mirrors shared/fabrication_markers.json.
_MARKER_RE = re.compile(
    r"coming soon|we(?:'ll| ?ll| will) reach out|work begins within|within two weeks of signing"
    r"|repl(?:y|ies) within \d+ business day|stabilization period|\bguarantee(?:d|s)?\b"
    r"|by next week|by end of|we shadow and observe|we redesign together|training and handoff"
    r"|\byour\s+(?:profile|preferences)\b|\bprofile\s+(?:on|about)\s+you\b",
    re.IGNORECASE,
)

#: Markdown control characters, neutralized rather than backslash-escaped: the
#: overlay's renderer (shared/report_render.py:65-78) has no escape syntax, so
#: "\*" would reach the reader as a literal backslash. Dropped characters carry
#: emphasis or markup only; brackets become parentheses so a bracketed phrase
#: is not mistaken for an ACK code, and double quotes become single because the
#: label is rendered inside double quotes.
_MARKDOWN_TRANSLATE = str.maketrans(
    {
        "*": None,
        "_": " ",
        "`": None,
        "~": None,
        "#": None,
        "<": None,
        ">": None,
        "|": " ",
        "\\": "/",
        "[": "(",
        "]": ")",
        '"': "'",
        "\u2014": "-",
        "\u2013": "-",
    }
)


def display_label(subject: object) -> str | None:
    """The task subject reduced to a label the send gate will pass, or None.

    None means "render no label": no subject, a caption, a fabrication marker,
    or nothing left once the gate's shapes are masked."""
    if not isinstance(subject, str):
        return None
    text = " ".join(subject.split())
    if text.lower().startswith(_PROVENANCE_MARK):
        text = text[len(_PROVENANCE_MARK) :].strip()
    if not text or _CAPTION_RE.search(text) or _MARKER_RE.search(text):
        return None
    for shape in _MASKED_SHAPES:
        text = shape.sub(_MASK, text)
    text = " ".join(text.translate(_MARKDOWN_TRANSLATE).split())
    if not any(ch.isalpha() for ch in text):
        return None
    if len(text) > LABEL_MAX_CHARS:
        text = text[: LABEL_MAX_CHARS - 1].rstrip() + _MASK
    return text


# ---------------------------------------------------------------------------
# Ordering and banding over digest-item dicts.
# ---------------------------------------------------------------------------


def needs_you_key(item: dict) -> tuple:
    """Authored priority marker first, then most overdue, then stable
    tie-breaks. The ONE order: the seat-wide projection and every
    per-recipient re-band sort with it."""
    return (
        0 if item.get("priority_marker") else 1,
        int(item.get("days_out") or 0),
        str(item.get("matter_id") or ""),
        str(item.get("task_id") or ""),
    )


def group_by_matter(items: list[dict]) -> dict:
    """Collapse a band's items into per-matter groups, counts by construction.

    Every count is a list length, never arithmetic, and the group carries the
    matter's own ``matter_number`` (plus its typed absence) so the renderer
    can name the matter without reaching back into an item. ``last_raised`` is
    the LATEST across the group: a group rendered as one line can state only
    one date, and the most recent raise answers "is anyone on this?"."""
    by_matter: dict[str, list[dict]] = {}
    for item in items:
        by_matter.setdefault(item["matter_id"], []).append(item)
    groups = [
        {
            **{k: g[0].get(k) for k in ("matter_id", "matter_number", "matter_number_absent")},
            "count": len(g),
            "ack_codes": [i["ack_code"] for i in g if i.get("ack_code")],
            "last_raised": max((i["last_raised"] for i in g if i.get("last_raised")), default=None),
            "items": g,
        }
        for _, g in sorted(by_matter.items())
    ]
    return {"total": len(items), "matter_count": len(groups), "matters": groups}


def band_stable_items(items: list[dict]) -> tuple[list[dict], dict | None]:
    """``(needs_you, overflow_band)`` for a list of stable firing items: the
    top ``NEEDS_YOU_MAX`` by ``needs_you_key``, the rest grouped per matter
    (None when there is no rest)."""
    ordered = sorted(items, key=needs_you_key)
    rest = ordered[NEEDS_YOU_MAX:]
    return ordered[:NEEDS_YOU_MAX], (group_by_matter(rest) if rest else None)


def rebalance_bands(sub: dict) -> dict:
    """Re-band one recipient's sub-digest in place and return it.

    The split filters the seat-wide bands by matter, which leaves a recipient
    whose items ranked sixth or lower with an empty needs-you band. This pools
    the recipient's stable items (needs-you plus overflow) and re-bands them:
    top five by the shared key, the rest grouped per matter with counts
    recomputed. Membership of the firing set, ACK codes and appends do not
    change; only which band an item renders in does.

    Consequence, stated: the bands a recipient is SENT no longer equal the
    seat-wide projection that ``blind_wake.plan_counts`` fingerprints as
    ``digest_sha256`` / ``digest_needs_you`` / ``digest_admin_total``. That
    fingerprint describes what the gate projected for the seat, not any one
    email; the per-send body hashes on the envelope are the sent record."""
    pool = list(sub.get("needs_you") or [])
    for group in (sub.get("admin_confirms") or {}).get("matters") or []:
        pool.extend(group.get("items") or [])
    needs_you, overflow = band_stable_items(pool)
    sub["needs_you"] = needs_you
    sub.pop("admin_confirms", None)
    if overflow:
        sub["admin_confirms"] = overflow
    return sub


def need_you_count(digest: dict) -> int:
    """What the subject counts: every item a person must act on by name,
    needs-you plus blanket-ack-only. A recipient whose only items are blanket
    ones must never read "0 need you"."""
    return len(digest.get("needs_you") or []) + len(digest.get("blanket_ack_only") or [])


# ---------------------------------------------------------------------------
# The numbered map (plain-word replies,
# docs/specs/operator/case-manager-deadline-work.md).
# ---------------------------------------------------------------------------


def matter_runs(items: list[dict]) -> list[list[dict]]:
    """Items grouped per matter, in order of each matter's first appearance."""
    runs: dict[object, list[dict]] = {}
    for item in items:
        runs.setdefault(item.get("matter_id"), []).append(item)
    return list(runs.values())


def number_firing(sub: dict, cap: int) -> dict:
    """A copy of ``sub`` whose firing items carry the number a reader answers with.

    The reader replies "got it on 1"; the overlay resolves 1 to the ledger rows
    whose ``n`` is 1 in that thread. So the number shown and the ``n`` on the
    row are the same value, assigned HERE, once, and copied by
    ``dispatch_envelope`` onto each append.

    Order is the firing order the appends are written in (``_firing_items``):
    each needs-you item gets its own number; each "Also open" matter group and
    each blanket matter group gets ONE number shared by every item in it, so
    answering the number quiets the whole group. Blanket items are reordered so
    each matter's items are contiguous, which is what lets the group render as
    one numbered line.

    ``cap`` is the append cap. A unit (an item, or a whole group) is numbered
    only if every row it covers fits under the cap; the first unit that does
    not fit ends the numbering, so no number is ever shown without a row behind
    it. Numbers are continuous from 1. The input is not mutated."""
    out = dict(sub)
    state = {"n": 0, "room": cap, "open": True}

    def claim(size: int) -> int | None:
        if not state["open"] or size > state["room"]:
            state["open"] = False
            return None
        state["n"] += 1
        state["room"] -= size
        return state["n"]

    def stamp(items: list[dict], n: int | None) -> list[dict]:
        return [{**item, "n": n} if n is not None else dict(item) for item in items]

    out["needs_you"] = [stamp([item], claim(1))[0] for item in sub.get("needs_you") or []]
    admin = sub.get("admin_confirms")
    if isinstance(admin, dict) and admin.get("matters"):
        groups = []
        for group in admin["matters"]:
            items = list(group.get("items") or [])
            n = claim(len(items))
            groups.append({**group, "items": stamp(items, n), **({"n": n} if n is not None else {})})
        out["admin_confirms"] = {**admin, "matters": groups}
    blanket = sub.get("blanket_ack_only")
    if blanket:
        out["blanket_ack_only"] = [item for run in matter_runs(blanket) for item in stamp(run, claim(len(run)))]
    return out
