"""The day's post, journaled: what combined post intake did with every letter
of a scanned bundle, and the numbered daily list a firm's office manager answers.

WHY (a client firm, 2026-10-29). The intake replied once per bundle, so a
day's post was several emails, each listing every letter. The office manager
asked for ONE email a day naming only what the Operator could not place, or
placed with low confidence, so she can answer it in one reply. The intake's
turn therefore records each bundle here, and the ``post-intake-daily-list``
skill's pre-run (a cron script, no model) reads the records once a day,
renders the list and has the seat send it out of turn to the one recipient
the firm authored. Nothing here sends anything.

WHAT A RECORD MAY SAY. One entry per letter, covering every page of the
bundle exactly once (the same partition the filing used), each with its kind
and, when it did not simply file, one ``issue`` from a closed vocabulary.
Whether a line is LISTED is decided here and in the pre-run from that
vocabulary, never by the model's wording:

* not placed: ``no_match``, ``one_candidate``, ``ambiguous``,
  ``search_failed``, ``filing_refused``;
* placed with low confidence (filed, but a keying step did not land or may
  be wrong): ``tab_not_written``, ``figure_not_saved``,
  ``possible_duplicate``, ``offer_not_written``, ``expense_not_staged``;
* a whole bundle nothing was filed from: ``unreadable``,
  ``partition_failed``, ``not_processed``.

A placed court paper is also listed ("needs calendaring"): the per-bundle
reply carried that flag at its top, and a summons nobody calendars is the one
consequence of silence this list must not create. A figure read from a scan is
NOT low confidence by itself: the row or expense already says so to whoever
relies on it, and on a firm whose post is all paper it would list every bill.

THE MODEL SUPPLIES THE LETTERS, CODE SUPPLIES WHAT HAPPENED TO THEM
(2026-11-04). Per letter the model gives ``first_page``, ``last_page``,
``sender`` (as printed), ``kind`` and an optional ``detail``. ``kind`` is the
one judgement the model still makes: what the paper IS, which decides which
keying act the letter needed. Where it was filed (``placed_on``), what kept it
from landing (``issue``) and the candidate matters (``candidates``) are
DERIVED in code from the outcome rows the resolve, filing and keying tools
wrote for this bundle and message (``outcome_ledger``, ``post_outcomes``). A
letter submitted with any of those three is refused, and the refusal names
what they derive to, so the model learns the fields are code's. A letter
nothing was recorded for is ``not_attempted`` and goes on the list.

WHILE THE SEAT OBSERVES (``logic_in_code.post_record_derivation``, absent is
``observe``). A letter that carries any of the three is recorded as typed, checked
exactly as it always was, and what code derives for it is written beside it: any
disagreement is an observation row (``differs``), and nothing the record would
have refused is refused. A letter that carries none is derived. ``enforce``
refuses the typed fields. So a skill that still types them behaves as it did the
day before this shipped, until the switch is thrown.

THE RECORD IS ALSO CHECKED AGAINST WHAT THIS PROCESS FILED. A letter derived
as filed must be a page range ``letter_pages.FILED_PAGES`` holds for that
bundle, and a letter derived as not placed must not be. After a restart the
in-process ledger has forgotten the bundle (the outcome rows on disk have
not); the record is kept and marked ``filing_unverified`` rather than refused,
because the list matters more than the check.

WHERE IT LIVES. ``$SMD_POST_JOURNAL_DIR`` (default
``/opt/data/post-intake-journal``): ``batches/`` one 0600 JSON file per
bundle, ``lists/`` the numbered lists the pre-run sent, ``state.json`` the
pre-run's cursor. It holds page ranges, senders as printed, matter numbers
and message ids, never page text, and keeps ``RETAIN_DAYS`` of each.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import logic_in_code, outcome_ledger, post_outcomes
from .letter_pages import FILED_PAGES
from .library import CUSTOMER_YAML_ENV, DEFAULT_CUSTOMER_YAML
from . import seat_config

JOURNAL_DIR_ENV = "SMD_POST_JOURNAL_DIR"
DEFAULT_JOURNAL_DIR = "/opt/data/post-intake-journal"
DAILY_LIST_SKILL = "post-intake-daily-list"
RETAIN_DAYS = 45
SCHEMA_VERSION = 1

KINDS = frozenset({"letter", "court_paper", "medical_bill", "vendor_bill", "settlement_offer"})
NOT_PLACED = frozenset({"no_match", "one_candidate", "ambiguous", "search_failed", "filing_refused", "not_attempted"})
PLACED_CHECK = frozenset(
    {"tab_not_written", "figure_not_saved", "possible_duplicate", "offer_not_written", "expense_not_staged"}
)
BUNDLE_ISSUES = frozenset({"unreadable", "partition_failed", "not_processed"})
#: What the model gives per letter. The three derived fields are code's.
_LETTER_KEYS = frozenset({"first_page", "last_page", "sender", "kind", "detail"})
_DERIVED_KEYS = ("placed_on", "issue", "candidates")
#: What a typed ``issue`` may say: ``not_attempted`` is only ever derived.
_TYPED_ISSUES = (NOT_PLACED - {"not_attempted"}) | PLACED_CHECK
_MAX_LETTERS = 200
_MAX_TEXT = 200
_MAX_SENDER = 120
_MAX_NUMBER = 40
_MAX_CANDIDATES = 10
_LIST_REF = re.compile(r"^\d{4}-\d{2}-\d{2}(?:-\d{1,2})?$")

NOW: Callable[[], datetime] = lambda: datetime.now(timezone.utc)  # noqa: E731 - a test swaps the clock


def journal_dir() -> Path:
    return Path(os.environ.get(JOURNAL_DIR_ENV) or DEFAULT_JOURNAL_DIR)


def _refused(reason: str) -> dict[str, Any]:
    return {"status": "refused", "recorded": False, "reason": reason}


def _text(value: Any, limit: int) -> str:
    """One line of plain text: whitespace collapsed, no em or en dash."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.replace("—", ",").replace("–", "-").split())[:limit]


def daily_list_authored(path: str | None = None) -> bool:
    """Whether the seat authors the daily list: the skill enabled with a
    ``settings.recipient`` AND a cron row for it on the same persona. Anything
    else (absent, unreadable) is False, and the intake replies per bundle as
    it always did (ADR 0037: unauthored is fail-closed, never a default)."""
    try:
        with open(path or os.environ.get(CUSTOMER_YAML_ENV) or DEFAULT_CUSTOMER_YAML, encoding="utf-8") as fh:
            doc = seat_config.parse(fh) or {}
    except Exception:  # noqa: BLE001 - an unreadable config authors nothing
        return False
    for persona in doc.get("personas") or [] if isinstance(doc, dict) else []:
        if not isinstance(persona, dict):
            continue
        armed = any(isinstance(c, dict) and c.get("skill") == DAILY_LIST_SKILL for c in persona.get("cron") or [])
        for skill in persona.get("skills") or []:
            if not isinstance(skill, dict) or skill.get("name") != DAILY_LIST_SKILL:
                continue
            settings = skill.get("settings") if isinstance(skill.get("settings"), dict) else {}
            recipient = settings.get("recipient")
            if armed and skill.get("enabled") is True and isinstance(recipient, str) and "@" in recipient:
                return True
    return False


def _letter(raw: Any, enforcing: bool) -> tuple[dict[str, Any] | None, str | None]:
    """One validated letter as the model gave it, or the reason it is
    refused. The derived fields are checked by the caller, which can name
    what they derive to."""
    if not isinstance(raw, dict) or set(raw) - _LETTER_KEYS - set(_DERIVED_KEYS):
        return (
            None,
            f"each letter carries only {sorted(_LETTER_KEYS if enforcing else _LETTER_KEYS | set(_DERIVED_KEYS))}",
        )
    first, last = raw.get("first_page"), raw.get("last_page")
    if not isinstance(first, int) or not isinstance(last, int) or isinstance(first, bool) or first < 1 or last < first:
        return None, "first_page and last_page are the letter's [p.N] bounds, first <= last"
    sender, kind = _text(raw.get("sender"), _MAX_SENDER), raw.get("kind") or "letter"
    if not sender:
        return None, "sender is who the letter is from, as it prints it"
    if kind not in KINDS:
        return None, f"kind is one of {sorted(KINDS)}"
    entry = {
        "first_page": first,
        "last_page": last,
        "sender": sender,
        "kind": kind,
        "detail": _text(raw.get("detail"), _MAX_TEXT),
    }
    return entry, None


def _typed_letter(raw: dict[str, Any], entry: dict[str, Any]) -> str | None:
    """Fill ``entry`` from the fields the model typed, or the reason it is
    refused. The checks a typed letter has always had, unchanged."""
    first, last = entry["first_page"], entry["last_page"]
    issue, placed_on = raw.get("issue") or "", _text(raw.get("placed_on"), _MAX_NUMBER)
    candidates = raw.get("candidates") or []
    if not isinstance(candidates, list) or len(candidates) > _MAX_CANDIDATES:
        return f"candidates is a list of at most {_MAX_CANDIDATES} matter numbers"
    candidates = [_text(c, _MAX_NUMBER) for c in candidates if _text(c, _MAX_NUMBER)]
    if issue and issue not in _TYPED_ISSUES:
        return f"issue is one of {sorted(_TYPED_ISSUES)}, or empty for a letter that simply filed"
    where = f"pages {first}-{last}"
    if issue in NOT_PLACED:
        if placed_on:
            return f"{where}: a letter that was not placed has no placed_on"
        if issue == "one_candidate" and len(candidates) != 1:
            return f"{where}: one_candidate names exactly one candidate matter number"
        if issue == "ambiguous" and len(candidates) < 2:
            return f"{where}: ambiguous names every candidate matter number the resolve listed"
        if issue not in ("one_candidate", "ambiguous") and candidates:
            return f"{where}: only one_candidate and ambiguous carry candidates"
    else:
        if not placed_on:
            return f"{where}: a filed letter carries placed_on, the matter number it was filed on"
        if candidates:
            return f"{where}: a filed letter carries no candidates"
    entry.update(placed_on=placed_on, issue=issue, candidates=candidates)
    return None


def _derivation_problem(entry: dict[str, Any], rows: list[dict[str, Any]]) -> str | None:
    first, last = entry["first_page"], entry["last_page"]
    return post_outcomes.span_problem(first, last, rows) or post_outcomes.kind_problem(entry["kind"], first, last, rows)


def _fill_derived(entry: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Fill ``entry``'s ``placed_on``, ``issue`` and ``candidates`` from the
    outcome rows."""
    found = post_outcomes.derive(entry["kind"], entry["first_page"], entry["last_page"], rows)
    detail = found["detail"]
    if detail == post_outcomes.NO_ATTEMPT and entry["detail"]:
        detail = f"{detail}; {entry['detail']}"
    entry.update(
        placed_on=_text(found["placed_on"], _MAX_NUMBER),
        issue=found["issue"],
        candidates=[_text(c, _MAX_NUMBER) for c in found["candidates"]][:_MAX_CANDIDATES],
        detail=_text(detail or entry["detail"], _MAX_TEXT),
    )


def _is_typed(raw: dict[str, Any]) -> bool:
    return any(key in raw for key in _DERIVED_KEYS)


def _observe(entry: dict[str, Any], status: str, reason: str, digest: str, message: str) -> None:
    outcome_ledger.observe(
        logic_in_code.POST_RECORD_DERIVATION,
        "record_post_batch",
        status,
        reason,
        sha256=digest,
        message_id=message,
        first_page=entry["first_page"],
        last_page=entry["last_page"],
    )


def _note_difference(entry: dict[str, Any], rows: list[dict[str, Any]], digest: str, message: str) -> None:
    """Write what code would have recorded for a typed letter, when it differs.
    Rows written with no message id count too: a skill that still types these
    fields does not pass one to the filing."""
    derived = dict(entry)
    _fill_derived(derived, rows)
    changed = [
        f"typed {key} {entry[key]!r}, derived {derived[key]!r}"
        for key in _DERIVED_KEYS
        if (sorted(entry[key]) if key == "candidates" else entry[key])
        != (sorted(derived[key]) if key == "candidates" else derived[key])
    ]
    if changed:
        where = f"pages {entry['first_page']}-{entry['last_page']}: "
        _observe(entry, outcome_ledger.DIFFERS, where + "; ".join(changed), digest, message)


def _derive_observing(letters: list[Any], parsed: list[dict[str, Any]], digest: str, message: str) -> str | None:
    """Under ``observe``: a typed letter stands as typed and is compared; an
    untyped one is derived. Nothing here refuses except a typed letter's own
    long-standing checks."""
    keyed = outcome_ledger.outcomes(digest, message)
    wider = keyed + (outcome_ledger.outcomes(digest, "") if message else [])
    for raw, entry in zip(letters, parsed):
        typed = _is_typed(raw)
        problem = _derivation_problem(entry, wider if typed else keyed)
        if problem:
            _observe(entry, outcome_ledger.WOULD_REFUSE, problem, digest, message)
        if typed:
            _note_difference(entry, wider, digest, message)
        else:
            _fill_derived(entry, keyed)
    return None


def _derive_enforcing(letters: list[Any], parsed: list[dict[str, Any]], digest: str, message: str) -> str | None:
    """Under ``enforce``: every letter is derived, and a typed field is refused
    with what it derives to."""
    rows = outcome_ledger.outcomes(digest, message)
    for entry in parsed:
        problem = _derivation_problem(entry, rows)
        if problem:
            return problem
        _fill_derived(entry, rows)
    return _typed_refusal(letters, parsed)


def _recorded_letters(
    letters: list[Any], page_count: int, digest: str, message: str
) -> tuple[list[dict[str, Any]], bool, str | None]:
    """(the letters as recorded, filing_unverified, refusal). Validates what the
    model gave, checks the partition, derives each letter from the outcome rows
    (``post_record_derivation`` decides what happens to a typed field), and
    cross-checks the filed pages."""
    enforcing = logic_in_code.load_switch(logic_in_code.POST_RECORD_DERIVATION).enforcing
    parsed: list[dict[str, Any]] = []
    for raw in letters:
        entry, problem = _letter(raw, enforcing)
        if problem is None and not enforcing and _is_typed(raw):
            problem = _typed_letter(raw, entry)  # type: ignore[arg-type]
        if problem:
            return [], False, problem
        parsed.append(entry)  # type: ignore[arg-type]
    if not parsed or page_count < 1:
        return [], False, "record every letter, or a bundle_issue when nothing could be filed"
    problem = _partition_problem(parsed, page_count)
    if problem is None:
        derive = _derive_enforcing if enforcing else _derive_observing
        problem = derive(letters, parsed, digest, message)
    if problem is not None:
        return [], False, problem
    problem, unverified = _filing_problem(parsed, digest)
    return parsed, unverified, problem


def _typed_refusal(raw_letters: list[Any], parsed: list[dict[str, Any]]) -> str | None:
    """A letter that carried ``placed_on``, ``issue`` or ``candidates`` is
    refused, naming what code derived for it, so the next call leaves them out."""
    for raw, entry in zip(raw_letters, parsed):
        typed = [k for k in _DERIVED_KEYS if k in raw]
        if typed:
            derived = ", ".join(f"{k} {entry[k]!r}" for k in _DERIVED_KEYS)
            return (
                f"pages {entry['first_page']}-{entry['last_page']}: {', '.join(typed)} are derived from what the "
                f"tools recorded, never passed; leave them out (code derives {derived})"
            )
    return None


def _partition_problem(letters: list[dict[str, Any]], page_count: int) -> str | None:
    expected = 1
    for entry in sorted(letters, key=lambda e: e["first_page"]):
        if entry["first_page"] != expected:
            return f"the letters must cover pages 1-{page_count} exactly once; page {expected} is missing or repeated"
        expected = entry["last_page"] + 1
    if expected != page_count + 1:
        return f"the letters must cover pages 1-{page_count} exactly once; they end at page {expected - 1}"
    return None


def _filing_problem(letters: list[dict[str, Any]], sha256: str) -> tuple[str | None, bool]:
    """(refusal, unverified). Placed letters must be filed pages; held ones must not."""
    filed = FILED_PAGES.pages(sha256)
    if filed is None:
        return None, any(not e["issue"] or e["issue"] in PLACED_CHECK for e in letters)
    for entry in letters:
        span = set(range(entry["first_page"], entry["last_page"] + 1))
        placed = entry["issue"] not in NOT_PLACED
        if placed and not span <= filed:
            return (
                f"pages {entry['first_page']}-{entry['last_page']} were not filed by this run; "
                "record them with the issue that kept them from filing"
            ), False
        if not placed and span & filed:
            return f"pages {entry['first_page']}-{entry['last_page']} were filed by this run; record where", False
    return None, False


def _write(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as fh:
        json.dump(record, fh, sort_keys=True)
    os.replace(tmp, path)


def _prune(directory: Path) -> None:
    cutoff = time.time() - RETAIN_DAYS * 86400
    for old in directory.glob("*.json"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            continue


def _listed(letters: list[dict[str, Any]]) -> int:
    return sum(1 for e in letters if e["issue"] or e["kind"] == "court_paper")


def record_post_batch(
    message_id: str,
    file_name: str,
    sha256: str,
    page_count: int,
    letters: list[dict[str, Any]],
    received_at: str = "",
    bundle_issue: str = "",
    bundle_detail: str = "",
) -> Any:
    """Record what this run did with one scanned bundle, after every letter is
    filed, held or keyed and BEFORE any reply. Classified INTERNAL_WRITE: the
    seat's own journal; nothing reaches the firm's record or anyone's inbox.

    ``message_id`` is the email the bundle came in on, ``file_name`` and
    ``sha256`` the attachment as the read returned them, ``page_count`` its
    pages, ``received_at`` when the email arrived (ISO 8601, from the event).
    ``letters`` has one entry per letter, covering every page exactly once:
    ``first_page``, ``last_page``, ``sender`` (as the letter prints it),
    ``kind`` (``letter``, ``court_paper``, ``medical_bill``, ``vendor_bill``
    or ``settlement_offer``), ``placed_on`` (the matter number it was filed
    on, from that letter's own resolve), ``issue`` (empty when it simply
    filed and everything after it landed; otherwise one of the words the
    skill lists), ``candidates`` (the matter numbers the resolve named, for
    ``one_candidate`` and ``ambiguous`` only) and ``detail`` (the plain
    reason, one short line, when the issue needs one; never what the letter
    says).

    When nothing in the bundle was filed at all (it could not be read, its
    letters could not be told apart, or it is a second PDF that was not
    processed), pass ``letters`` empty and ``bundle_issue`` (``unreadable``,
    ``partition_failed``, ``not_processed``) with ``bundle_detail``.

    Returns ``status``: ``recorded`` (``to_list`` lines will be on the daily
    list; ``daily_list`` true when the firm gets one, which means NO reply to
    this bundle) or ``refused`` (``reason``; nothing recorded: correct it and
    call again)."""
    message, name, digest = _text(message_id, 300), _text(file_name, 200), _text(sha256, 64).lower()
    if not message or not name or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return _refused("message_id, file_name and the read's sha256 are required")
    if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 0:
        return _refused("page_count is the bundle's page count")
    if not isinstance(letters, list) or len(letters) > _MAX_LETTERS:
        return _refused(f"letters is a list of at most {_MAX_LETTERS} entries")
    unverified = False
    if bundle_issue:
        if bundle_issue not in BUNDLE_ISSUES:
            return _refused(f"bundle_issue is one of {sorted(BUNDLE_ISSUES)}")
        if letters:
            return _refused("a bundle nothing was filed from carries no letters")
        parsed: list[dict[str, Any]] = []
    else:
        parsed, unverified, problem = _recorded_letters(letters, page_count, digest, message)
        if problem:
            return _refused(problem)
    now = NOW().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    key = hashlib.sha256(f"{message}|{digest}|{bundle_issue}".encode()).hexdigest()[:24]
    path = journal_dir() / "batches" / f"{key}.json"
    recorded_at = now
    try:
        if path.is_file():
            recorded_at = json.loads(path.read_text("utf-8")).get("recorded_at") or now
    except Exception:  # noqa: BLE001 - an unreadable earlier record is replaced, stamped now
        recorded_at = now
    record = {
        "version": SCHEMA_VERSION,
        "recorded_at": recorded_at,
        "received_at": _text(received_at, 40),
        "message_id": message,
        "file_name": name,
        "sha256": digest,
        "page_count": page_count,
        "letters": sorted(parsed, key=lambda e: e["first_page"]),
        "bundle_issue": bundle_issue,
        "bundle_detail": _text(bundle_detail, _MAX_TEXT),
        "filing_unverified": unverified,
    }
    try:
        _write(path, record)
        _prune(path.parent)
    except OSError as exc:
        return _refused(f"the journal could not be written ({exc.__class__.__name__}); nothing recorded")
    result = {
        "status": "recorded",
        "recorded": True,
        "letters": len(parsed),
        "to_list": 1 if bundle_issue else _listed(parsed),
        "daily_list": daily_list_authored(),
        "filing_unverified": unverified,
    }
    if any(not _is_typed(raw) for raw in letters):
        result["letters_recorded"] = [
            {k: e[k] for k in ("first_page", "last_page", "kind", "placed_on", "issue", "candidates")}
            for e in record["letters"]
        ]
    return result


def get_post_list(list_ref: str) -> Any:
    """The numbered lines of one daily post list, by the reference its subject
    carries ("Post list 2026-10-29", or "2026-10-29-2" for a second list that
    day). Classified READ. Use it when someone replies to a daily list: line N
    gives the email the bundle came in on (``message_id``), the attachment
    (``file_name``, ``sha256``), the letter's pages, and the candidates the
    list named. The matter number still comes ONLY from the reply.

    Returns ``found`` with ``items``, ``not_found``, or ``refused``."""
    ref = _text(list_ref, 20)
    if not _LIST_REF.match(ref):
        return _refused('list_ref is the date in the list\'s subject, e.g. "2026-10-29"')
    path = journal_dir() / "lists" / f"{ref}.json"
    try:
        doc = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return {"status": "not_found", "list_ref": ref}
    except Exception as exc:  # noqa: BLE001 - an unreadable list is reported, never read as empty
        return _refused(f"the list could not be read ({exc.__class__.__name__})")
    items = doc.get("items") if isinstance(doc, dict) else None
    return {"status": "found", "list_ref": ref, "items": items if isinstance(items, list) else []}


def register(server: Any) -> None:
    server.tool()(record_post_batch)
    server.tool()(get_post_list)


__all__ = [
    "BUNDLE_ISSUES",
    "DAILY_LIST_SKILL",
    "JOURNAL_DIR_ENV",
    "KINDS",
    "NOT_PLACED",
    "PLACED_CHECK",
    "daily_list_authored",
    "get_post_list",
    "journal_dir",
    "record_post_batch",
    "register",
]
