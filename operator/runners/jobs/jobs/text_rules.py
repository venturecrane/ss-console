"""Text rules written once: the patterns several lanes read text with, and the
document readers two of them shared as copies.

Every rule here was a byte-identical copy in two to seven modules (wave 2,
item 2.2a). Each of those modules now imports the rule under the name it
always used, so a module's own code reads exactly as before and a change to a
rule lands here once. Rules that differ between lanes, even slightly, are NOT
here: folding two different rules into one changes what a lane finds, and
each such change is its own reviewed step (2.2b).
``tests/goldens/text_rules`` pins every checker's answers.

The chronology's text
    :data:`ENTRY_START`, :data:`DATE_HEAD`, :data:`EXHIBIT_CITE` and its
    two variants, :data:`EXHIBIT_PAGE_MENTION`, :data:`US_DATE`.

The system's own words
    :data:`SENTINEL_LINE`, a ``=== HEADING ===`` line a model was told to write
    and a deliverable must never carry.

Document readers (the litigation status job and the negotiation watch)
    :class:`ExtractFailure`, :func:`paged`, :func:`docx_text`,
    :func:`html_text`, :func:`doc_text` and the constants they use. Each lane
    keeps its own PDF reader and email walk, because those differ.
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

# ---- the chronology's text ----------------------------------------------------
#: Where a chronology entry starts: a line that opens with MM/DD/YYYY, alone or
#: before a parenthesis (``03/14/2024`` or ``03/14/2024 (as written)``). Used to
#: split a chronology into its entries.
ENTRY_START = re.compile(r"(?m)^(?=\d{2}/\d{2}/\d{4}\s*(?:\(|$))")
#: The date an entry opens with, as (month, day, year).
DATE_HEAD = re.compile(r"^(\d{2})/(\d{2})/(\d{4})")
#: A chronology citation as the pipeline writes it: ``(Exhibit 3 - p. 4, 6-7)``,
#: with the exhibit number and the pages as groups.
EXHIBIT_CITE = re.compile(r"\(Exhibit (\d+)(?: - p\. ([0-9,\s\-]+))?\)")
#: The same citation, matched without groups.
EXHIBIT_CITE_PLAIN = re.compile(r"\(Exhibit \d+(?: - p\. [0-9,\s\-]+)?\)")
#: The same citation read leniently (spacing, and a ``machine transcription``
#: note), as a delivered chronology may carry it.
EXHIBIT_CITE_LENIENT = re.compile(r"\(Exhibit (\d+)\s*(?:-\s*p\.\s*([0-9,\s\-]+?))?\s*(?:,\s*machine transcription)?\)")
#: An exhibit a reviewer names in prose (``Exhibit 4, p. 2``, ``exhibit 4 page 2``).
EXHIBIT_PAGE_MENTION = re.compile(r"(?i)\bExhibit\s+(\d+)(?:\s*[,-]?\s*p(?:age|p)?\.?\s*(\d+))?")
#: A whole MM/DD/YYYY date anywhere in a line.
US_DATE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")

# ---- the system's own words --------------------------------------------------
#: A ``=== NEEDS THE ATTORNEY ===`` style heading line.
SENTINEL_LINE = re.compile(r"(?m)^\W*===\s*[A-Z][A-Z ]+[^=\n]*===\s*$")

# ---- document readers --------------------------------------------------------
PAGE_MARK = "===== page {n} ====="
SCAN_FLOOR = 25  # characters: a page with less carries no text layer
OCR_PROMPT = (
    "Transcribe every word on this page exactly as printed, top to bottom, including stamps, handwriting you can "
    "read, and form labels. For a checkbox write [X] when it is marked and [ ] when it is empty. Output the text only."
)
Ocr = Callable[[bytes], str]


class ExtractFailure(RuntimeError):
    def __init__(self, problem: str) -> None:
        self.problem = problem
        super().__init__(problem)


#: The failure code of a PDF locked with a password (``unopened``): a locked
#: attachment is named inside its email instead of making the email, body and
#: every other attachment, one unreadable file.
PASSWORD = "pdf_password"


def paged(texts: list[str]) -> str:
    return "\n".join(f"{PAGE_MARK.format(n=i)}\n{t.strip()}" for i, t in enumerate(texts, 1))


def docx_text(data: bytes) -> str:
    import docx

    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            parts.append(" | ".join(c.text for c in row.cells))
    return "\n".join(parts)


def html_text(raw: bytes | str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())


def doc_text(path: Path) -> str:
    exe = shutil.which("antiword")
    if not exe:
        raise ExtractFailure("doc_unreadable: antiword is not installed on this seat")
    r = subprocess.run([exe, str(path)], capture_output=True, text=True, timeout=120, check=False)  # noqa: S603 - fixed binary, a path we wrote
    if r.returncode != 0:
        raise ExtractFailure("doc_unreadable: antiword could not read the file")
    return r.stdout


__all__ = [
    "DATE_HEAD",
    "ENTRY_START",
    "EXHIBIT_CITE",
    "EXHIBIT_CITE_LENIENT",
    "EXHIBIT_CITE_PLAIN",
    "EXHIBIT_PAGE_MENTION",
    "OCR_PROMPT",
    "PAGE_MARK",
    "PASSWORD",
    "SCAN_FLOOR",
    "SENTINEL_LINE",
    "US_DATE",
    "ExtractFailure",
    "Ocr",
    "doc_text",
    "docx_text",
    "html_text",
    "paged",
]
