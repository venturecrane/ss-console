"""File notes: plain, one per routine per matter, silent when unchanged.

WHY THIS EXISTS. Until 2026-09-29 every ``create_memo`` POSTed a new memo with
whatever markup the model wrote, so a routine that runs every weekday left a
new note on every matter every weekday, each one carrying ``>`` quote marks,
``**`` and ``#`` that Smokeball shows as literal characters. A paralegal
reading a matter saw the same finding forty times in forty dialects.

What this module does, in the connector, so no skill has to remember it:

* ``normalize_memo_text`` is the one place markup leaves a note: heading marks,
  quote marks, ``**`` and backticks are stripped; bullets become plain lines; an
  ordered run is renumbered 1, 2, 3. A pipe table is REFUSED with the remedy
  (a table belongs in a Word document), because a table flattened into a note
  is unreadable and there is no faithful plain rendering of one.
* A note that opens with the header ``[Operator] <Routine name> as of <day>``
  is a ROUTINE note. ``upsert_memo`` finds that routine's latest note on the
  matter and updates it in place (``PUT /matters/{id}/memos/{memoId}``, proven
  live 2026-09-29, vfy_01M3Q1AKEYT45KY6FTGK7GPNTM: 202, the read-back lags a few
  seconds). When nothing changed, only the header's "as of" day moves, so a
  paralegal can tell "checked today, nothing new" from "not checked", and a
  day that stops advancing is itself the broken-routine signal.
* A note with no header is written exactly as before: a new memo, append-only.

The PUT is a full replace, so the superseded body would be lost; its first line
survives as a bounded ``Previously (<day>): ...`` tail (three entries, oldest
dropped) with any internal id removed from it, and every machine-marker line is
carried forward (see below).
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any

from .memo_confirm import _norm, _readback, _with, post_and_confirm
from .render import _number_ordered_runs, _table_row
from .task_update import PROVENANCE_MARK

#: Machine-marker lines: state some skills store in their own notes and a later
#: run reads back (service-confirmation-watcher's ``fileId <id> recorded``,
#: matter-memo-on-update's ``op-mmou:<matterId>:<ticks>`` change key, and
#: medical-chronology-maintainer's covered-set line). A marker line is never
#: normalized, never compared, never counted as note content, and is carried
#: forward on every update, deduplicated.
#:
#: DEBT: markers in a note are state the firm can read and edit. They belong in
#: a ledger (the casework ledger is the template); until they move, this list
#: is the contract between those skills' output formats and the upsert.
MEMO_MACHINE_LINES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\(?fileId\s+[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\s+recorded\)?\.?$"),
    re.compile(r"^op-mmou:[^\s:]+:\d+$"),
    re.compile(r"^Package job: \S.*; covered document ids: \S.*$"),
    re.compile(r"^facts [0-9a-f]{12}$"),
)

#: The facts-digest line (2026-09-29). The pre-run gate hashes the facts a
#: routine reports for a matter (its calendar entries and open tasks) and hands
#: the model ``facts_digest`` per matter; the model copies it as the note's last
#: line. Two runs over identical facts carry the same line whatever their
#: wording, so the note stays unchanged when only the prose varies. Unlike the
#: other markers it is REPLACED, never carried forward: a note holds one digest.
_FACTS_RE = re.compile(r"^facts [0-9a-f]{12}$")

#: A marker the service watcher writes INSIDE a sentence ("... (fileId <id>
#: recorded)."). When that sentence is superseded, the marker is carried
#: forward as a line of its own, so the watcher's dedup still finds it.
_EMBEDDED_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bfileId\s+[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\s+recorded"),
)

TABLE_REFUSAL = "a table belongs in a Word document: call render_docx_draft and name the file in the note"

#: A day as a header carries it: ISO, or the firm's written form ("Sep 29, 2026").
_DAY = r"\d{4}-\d{2}-\d{2}|[A-Z][a-z]{2,8}\.? \d{1,2}, \d{4}"
_HEADER_RE = re.compile(rf"^(?P<routine>\S.*?) as of (?P<day>{_DAY})\s*$")
_PREVIOUSLY_RE = re.compile(r"^Previously \((?P<day>[^)]{1,40})\): ")
_MAX_PREVIOUSLY = 3

#: An internal identifier a person should never read: a GUID, a long hex or
#: ULID token, or a short hex handle after a record noun ("file 788e0854").
#: The output gate refuses these in the note the model writes; the Previously
#: tail is composed HERE, after that gate, from the superseded body, so a line
#: written before the gate existed would carry its id forward for three more
#: changes. The tail is history for a person: the id leaves, the sentence stays.
_INTERNAL_ID = (
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|\b[0-9a-f]{32,64}\b"
    r"|\b[0-9A-HJKMNP-TV-Z]{26}\b"
    r"|\b(?:event|task|file|memo|document|doc|job|matter|id|facts)\s+(?=[0-9a-f]{0,11}[a-f])[0-9a-f]{8,12}\b(?!-)"
)
#: The short handle needs a hex LETTER: "file 84930211" is a firm's own number
#: and stays; "file 788e0854" is ours and goes.
_ID_RE = re.compile(_INTERNAL_ID)
_ID_PAREN_RE = re.compile(rf"\s*\([^()]*(?:{_INTERNAL_ID})[^()]*\)")

_HEADING_RE = re.compile(r"^#{1,6}\s+")
_QUOTE_RE = re.compile(r"^(?:>\s?)+")
_BULLET_RE = re.compile(r"^[-*]\s+")
_BLANK_RUN = 3

#: Memo listing page size; the listing is paged past it.
_PAGE = 100
_MAX_PAGES = 20

#: Read-back after a PUT: the 202 applies asynchronously (seconds).
_PUT_ATTEMPTS = 5
_PUT_BACKOFF = (1, 2, 4, 6)


class MemoRefused(ValueError):
    """A note the connector will not write, with the remedy in the message."""


# ---- normalization -----------------------------------------------------------
def is_marker(line: str) -> bool:
    return any(p.fullmatch(line.strip()) for p in MEMO_MACHINE_LINES)


def _plain_line(line: str) -> str:
    text = line.strip()
    stamp = ""
    if text.startswith(PROVENANCE_MARK):
        stamp, text = PROVENANCE_MARK + " ", text[len(PROVENANCE_MARK) :].strip()
    text = _QUOTE_RE.sub("", text)
    text = _HEADING_RE.sub("", text)
    text = _BULLET_RE.sub("", text)
    text = text.replace("**", "").replace("`", "")
    return (stamp + text.strip()).strip()


def _is_table_line(line: str) -> bool:
    row = _table_row(line.strip())
    return row is not None and len(row) >= 2


def _collapse_blanks(lines: list[str]) -> list[str]:
    out: list[str] = []
    blanks = 0
    for line in lines:
        blanks = blanks + 1 if not line else 0
        if not line and blanks >= _BLANK_RUN:
            continue
        out.append(line)
    while out and not out[0]:
        out.pop(0)
    while out and not out[-1]:
        out.pop()
    return out


def _join_bare_stamp(lines: list[str]) -> list[str]:
    """``[Operator]`` alone on the first line joins the next non-empty line, so
    the header is always one line (the one a later run matches). Never onto a
    marker line: ``[Operator] op-mmou:...`` would be neither a marker nor a
    header, and the dedup key would be lost."""
    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first is None or lines[first].strip() != PROVENANCE_MARK:
        return lines
    rest = next((i for i in range(first + 1, len(lines)) if lines[i].strip()), None)
    if rest is None or is_marker(lines[rest]):
        return lines
    return [*lines[:first], f"{PROVENANCE_MARK} {lines[rest].strip()}", *lines[rest + 1 :]]


def normalize_memo_text(text: str) -> str:
    """The note as plain lines. Raises ``MemoRefused`` on a pipe table.

    Marker lines (``MEMO_MACHINE_LINES``) pass through byte for byte."""
    if not isinstance(text, str):
        return text
    raw = _join_bare_stamp(text.splitlines())
    for line in raw:
        if not is_marker(line) and _is_table_line(line):
            raise MemoRefused(TABLE_REFUSAL)
    plain = [line if is_marker(line) else _plain_line(line) for line in raw]
    numbered = _number_ordered_runs(plain)
    restored = [raw[i] if is_marker(raw[i]) else numbered[i] for i in range(len(raw))]
    return "\n".join(_collapse_blanks(restored))


# ---- the header -------------------------------------------------------------
def _routine_key(routine: str) -> str:
    return " ".join(routine.split()).casefold()


def memo_header(text: Any) -> tuple[str, str] | None:
    """``(routine, day)`` from ``[Operator] <Routine> as of <day>``, else None.

    The header is the first non-empty line after the ``[Operator]`` stamp; a
    note without the stamp has no header, whatever its first line says."""
    if not isinstance(text, str):
        return None
    body = text.lstrip()
    if not body.startswith(PROVENANCE_MARK):
        return None
    for line in body[len(PROVENANCE_MARK) :].splitlines():
        if line.strip():
            match = _HEADER_RE.match(line.strip())
            return (match.group("routine"), match.group("day")) if match else None
    return None


def _stamped(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        return text
    return text if text.lstrip().startswith(PROVENANCE_MARK) else f"{PROVENANCE_MARK} {text}"


class _Parts:
    """A routine note split into header, content, markers and Previously tail."""

    def __init__(self, body: str) -> None:
        lines = body.strip().splitlines()
        self.header = lines[0].strip() if lines else ""
        head = memo_header(body)
        self.day = head[1] if head else ""
        self.content: list[str] = []
        self.markers: list[str] = []
        self.previously: list[str] = []
        for line in lines[1:]:
            if is_marker(line):
                self.markers.append(line.strip())
            elif _PREVIOUSLY_RE.match(line.strip()):
                self.previously.append(line.strip())
            else:
                self.content.append(line.rstrip())
        self.content = _collapse_blanks(self.content)

    def projection(self) -> str:
        return _norm("\n".join(self.content))

    def first_line(self) -> str:
        return next((line.strip() for line in self.content if line.strip()), "")

    def embedded(self) -> list[str]:
        return [m.group(0) for line in self.content for p in _EMBEDDED_MARKERS for m in p.finditer(line)]


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _facts_line(parts: _Parts) -> str | None:
    """The note's ``facts <digest>`` line, or None. The last one wins."""
    found = [m for m in parts.markers if _FACTS_RE.fullmatch(m)]
    return found[-1] if found else None


def _carried_markers(old: _Parts, new: _Parts, facts: str | None) -> list[str]:
    """Every marker from both notes, once; an old sentence-embedded marker the
    new content no longer holds becomes a line of its own. A facts line is never
    carried: ``facts`` (or nothing) takes its place, last."""
    new_text = "\n".join(new.content)
    embedded = [m for m in old.embedded() if m not in new_text]
    kept = [m for m in _dedupe([*old.markers, *embedded, *new.markers]) if not _FACTS_RE.fullmatch(m)]
    return [*kept, facts] if facts else kept


def _without_ids(line: str) -> str:
    """The line with every internal id gone: a parenthetical holding one goes
    whole ("(file 788e0854, 12 pages)"), a bare id goes on its own."""
    text = _ID_PAREN_RE.sub("", line)
    text = _ID_RE.sub("", text)
    text = re.sub(r"\s+([,.;:])", r"\1", re.sub(r"[ \t]{2,}", " ", text))
    return text.strip()


def _tail(previously: list[str]) -> list[str]:
    """The Previously tail as a person reads it: bounded, no internal ids."""
    return [_without_ids(line) for line in previously[:_MAX_PREVIOUSLY]]


def _compose(header: str, content: list[str], markers: list[str], previously: list[str]) -> str:
    return "\n".join([header, *content, *markers, *_tail(previously)]).strip()


# ---- reads ------------------------------------------------------------------
def _items(resp: Any) -> list:
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict) and isinstance(resp.get("value"), list):
        return resp["value"]
    return []


def _instant(memo: dict, key: str) -> datetime | None:
    raw = memo.get(key)
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def memo_last_touched(memo: dict) -> datetime:
    """``max(createdDate, lastUpdated)``: a note updated in place is as new as
    its last update, not its creation."""
    stamps = [s for s in (_instant(memo, "createdDate"), _instant(memo, "lastUpdated")) if s]
    return max(stamps) if stamps else datetime.min.replace(tzinfo=timezone.utc)


def _list_memos(client: Any, matter_id: str) -> list:
    memos: list = []
    for page in range(_MAX_PAGES):
        batch = _items(client.get(f"/matters/{matter_id}/memos", Limit=_PAGE, Offset=page * _PAGE))
        memos.extend(batch)
        if len(batch) < _PAGE:
            break
    return memos


def find_latest_routine_memo(client: Any, matter_id: str, routine: str) -> dict | None:
    """The newest live Operator note on the matter for ``routine``, or None.

    Matched on the ``[Operator]`` stamp AND the header's routine, newest by
    ``memo_last_touched``. Not matched on ``createdByUserId``: Smokeball has no
    endpoint that names the calling user, and every Operator write is authored
    as the consenting human (``server._stamp``), so the user id would match
    that person's own hand-typed notes too and would break the chain the day
    the firm re-consents as someone else. The stamp plus the header is what
    only the Operator writes."""
    want = _routine_key(routine)
    best: dict | None = None
    for memo in _list_memos(client, matter_id):
        if not isinstance(memo, dict) or memo.get("isDeleted") is True or not memo.get("id"):
            continue
        head = memo_header(memo.get("plainText"))
        if head is None or _routine_key(head[0]) != want:
            continue
        if best is None or memo_last_touched(memo) > memo_last_touched(best):
            best = memo
    return best


# ---- slimming (moved from server.py 2026-09-29, module-size ratchet) --------
# Lean lossless representation (context-cost fix): Smokeball returns BOTH an RTF
# `text` rendering AND a `plainText` rendering of every memo, the same content
# twice, with the RTF markup adding ~half the payload and nothing the agent needs
# (it reads plainText). get_memos_on_matter is the seat's single biggest retained
# tool-result (a full memo list is ~20k tokens and is re-read many times a
# session), so dropping the redundant rendering is a large, LOSSLESS per-turn
# context reduction. This is instance #1 of the general connector convention:
# return the leanest lossless form, never a second copy of the same content.


def _slim_memo(memo: Any) -> Any:
    """Drop the redundant RTF ``text`` field when ``plainText`` carries the same
    content. LOSSLESS + fail-safe: keep ``text`` whenever ``plainText`` is
    absent/empty, so a memo can never lose its only body."""
    if isinstance(memo, dict) and (memo.get("plainText") or "").strip() and "text" in memo:
        return {k: v for k, v in memo.items() if k != "text"}
    return memo


def _slim_memos(resp: Any) -> Any:
    """Apply :func:`_slim_memo` across a memos HATEOAS envelope (or bare list).
    Best-effort: an unexpected shape is returned untouched."""
    if isinstance(resp, dict) and isinstance(resp.get("value"), list):
        resp["value"] = [_slim_memo(m) for m in resp["value"]]
        return resp
    if isinstance(resp, list):
        return [_slim_memo(m) for m in resp]
    return resp


# ---- writes -----------------------------------------------------------------
def put_and_confirm(client: Any, matter_id: str, memo_id: str, body: str, *, sleep: Any = None) -> Any:
    """PUT ``body`` over memo ``memo_id``, then read it back by id.

    ``title`` is sent on every PUT (the vendor PUT is a full replace, and title
    is required); it is the note's first line. A PUT failure raises; after a
    successful PUT the result carries ``confirmed`` exactly as a create does."""
    title = next((line.strip() for line in body.splitlines() if line.strip()), "")
    resp = client.request("PUT", f"/matters/{matter_id}/memos/{memo_id}", json={"title": title, "text": body})
    base = resp if isinstance(resp, dict) and resp.get("id") else {**(resp or {}), "id": memo_id}
    confirmed, detail = _readback(
        client,
        matter_id,
        memo_id,
        body,
        sleep=sleep or time.sleep,
        attempts=_PUT_ATTEMPTS,
        backoff=_PUT_BACKOFF,
        retry_mismatch=True,
    )
    return _with(base, confirmed, detail)


def _post(client: Any, matter_id: str, body: str, sleep: Any) -> Any:
    return post_and_confirm(client, matter_id, body, sleep=sleep or time.sleep)


def upsert_memo(client: Any, matter_id: str, text: str, *, sleep: Any = None) -> Any:
    """Write a file note: one per routine per matter, silent when unchanged.

    * No header: a new memo, exactly as ``create_memo`` always wrote.
    * Header, no earlier note for the routine: a new memo, ``created: true``.
    * Header, same content as the routine's latest note, or both notes carry
      the same ``facts <digest>`` line whatever their prose says: that note's
      header moves to today's day (nothing else changes), ``unchanged: true``;
      when the day is already today, nothing is written at all.
    * Header, different content: the note is replaced in place with the new
      content, every marker line, and a ``Previously`` tail, ``updated: true``."""
    body = normalize_memo_text(_stamped(text))
    head = memo_header(body)
    if head is None:
        return _post(client, matter_id, body, sleep)
    latest = find_latest_routine_memo(client, matter_id, head[0])
    if latest is None:
        return {**_as_dict(_post(client, matter_id, body, sleep)), "created": True}
    return _replace(client, matter_id, latest, body, sleep)


def _as_dict(resp: Any) -> dict:
    return resp if isinstance(resp, dict) else {"response": resp}


def _replace(client: Any, matter_id: str, latest: dict, body: str, sleep: Any) -> Any:
    old, new = _Parts(latest.get("plainText") or ""), _Parts(body)
    memo_id = str(latest["id"])
    old_text = latest.get("plainText") or ""
    old_facts, new_facts = _facts_line(old), _facts_line(new)
    new_markers = [m for m in new.markers if m not in old_text and not _FACTS_RE.fullmatch(m)]
    # The same facts on both sides: unchanged, whatever the wording. The model
    # rewords identical facts from run to run; the digest does not move.
    same_facts = old_facts is not None and old_facts == new_facts
    same_prose = old.projection() == new.projection() and new_facts in (None, old_facts)
    if (same_facts or same_prose) and not new_markers:
        markers = _carried_markers(old, new, old_facts)
        if old.header == new.header:
            return {
                "id": memo_id,
                "confirmed": True,
                "unchanged": True,
                "confirm_detail": "already current; nothing written",
            }
        text = _compose(new.header, old.content, markers, old.previously)
        return {**_as_dict(put_and_confirm(client, matter_id, memo_id, text, sleep=sleep)), "unchanged": True}
    markers = _carried_markers(old, new, new_facts)
    previously = old.previously
    if old.projection() != new.projection() and old.first_line():
        previously = [f"Previously ({old.day}): {old.first_line()}", *old.previously]
    text = _compose(new.header, new.content, markers, previously[:_MAX_PREVIOUSLY])
    return {**_as_dict(put_and_confirm(client, matter_id, memo_id, text, sleep=sleep)), "updated": True}


# ---- MCP tools ------------------------------------------------------------
def update_memo(matter_id: str, memo_id: str, text: str) -> Any:
    """Replace the text of an existing memo on a matter; classified
    INTERNAL_WRITE. ``create_memo`` already updates a routine's note in place
    when the text opens with ``[Operator] <Routine name> as of <day>``; use this
    only to correct a particular memo whose id you hold.

    The text is written plain (heading marks, quote marks, ``**`` and backticks
    are removed; a table is refused: put it in a Word document with
    ``render_docx_draft`` and name the file in the note). Refuses if ``text``
    cites a matter number other than ``matter_id``'s own. The result carries
    ``confirmed`` exactly as ``create_memo``'s does."""
    from . import server
    from .digest_home import verify_unless_digest_home

    client = server._get_client()
    verify_unless_digest_home(server._verify_matter_reference, client, matter_id, text)
    return put_and_confirm(client, matter_id, memo_id, normalize_memo_text(_stamped(text)))


def register(server: Any) -> None:
    """Register ``update_memo``. Called once, from ``attachment_tools.register``."""
    server.tool()(update_memo)


__all__ = [
    "MEMO_MACHINE_LINES",
    "TABLE_REFUSAL",
    "MemoRefused",
    "find_latest_routine_memo",
    "is_marker",
    "memo_header",
    "memo_last_touched",
    "normalize_memo_text",
    "put_and_confirm",
    "register",
    "update_memo",
    "upsert_memo",
]
