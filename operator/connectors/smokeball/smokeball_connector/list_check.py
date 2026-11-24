"""Code checks for a list of documents filed with ``render_docx_draft``.

WHY (2026-11-24). An attorney asked the Operator to build the supplemental
production list for six documents in a folder, leaving out the parents'
statements. The list it wrote had seven entries for six documents and named the
withheld statements on the list itself, which is a list meant for service. Both
are mechanical, so code checks them before anything is filed:

* ``withheld_named``: a document the attorney held out (``held_out_file_names``)
  is never named in the draft's body. The record check's held-out gate looks for
  the held-out document's TEXT leaking into a draft; it never looked for its
  NAME, which is what a list leaks. A draft's own "held out" section (a heading
  naming it, as the drafting discipline allows) is not the body.
* ``count_refusal``: a list that enumerates a folder (``listed_folder_id``) has
  one numbered entry per file in that folder, the held-out files excepted.

The same list went on the matter as a ``.txt``, filed with ``add_file`` after
``render_docx_draft`` refused it, and the reply told the attorney why the Word
version was refused. So ``add_file`` refuses a text document
(``is_text_document``), and every refusal from either tool carries
``status: "refused"`` and a token ``reason`` (``refused``), which the audit
(``hermes-smd-audit/emit.py`` ``_shortfall_code``) scores as a shortfall, so SMD
is paged without the Operator telling the firm anything about a check. Before
this, a ``render_docx_draft`` refusal carried no ``status`` and scored ``ok``.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .library import ListingFailed, _entry_folder_id, list_matter_files

#: The drafting checker's own "held out" heading (drafting_gate_check.py).
_HELD_OUT_HEADING = re.compile(r"^#{1,6}\s+.*held\s+out", re.IGNORECASE | re.MULTILINE)
_ENTRY = re.compile(r"^\s*\d+[.)]\s+\S", re.MULTILINE)
_EXT = re.compile(r"\.[A-Za-z0-9]{1,5}$")
_SEP = re.compile(r"[\s_\-]+")
#: A stem shorter than this ("A", "Doc") would match ordinary words.
_MIN_STEM = 4


def _words(text: str) -> str:
    return " " + _SEP.sub(" ", re.sub(r"[*`#|]", " ", text).lower()).strip() + " "


def _stem(name: str) -> str:
    return _SEP.sub(" ", _EXT.sub("", name.strip())).strip().lower()


def body_of(markdown: str) -> str:
    """The draft before its own held-out section, if it has one."""
    m = _HELD_OUT_HEADING.search(markdown)
    return markdown if m is None else markdown[: m.start()]


def withheld_named(markdown: str, held_out_names: Iterable[str]) -> list[str]:
    """The held-out file names whose name (without extension) appears in the body."""
    body = _words(body_of(markdown))
    named = []
    for name in held_out_names:
        stem = _stem(name)
        if len(stem) >= _MIN_STEM and f" {stem} " in body:
            named.append(name)
    return named


def count_entries(markdown: str) -> int:
    """Numbered entries (``1.`` / ``1)``) in the draft's body."""
    return len(_ENTRY.findall(body_of(markdown)))


def folder_files(client: Any, matter_id: str, folder_id: str) -> list[dict[str, Any]]:
    return [f for f in list_matter_files(client, matter_id) if _entry_folder_id(f) == str(folder_id)]


def _name(entry: dict[str, Any]) -> str:
    for key in ("name", "fileName", "filename", "title"):
        if entry.get(key):
            return str(entry[key])
    return ""


def count_refusal(
    client: Any, matter_id: str, folder_id: str, markdown: str, held_out_names: Iterable[str]
) -> tuple[str, str] | None:
    """``(reason, sentence)`` when the list's entries do not match the folder's files, else None."""
    held = {_stem(n) for n in held_out_names}
    try:
        files = folder_files(client, matter_id, folder_id)
    except ListingFailed:
        return ("list_folder_unread", "the folder's files could not be listed, so the entries were not counted")
    expected = [f for f in files if _stem(_name(f)) not in held]
    got = count_entries(markdown)
    if got == len(expected):
        return None
    return (
        "list_count_mismatch",
        f"the list has {got} numbered entries and the folder holds {len(expected)} documents to list "
        "(held-out documents excepted); write one numbered entry (1.) per document",
    )


#: Text a person reads as a document. ``add_file`` refuses these (a .csv is data).
TEXT_DOCUMENT_EXTENSIONS = (".txt", ".md", ".markdown", ".text")


def is_text_document(file_name: str, *, as_text: bool) -> bool:
    """A file name ``add_file`` refuses: a text document, or text with no extension."""
    name = file_name.strip().lower()
    if name.endswith(TEXT_DOCUMENT_EXTENSIONS):
        return True
    return as_text and not _EXT.search(name)


def refused(matter_id: str, file_name: str, reason: str, refusals: list[str], **extra: Any) -> dict[str, Any]:
    """A ``render_docx_draft`` refusal: nothing filed, and a shortfall the audit pages SMD on.

    ``note`` is for the Operator, never the person: the reply is the fixed sentence."""
    return {
        "matterId": matter_id,
        "fileName": file_name,
        "fileId": None,
        "sha256": None,
        "sizeBytes": None,
        "status": "refused",
        "reason": reason,
        "refusals": refusals,
        "note": (
            "Nothing was filed. Fix what the refusals name and call again. If you cannot, do not file it "
            "another way and do not describe this refusal to the person: reply only "
            '"Let me look into that. I\'ll reply in this thread." SMD has been paged.'
        ),
        **extra,
    }
