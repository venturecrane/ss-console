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
from datetime import date

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


def _before_caption(text: str) -> str:
    """``text`` cut at the first ``" - "`` segment that reads as a caption.

    Smokeball calendar titles are commonly "<what> - <caption> (<room>)", e.g.
    "Final Status Conference - Okafor v. Grand Valley Market (Dept 47)". The
    part before the caption says what the date is and is the firm's own words;
    the caption refuses (see ``_CAPTION_RE``). A caption in the FIRST segment
    leaves nothing, and the label is refused whole."""
    kept: list[str] = []
    for segment in text.split(" - "):
        if _CAPTION_RE.search(segment):
            break
        kept.append(segment)
    return " - ".join(kept).strip()


def display_label(subject: object) -> str | None:
    """A task subject or calendar title reduced to a label the send gate will
    pass, or None.

    None means "render no label": no subject, a caption in its first segment,
    a fabrication marker, or nothing left once the gate's shapes are masked.
    A caption in a LATER ``" - "`` segment is cut off with everything after it
    (``_before_caption``)."""
    if not isinstance(subject, str):
        return None
    text = " ".join(subject.split())
    if text.lower().startswith(_PROVENANCE_MARK):
        text = text[len(_PROVENANCE_MARK) :].strip()
    text = _before_caption(text)
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
            **{k: g[0].get(k) for k in ("matter_id", "matter_number", "matter_number_absent", "matter_name")},
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


def extract_task_review(sub: dict) -> dict:
    """Collapse one recipient's overdue tasks past the top five into a count.

    Only when the digest carries a ``task_review`` marker, which the pre_run
    sets only when the firm authored ``case_manager.task_cleanup``: those tasks
    are in the weekly task review, so the digest names the count in one line
    instead of an "Also open" band of them (case-manager spec, Job 1). Court
    dates and not-yet-overdue tasks past the top five stay in the band: the
    review covers overdue tasks and nothing else, so the line must not claim
    more. The collapsed items are not raised (no ``fired`` row): they are the
    review's. No marker, no change."""
    review = sub.get("task_review")
    if not isinstance(review, dict):
        return sub
    band = sub.get("admin_confirms") or {}
    items = [i for g in band.get("matters") or [] for i in g.get("items") or []]
    overdue = [i.get("label") == "task-deadline" and int(i.get("days_out") or 0) < 0 for i in items]
    moved = [i for i, hit in zip(items, overdue) if hit]
    rest = [i for i, hit in zip(items, overdue) if not hit]
    sub.pop("admin_confirms", None)
    if rest:
        sub["admin_confirms"] = group_by_matter(rest)
    sub["task_review"] = {**review, "count": len(moved)}
    return sub


def need_you_count(digest: dict) -> int:
    """What the subject counts: every item a person must act on by name,
    needs-you plus blanket-ack-only. A recipient whose only items are blanket
    ones must never read "0 need you"."""
    return len(digest.get("needs_you") or []) + len(digest.get("blanket_ack_only") or [])


# ---------------------------------------------------------------------------
# The words an item is named by (2026-09-28). The Captain's read of the pilot
# digest: "1. matter 2026-PI-105, court-date 2026-10-02 (due in 4 days) / a
# court date the firm authored" said neither which case nor what the date was,
# and its vocabulary was ours, not the firm's. Every word below is read from
# the record (the matter's title, the task subject, the calendar title) or is
# a fixed plain word; nothing is composed about the case.
# ---------------------------------------------------------------------------

#: A client surname as the matter title carries it: letters, then letters,
#: spaces, apostrophes, hyphens and periods ("O'Neil", "Cruz-Hernandez",
#: "St. John"). Anything else (a digit, a comma, a slash) is not a name.
_SURNAME_RE = re.compile(r"[A-Za-z][A-Za-z' .\-]{0,39}")

#: A matter number the overlay's reply parser recognizes on its own at the
#: start of a numbered line (``reply_items._DIGEST_ITEM_LINE``: ``N. matter``
#: or ``N. <four digits>-``). Any other number keeps the word "matter" in front
#: of it, so a quoted digest line is never read as the reader's answer.
_BARE_NUMBER_RE = re.compile(r"\d{4}-")

_WEEKDAY_WORDS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTH_WORDS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def matter_name(title: object, number: object) -> str | None:
    """The client's surname from the matter's own ``title``, or None.

    Smokeball titles read "<number> - <Surname>, <Given> - <type> - ...". The
    first segment must BE the matter's number (a title in another layout is not
    guessed at), the second is the client and nothing else is. A multi-client
    segment, a caption, a marker, or anything that is not a plain surname gives
    None, and the line names the matter by number alone. Never a caption: the
    send gate refuses "Okafor v. Grand Valley" in a pre-rendered body, because
    nothing read inside the session registers it."""
    if not (isinstance(title, str) and isinstance(number, str) and number.strip()):
        return None
    segments = [s.strip() for s in " ".join(title.split()).split(" - ")]
    if len(segments) < 2 or segments[0] != number.strip():
        return None
    client = segments[1]
    if any(mark in client for mark in ("|", "&", ";", "/")) or " and " in client.lower():
        return None
    surname = client.split(",")[0].strip()
    separators = {"v", "v.", "vs", "vs.", "versus", "in", "re"}
    if not _SURNAME_RE.fullmatch(surname) or separators & set(surname.lower().split()):
        return None
    if _CAPTION_RE.search(surname) or _MARKER_RE.search(surname):
        return None
    return surname


def _day(iso: object) -> date | None:
    try:
        return date.fromisoformat(str(iso)[:10])
    except ValueError:
        return None


def day_words(iso: object, *, weekday: bool, year: bool) -> str:
    """ "Fri Oct 2, 2026" (or a shorter form) for an authored ISO day. The value
    is the authored date, only spelled out; an unparseable one renders as read.
    With the year, the send gate's per-line matter/date pairing still sees the
    date; without it (the subject line) there is no matter number beside it."""
    day = _day(iso)
    if day is None:
        return str(iso)
    words = f"{_MONTH_WORDS[day.month - 1]} {day.day}"
    if year:
        words += f", {day.year}"
    return f"{_WEEKDAY_WORDS[day.weekday()]} {words}" if weekday else words


def relative_words(days_out: int) -> str:
    if days_out < 0:
        return f"overdue {-days_out} day" + ("" if days_out == -1 else "s")
    if days_out == 0:
        return "today"
    if days_out == 1:
        return "tomorrow"
    return f"in {days_out} days"


def is_task(item: dict) -> bool:
    return item.get("label") == "task-deadline"


def _without_name_suffix(label: str, name: object) -> str:
    """``label`` less a trailing " - <name>" when ``name`` is the client surname
    the line already shows beside the matter number. Firms end task subjects
    with the client ("Send the preservation letter - Doe"); under "2026-PI-900
    Doe" the suffix only repeats the name (2026-09-28). A label that is
    nothing but the name keeps it."""
    if not (isinstance(name, str) and name):
        return label
    head, sep, tail = label.rpartition(" - ")
    if sep and head.strip() and tail.strip().lower() == name.strip().lower():
        return head.rstrip()
    return label


def what_words(item: dict) -> str:
    """What the item is, in the record's words: the task subject or calendar
    title (masked by ``display_label``), else a plain noun. A trailing
    " - <surname>" the matter head already names is dropped."""
    label = item.get("subject_display")
    if isinstance(label, str) and label:
        return _without_name_suffix(label, item.get("matter_name"))
    return "a task" if is_task(item) else "a calendar date"


def when_words(item: dict) -> str:
    """ "due Jul 8, 2026 (overdue 82 days)" for a task, "Fri Oct 2, 2026 (in 4
    days)" for a calendar date."""
    days_out = int(item.get("days_out") or 0)
    if is_task(item):
        day = "due " + day_words(item.get("authored_date"), weekday=False, year=True)
    else:
        day = day_words(item.get("authored_date"), weekday=True, year=True)
    return f"{day} ({relative_words(days_out)})"


def head_words(unit: dict) -> str:
    """The matter an item or group is about: "2026-PI-105 Okafor", or the
    exact authored absence phrase. Never a GUID, never a supplied value (ss
    #2390). A number the reply parser would not recognize at the start of a
    line keeps the word "matter" in front of it."""
    number = unit.get("matter_number")
    if isinstance(number, str) and number:
        head = number if _BARE_NUMBER_RE.match(number) else f"matter {number}"
    elif unit.get("matter_number_absent") == "no_number_on_record":
        head = "matter with no number on record"
    else:
        head = "matter number unavailable"
    name = unit.get("matter_name")
    return f"{head} {name}" if isinstance(name, str) and name else head


def subject_line(digest: dict, today: object) -> str:
    """The subject: the one item by case and date, or a count and the day.

    "[Deadlines] Okafor: Final Status Conference Fri Oct 2", or "[Deadlines]
    2 tasks need you, Sep 28". The "[Deadlines]" prefix is what inbox routing
    reads (matter-inbox-router). Counts are ``need_you_count`` (Law 11)."""
    items = list(digest.get("needs_you") or []) + list(digest.get("blanket_ack_only") or [])
    today_iso = today if isinstance(today, str) else getattr(today, "isoformat", lambda: str(today))()
    if len(items) == 1:
        item = items[0]
        who = item.get("matter_name") or item.get("matter_number")
        if isinstance(who, str) and who:
            what = what_words(item)
            day = day_words(item.get("authored_date"), weekday=not is_task(item), year=False)
            if is_task(item):
                day = ("overdue since " if int(item.get("days_out") or 0) < 0 else "due ") + day
            return f"[Deadlines] {who}: {what} {day}"
    on = day_words(today_iso, weekday=False, year=False)
    if not items:
        return f"[Deadlines] No dates need you today, {on}"
    return f"[Deadlines] {_count_words(items)} you, {on}"


def _count_words(items: list[dict]) -> str:
    """ "2 tasks need", "1 task and 1 date need", "1 date needs": what the
    items ARE, counted. A task is not a date (2026-09-28: two overdue tasks
    went out as "2 dates need you")."""
    tasks = sum(1 for item in items if is_task(item))
    dates = len(items) - tasks
    parts = [f"{n} {noun}{'' if n == 1 else 's'}" for n, noun in ((tasks, "task"), (dates, "date")) if n]
    verb = "needs" if len(items) == 1 else "need"
    return " and ".join(parts) + " " + verb


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
