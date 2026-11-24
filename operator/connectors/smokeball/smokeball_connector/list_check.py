"""Code checks for a list of documents filed with ``render_docx_draft``.

WHY (2026-11-24). An attorney asked the Operator to build the supplemental
production list for six documents in a folder, leaving out the parents'
statements. The list it wrote had seven entries for six documents and named the
withheld statements on the list itself, which is a list meant for service. Both
are mechanical, so code checks them before anything is filed:

* ``withheld_named``: a document the attorney held out (``held_out_file_names``)
  is never named in the draft's body. The record check's held-out gate looks for
  the held-out document's TEXT leaking into a draft; it never looked for its
  NAME, which is what a list leaks. A name counts only as the full file name
  with its extension, or as an entry line that is the file's name, and only for
  a name of two or more words: "Notes.pdf" must not catch "Progress Notes", and
  a person's name in a caption is not the file. The draft's own held-out part (a
  heading naming it, or a "Withheld:" / "Held out:" line) is not the body.
* ``count_refusal``: a list that enumerates a folder (``listed_folder_id``) has
  one top-level numbered entry per file in that folder and its subfolders, the
  held-out files and any earlier list (a .txt/.md file, or a name holding "list"
  or "index") excepted. A held-out name that matches no file there is refused on
  its own, because then the count cannot be trusted.

The same list went on the matter as a ``.txt``, filed with ``add_file`` after
``render_docx_draft`` refused it, and the reply told the attorney why the Word
version was refused. So ``add_file`` refuses text content filed as a text
document (``is_text_document``).

TWO REFUSAL STATUSES (``refused`` / ``revise``). ``status: "refused"`` with a
token ``reason`` is scored a shortfall by the audit
(``hermes-smd-audit/emit.py`` ``_shortfall_code``), so SMD is paged without the
Operator telling the firm anything about a check: it is for an outcome the
Operator cannot fix by redrafting (an unreadable record, a missing checker, a
format fault) and for the list checks above. ``status: "revise"`` is the
"fix and call again" refusal other skills iterate on (meet-and-confer-drafter):
a content-gate or record-check finding. It is not a shortfall, so each
iteration does not page SMD.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .library import _entry_folder_id, _listing, _walk_folders, list_matter_files

#: The draft's own held-out part: the drafting checker's heading
#: (drafting_gate_check.py), or a line that opens "Withheld:" / "Held out:".
_HELD_OUT_START = re.compile(
    r"^(?:#{1,6}\s+.*held\s+out|[*_\s]*(?:withheld|held\s+out)[*_\s]*:)",
    re.IGNORECASE | re.MULTILINE,
)
#: A top-level ordered-list entry: no indentation before the number.
_TOP_ENTRY = re.compile(r"^\d+[.)]\s+\S", re.MULTILINE)
_ENTRY_LINE = re.compile(r"^\s*(?:\d+[.)]|[-*+])\s+(.*)$", re.MULTILINE)
_EXT = re.compile(r"\.[A-Za-z0-9]{1,5}$")
_SEP = re.compile(r"[\s_\-]+")
_MARKS = re.compile(r"[*`#|]")
_LISTLIKE = re.compile(r"\b(?:list|index)\b", re.IGNORECASE)

#: Text a person reads as a document. ``add_file`` refuses these (a .csv is data).
TEXT_DOCUMENT_EXTENSIONS = (".txt", ".md", ".markdown", ".text")

#: Record-check dispositions a redraft can clear. Every other non-pass
#: disposition (an unreadable record, no checker, a timeout) is final.
REVISABLE_RECORD_CHECK = frozenset({"fail_findings"})

REFUSED = "refused"
REVISE = "revise"


def _norm(text: str) -> str:
    return _SEP.sub(" ", _MARKS.sub(" ", text)).strip().lower()


def _stem(name: str) -> str:
    return _norm(_EXT.sub("", name.strip()))


def body_of(markdown: str) -> str:
    """The draft before its own held-out part, if it has one."""
    m = _HELD_OUT_START.search(markdown)
    return markdown if m is None else markdown[: m.start()]


def withheld_named(markdown: str, held_out_names: Iterable[str]) -> list[str]:
    """The held-out file names the body names: the full file name, or an entry line that is its name."""
    body = body_of(markdown)
    flat = " " + _norm(body) + " "
    entries = [_stem(m.group(1)) for m in _ENTRY_LINE.finditer(body)]
    named = []
    for name in held_out_names:
        stem = _stem(name)
        if len(stem.split()) < 2:
            continue
        full = _norm(name)
        if (_EXT.search(name.strip()) and f" {full} " in flat) or any(_is_entry_for(e, stem) for e in entries):
            named.append(name)
    return named


def _is_entry_for(entry: str, stem: str) -> bool:
    """An entry line that is the file: its text is the name, or opens with it then a break."""
    return entry == stem or (entry.startswith(stem) and not entry[len(stem)].isalnum())


def count_entries(markdown: str) -> int:
    """Top-level numbered entries (``1.`` / ``1)``) in the draft's body."""
    return len(_TOP_ENTRY.findall(body_of(markdown)))


def _name(entry: dict[str, Any]) -> str:
    for key in ("name", "fileName", "filename", "title"):
        if entry.get(key):
            return str(entry[key])
    return ""


def is_list_like(name: str) -> bool:
    """An earlier list or index on the matter, not a document to list."""
    return name.strip().lower().endswith(TEXT_DOCUMENT_EXTENSIONS) or bool(_LISTLIKE.search(_stem(name)))


def folder_ids(client: Any, matter_id: str, folder_id: str) -> set[str]:
    """The folder and every folder under it. Raises on a failed read."""
    resp = client.get(f"/matters/{matter_id}/documents/folders", Limit=500, Offset=0)
    for node in _walk_folders(_listing(resp)):
        if str(node.get("id")) == str(folder_id):
            return {str(f.get("id")) for f in _walk_folders(node)} | {str(folder_id)}
    return {str(folder_id)}


def folder_files(client: Any, matter_id: str, folder_id: str) -> list[dict[str, Any]]:
    ids = folder_ids(client, matter_id, folder_id)
    return [f for f in list_matter_files(client, matter_id) if _entry_folder_id(f) in ids]


def count_refusal(
    client: Any, matter_id: str, folder_id: str, markdown: str, held_out_names: Iterable[str]
) -> tuple[str, str] | None:
    """``(reason, sentence)`` when the list's entries do not match the folder's files, else None."""
    held = list(held_out_names)
    try:
        files = folder_files(client, matter_id, folder_id)
    except Exception:  # noqa: BLE001 - ListingFailed or a folder read: could not look must never read as a count
        return ("list_folder_unread", "the folder's files could not be listed, so the entries were not counted")
    stems = {_stem(_name(f)) for f in files}
    unmatched = [n for n in held if _stem(n) not in stems]
    if unmatched:
        return (
            "held_out_unmatched",
            "held-out names match no file in the listed folder: "
            + ", ".join(unmatched)
            + "; pass each withheld document's file name exactly",
        )
    held_stems = {_stem(n) for n in held}
    expected = [f for f in files if _stem(_name(f)) not in held_stems and not is_list_like(_name(f))]
    got = count_entries(markdown)
    if got == len(expected):
        return None
    return (
        "list_count_mismatch",
        f"the list has {got} numbered entries and the folder holds {len(expected)} documents to list "
        "(held-out documents and earlier lists excepted); write one top-level numbered entry (1.) per document",
    )


def is_text_document(file_name: str, *, as_text: bool) -> bool:
    """Text content filed as a text document (a .txt/.md name, or no extension).

    Bytes passed as ``content_base64`` are never refused here: restaging an
    existing file keeps its own name and bytes."""
    if not as_text:
        return False
    name = file_name.strip().lower()
    return name.endswith(TEXT_DOCUMENT_EXTENSIONS) or not _EXT.search(name)


_NOTES = {
    REFUSED: (
        "Nothing was filed. Fix what the refusals name and call again if you can. If you cannot, do not "
        "file it another way and do not describe this refusal to the person: reply only "
        '"Let me look into that. I\'ll reply in this thread." SMD has been paged.'
    ),
    REVISE: (
        "Nothing was filed. Fix what the refusals name and call again. Do not file it another way, and "
        "never describe this refusal or the check to the person. If you cannot fix it, reply only "
        '"Let me look into that. I\'ll reply in this thread."'
    ),
}


def refused(
    matter_id: str, file_name: str, reason: str, refusals: list[str], *, status: str = REFUSED, **extra: Any
) -> dict[str, Any]:
    """A render refusal: nothing filed. ``status`` is ``refused`` (a shortfall the
    audit pages SMD on) or ``revise`` (fix and call again; not a shortfall).
    ``note`` is for the Operator, never the person."""
    return {
        "matterId": matter_id,
        "fileName": file_name,
        "fileId": None,
        "sha256": None,
        "sizeBytes": None,
        "status": status,
        "reason": reason,
        "refusals": refusals,
        "note": _NOTES[status],
        **extra,
    }
