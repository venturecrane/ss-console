"""What a turn's tool calls actually did, so code writes the reply to a request
for work (2026-11-24, A&P).

WHY. A replay of every request the firm had sent found reply failures the
largest cause of a bad answer: 41 of 160, 32 of them in one week. On a turn
that DID something (filed a document, made a folder, copied files, queued a
job) the model wrote the reply, and its words were not the facts: it asked the
attorney a question, stated a limit of the product, said a filed document was
not filed, gave a false status, or told him to paste the text in himself. The
tool results held the truth the whole time.

WHAT THIS IS. :class:`TurnFacts` records each tool call's outcome per (tool,
target), fed from the reply plugin's ``post_tool_call`` (``relay_checks.note_turn``),
which sees every call. A target is a matter, a file name or a job, read from the
tool's result (its arguments only where the result does not name it). The act
tools and how to read each one's result are ONE table, :data:`ACT_TOOLS`: there
is no registry of acts anywhere else.

When a turn included an act, :func:`compose` writes the reply's body from the
record alone, and the model's draft is discarded (as ``job_reply`` already does
for a drafting job's delivery):

* the turn's required ``say`` blocks (the lanes' own sentences, step 5a);
* one line per file actually filed, placed or created this turn, so a turn that
  failed part-way still reports, truthfully, what it did (nothing is deleted);
* :data:`LOOK_INTO`, once, when any act or read failed and no later call
  succeeded against the same target. SMD is told through an owed row
  (``shared.smd_owed``, kind ``look_into``), and a thread gets that line once
  until SMD answers the earlier one.

A turn with no act is untouched here: the model's words go through every check
as before. Two narrow checks read those words (:func:`unfinished_ask`,
:func:`listing_contradiction`) and the relay holds the reply for a redraft.

A restart forgets the record, and the reply is then the model's, as every reply
was before this (the gateway-restart fallback). Never raises.
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from shared import result_unwrap
from shared.reply_could_not import LOOK_INTO

OK = "ok"
REFUSED = "refused"
FAILED = "failed"
OUT_OF_TIME = "out_of_time"
#: A lane that answered ``say: "none"``: nothing is said about it, nothing failed.
SILENT = "silent"
_NOT_OK = frozenset({REFUSED, FAILED, OUT_OF_TIME})

#: Act kinds.
FILE = "file"
FOLDER = "folder"
PLACE = "place"
SUBMIT = "submit"
#: Read kinds: a matter's file listing (the status answer reads it) and any other read.
LISTING = "listing"
READ = "read"

#: The act tools, and how each one's result is read. Lanes without a ``say``
#: (chronology, demand, litigation) are not here: their reply is not code's yet.
ACT_TOOLS: dict[str, str] = {
    "mcp_smokeball_add_file": FILE,
    "mcp_smokeball_render_docx_draft": FILE,
    "mcp_smokeball_render_docx_template": FILE,
    "mcp_smokeball_file_attachment_to_matter": FILE,
    "mcp_smokeball_create_folder": FOLDER,
    "mcp_smokeball_copy_files_to_folder": PLACE,
    "mcp_smokeball_move_files_to_folder": PLACE,
    "records_job_submit": SUBMIT,
    "drafting_job_submit": SUBMIT,
    "drafting_job_add_to_request": SUBMIT,
}
READ_TOOLS: dict[str, str] = {
    "mcp_smokeball_get_files_on_matter": LISTING,
    "mcp_smokeball_find_files_on_matter": LISTING,
    "mcp_smokeball_list_folders": READ,
    "mcp_smokeball_get_file": READ,
    "mcp_smokeball_read_document": READ,
}
#: The seat verticals whose replies to a work request code writes.
FIRM_VERTICALS = frozenset({"law-firm"})

_BAD_STATUSES = frozenset({"readback_mismatch", "link_not_visible", "error", "failed", "failure"})
_REFUSAL_STATUSES = frozenset({"refused", "revise", "blocked", "denied"})
_PLACED = {"copied": "copied", "already_copied": "copied", "moved": "moved"}


@dataclass(frozen=True)
class Entry:
    """One target's outcome from one call. ``line`` is code's sentence for a
    success that changed the matter, "" otherwise."""

    tool: str
    kind: str
    target: str
    outcome: str
    line: str = ""
    name: str = ""


@dataclass(frozen=True)
class Composed:
    """A reply code wrote: its body, the files it names (exempt from the
    checklist: the matter's own names), and whether it carries LOOK_INTO."""

    text: str
    names: tuple[str, ...] = ()
    look_into: bool = False
    failed: tuple[str, ...] = field(default=())


def _parsed(result: Any) -> dict[str, Any]:
    parsed = result_unwrap.parse_tool_result(result)
    return parsed if isinstance(parsed, dict) else {}


def call_failed(parsed: dict[str, Any], status: Any = None, error_type: Any = None) -> bool:
    """The hook or the result reports the call itself failed."""
    if isinstance(status, str) and status.strip().lower() in {"error", "failed", "failure"}:
        return True
    if isinstance(error_type, str) and error_type.strip().lower() not in {"", "none", "null"}:
        return True
    if parsed.get("error") or parsed.get("isError") is True or parsed.get("is_error") is True:
        return True
    return parsed.get("ok") is False or parsed.get("success") is False


def _status(parsed: dict[str, Any]) -> str:
    value = parsed.get("status")
    return value.strip().lower() if isinstance(value, str) else ""


def _file_name(tool: str, parsed: dict[str, Any], args: dict[str, Any]) -> str:
    name = str(parsed.get("fileName") or args.get("file_name") or "").strip()
    if name and tool == "mcp_smokeball_render_docx_draft" and not name.lower().endswith(".docx"):
        name += ".docx"
    return name


def _matter(parsed: dict[str, Any], args: dict[str, Any]) -> str:
    return str(parsed.get("matterId") or args.get("matter_id") or args.get("matter") or "").strip()


def _file_outcome(parsed: dict[str, Any]) -> str:
    status = _status(parsed)
    if status in ("readback_mismatch", "link_not_visible"):
        return FAILED
    if parsed.get("reason") == OUT_OF_TIME or parsed.get("outOfTime"):
        return OUT_OF_TIME
    if status in _REFUSAL_STATUSES:
        return REFUSED
    if parsed.get("fileId") and status not in _BAD_STATUSES:
        return OK
    return FAILED


def _file_entries(tool: str, parsed: dict[str, Any], args: dict[str, Any]) -> list[Entry]:
    name = _file_name(tool, parsed, args)
    outcome = _file_outcome(parsed)
    line = f"We filed {name} in the matter." if outcome == OK and name else ""
    return [Entry(tool, FILE, f"{_matter(parsed, args)}:{name}", outcome, line, name)]


def _folder_entries(tool: str, parsed: dict[str, Any], args: dict[str, Any]) -> list[Entry]:
    name = str(args.get("name") or parsed.get("name") or "").strip()
    status = _status(parsed)
    ok = status not in _BAD_STATUSES | _REFUSAL_STATUSES and parsed.get("readback") == "confirmed"
    outcome = OK if ok else (REFUSED if status in _REFUSAL_STATUSES else FAILED)
    line = f"We created the {name} folder." if ok and name else ""
    return [Entry(tool, FOLDER, f"{_matter(parsed, args)}:folder:{name}", outcome, line, name)]


def _place_row(tool: str, row: dict[str, Any], folder: str, where: str) -> Entry:
    name = str(row.get("name") or row.get("fileId") or "").strip()
    result = str(row.get("result") or "")
    bad = str(row.get("status") or "") in ("readback_mismatch", "link_not_visible")
    target = f"{where}:{row.get('fileId') or name}"
    if result == "already_in_folder" and not bad:
        return Entry(tool, PLACE, target, OK, f"{name} is in the {folder} folder.", name)
    verb = _PLACED.get(result)
    if verb and not bad:
        return Entry(tool, PLACE, target, OK, f"We {verb} {name} into the {folder} folder.", name)
    return Entry(tool, PLACE, target, FAILED)


def _place_entries(tool: str, parsed: dict[str, Any], args: dict[str, Any]) -> list[Entry]:
    rows = parsed.get("files")
    where = f"{_matter(parsed, args)}:{parsed.get('folderId') or args.get('folder_id') or ''}"
    if not isinstance(rows, list) or not rows:
        refused = _status(parsed) in _REFUSAL_STATUSES
        return [Entry(tool, PLACE, where, REFUSED if refused else FAILED)]
    folder = str(parsed.get("folderName") or "").strip() or "named"
    return [_place_row(tool, r, folder, where) for r in rows if isinstance(r, dict)]


def _submit_entries(tool: str, parsed: dict[str, Any], args: dict[str, Any]) -> list[Entry]:
    target = (
        f"{tool}:{args.get('matter') or args.get('matter_number') or parsed.get('job_id') or ''}"
    )
    say = parsed.get("say")
    if isinstance(say, str) and say.strip():
        # The lane wrote its own sentence (step 5a keeps it): code's words either way.
        return [Entry(tool, SUBMIT, target, SILENT if say.strip() == "none" else OK)]
    if parsed.get("accepted") is False or _status(parsed) in _REFUSAL_STATUSES:
        return [Entry(tool, SUBMIT, target, REFUSED)]
    return [Entry(tool, SUBMIT, target, OK if parsed.get("accepted") else FAILED)]


_READERS: dict[str, Callable[[str, dict[str, Any], dict[str, Any]], list[Entry]]] = {
    FILE: _file_entries,
    FOLDER: _folder_entries,
    PLACE: _place_entries,
    SUBMIT: _submit_entries,
}


def read_call(
    tool: str, args: Any, result: Any, status: Any = None, error_type: Any = None
) -> list[Entry]:
    """The entries one call wrote, ``[]`` for a tool neither table names."""
    kind = ACT_TOOLS.get(tool) or READ_TOOLS.get(tool)
    if kind is None:
        return []
    argd = args if isinstance(args, dict) else {}
    parsed = _parsed(result)
    if kind in (LISTING, READ):
        target = f"{_matter(parsed, argd)}:{argd.get('file_id') or ''}"
        return [
            Entry(tool, kind, target, FAILED if call_failed(parsed, status, error_type) else OK)
        ]
    if call_failed(parsed, status, error_type):
        target = (
            f"{_matter(parsed, argd)}:{_file_name(tool, parsed, argd) or argd.get('name') or ''}"
        )
        return [Entry(tool, kind, target, FAILED)]
    return _READERS[kind](tool, parsed, argd)


def listed_names(result: Any) -> tuple[str, ...]:
    """The file names a listing returned (``name`` plus ``fileExtension``)."""
    out: list[str] = []

    def walk(value: Any, depth: int) -> None:
        if depth > 4:
            return
        if isinstance(value, list):
            for item in value:
                walk(item, depth + 1)
        elif isinstance(value, dict):
            name = value.get("name")
            if isinstance(name, str) and name.strip() and ("id" in value or "fileId" in value):
                out.append(f"{name.strip()}{value.get('fileExtension') or ''}")
            for item in value.values():
                if isinstance(item, (list, dict)):
                    walk(item, depth + 1)

    walk(_parsed(result), 0)
    return tuple(dict.fromkeys(out))


class TurnFacts:
    """Per (session, email): every entry in call order, and the names each
    listing read returned. Bounded; a restart forgets it."""

    _MAX = 512

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: OrderedDict[tuple[str, str], list[Entry]] = OrderedDict()
        self._listed: dict[tuple[str, str], list[str]] = {}

    def note(
        self, session_id: str, message_id: str, entries: Iterable[Entry], names: Iterable[str] = ()
    ) -> None:
        key = (session_id, message_id)
        with self._lock:
            have = self._entries.pop(key, [])
            have.extend(entries)
            self._entries[key] = have
            listed = self._listed.setdefault(key, [])
            listed.extend(n for n in names if n not in listed)
            while len(self._entries) > self._MAX:
                old, _ = self._entries.popitem(last=False)
                self._listed.pop(old, None)

    def entries(self, session_id: str, message_id: str) -> tuple[Entry, ...]:
        with self._lock:
            return tuple(self._entries.get((session_id, message_id), ()))

    def listed(self, session_id: str, message_id: str) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._listed.get((session_id, message_id), ()))

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._listed.clear()


TURN_FACTS = TurnFacts()


def note_call(session_id: str, message_id: str, kwargs: dict[str, Any]) -> None:
    """Record one ``post_tool_call``. Never raises."""
    tool = str(kwargs.get("tool_name") or "")
    if not session_id or not message_id or not (tool in ACT_TOOLS or tool in READ_TOOLS):
        return
    entries = read_call(
        tool,
        kwargs.get("args"),
        kwargs.get("result"),
        kwargs.get("status"),
        kwargs.get("error_type"),
    )
    names = listed_names(kwargs.get("result")) if READ_TOOLS.get(tool) == LISTING else ()
    TURN_FACTS.note(session_id, message_id, entries, names)


def has_act(entries: Iterable[Entry]) -> bool:
    return any(e.kind in (FILE, FOLDER, PLACE, SUBMIT) for e in entries)


def unfinished(entries: Iterable[Entry]) -> tuple[str, ...]:
    """``tool outcome`` for each target that failed and never succeeded later
    (a retry of the same target that succeeds counts as success)."""
    done = {e.target for e in entries if e.outcome == OK}
    out: list[str] = []
    for e in entries:
        if e.outcome in _NOT_OK and e.target not in done:
            word = f"{e.tool} {e.outcome}"
            if word not in out:
                out.append(word)
    return tuple(out)


def compose(
    entries: tuple[Entry, ...], blocks: Iterable[str], *, allow_look_into: bool = True
) -> Composed | None:
    """The body code writes for a turn that included an act, or None (no act,
    or nothing to say: the model's reply goes through as before).

    ``blocks`` are the turn's required ``say`` blocks (step 5a's register, its
    ``FORBID`` entries left out). ``allow_look_into`` is False while the thread
    already waits on SMD for an earlier LOOK_INTO."""
    if not has_act(entries):
        return None
    paragraphs = [b.strip() for b in blocks if isinstance(b, str) and b.strip()]
    lines: list[str] = []
    names: list[str] = []
    seen: set[str] = set()
    for e in entries:
        if e.outcome == OK and e.line and e.target not in seen:
            seen.add(e.target)
            lines.append(e.line)
            names.append(e.name)
    if lines:
        paragraphs.append("\n".join(lines))
    failed = unfinished(entries)
    look = bool(failed) and allow_look_into
    if look:
        paragraphs.append(LOOK_INTO)
    return Composed("\n\n".join(paragraphs), tuple(n for n in names if n), look, failed)


# --------------------------------------------------------------------------
# A turn with no act: two checks on the model's own words
# --------------------------------------------------------------------------

#: "was this done, and if not, please do so": an instruction inside a question.
_CONDITIONAL_RE = re.compile(
    r"\bif\s+(?:not|it\s+(?:has|was|is)n['’]?t|it\s+(?:has|was|is)\s+not|that\s+(?:has|was)n['’]?t"
    r"|that\s+(?:has|was)\s+not)\b[^.?!\n]{0,60}?\b(?:please|kindly|go\s+ahead|do\s+so|do\s+it|file|send"
    r"|prepare|draft|make|revise|update|fix|correct|redo|finish|complete)\b",
    re.IGNORECASE,
)
#: The reply says the work is not done.
_NOT_DONE_RE = re.compile(
    r"\b(?:has\s+not|hasn['’]?t|have\s+not|haven['’]?t|was\s+not|wasn['’]?t|were\s+not|weren['’]?t"
    r"|is\s+not|isn['’]?t|did\s+not|didn['’]?t)\s+(?:yet\s+)?(?:been\s+)?(?:done|filed|sent|prepared"
    r"|drafted|made|completed|started|created|served|revised|updated|submitted|finished|corrected)\b"
    r"|\bnot\s+yet\s+(?:been\s+)?(?:done|filed|sent|prepared|drafted|made|completed|revised|updated)\b",
    re.IGNORECASE,
)
#: A sentence that says a document is not on the matter.
_NOT_FILED_RE = re.compile(
    r"\b(?:not|n['’]t|never)\s+(?:yet\s+)?(?:been\s+)?(?:filed|saved|uploaded|in\s+the\s+(?:file|matter))\b"
    r"|\b(?:could\s+not|couldn['’]t|cannot|can['’]t)\s+find\b|\bno\s+(?:such\s+)?(?:file|document)\b",
    re.IGNORECASE,
)
_SENTENCE_RE = re.compile(r"[^.?!\n]+[.?!]?")


def unfinished_ask(email_words: Any, reply: Any) -> bool:
    """The email asks for the work if it is not done, and the reply says it is
    not done: the act is owed in this turn, not an answer."""
    if not isinstance(email_words, str) or not isinstance(reply, str):
        return False
    return bool(_CONDITIONAL_RE.search(email_words)) and bool(_NOT_DONE_RE.search(reply))


def _stem(name: str) -> str:
    return re.sub(r"\.[A-Za-z0-9]{2,5}$", "", name).strip().lower()


def listing_contradiction(listed: Iterable[str], reply: Any) -> str:
    """A file this turn's listing read shows on the matter, which the reply
    says is not there; "" when none."""
    if not isinstance(reply, str):
        return ""
    stems = [(n, _stem(n)) for n in listed if len(_stem(n)) >= 6]
    for sentence in _SENTENCE_RE.findall(reply):
        if not _NOT_FILED_RE.search(sentence):
            continue
        low = sentence.lower()
        for name, stem in stems:
            if stem in low:
                return name
    return ""


def draft_words(args: Any) -> str:
    """The draft's plain words, read from its arguments (no recorded call)."""
    if not isinstance(args, dict):
        return ""
    for key in ("text", "body_text", "html", "body_html"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return re.sub(r"<[^>]+>", " ", value) if "html" in key else value
    return ""


__all__ = [
    "ACT_TOOLS",
    "FIRM_VERTICALS",
    "LOOK_INTO",
    "READ_TOOLS",
    "TURN_FACTS",
    "Composed",
    "Entry",
    "TurnFacts",
    "compose",
    "draft_words",
    "has_act",
    "listed_names",
    "listing_contradiction",
    "note_call",
    "read_call",
    "unfinished",
    "unfinished_ask",
]
