"""What a scanned document's transcription on disk actually tells us.

ONE reader, used by the vision stage's resume test and by build_units' gate,
because those two asked the same question with the same magic number and drew
OPPOSITE conclusions from it.

The number was never wrong. A transcription of a few dozen bytes really does
mean the document carries no clinical content. What was wrong is what
``build_units`` concluded from it: that the document had not been READ. On
2026-09-11 a firm's chronology refused with "4 scan-queued file(s) have NO
transcription yet" over four phone screenshots the Operator had read correctly
and completely -- ``[illegible]``, ``View motion photo`` twice, and a phone
status bar. The run stopped for four days and two sessions diagnosed it as an
image-rendering fault, because the message said the files were unread.

So the states are named, and each one names the act it licenses:

``ABSENT``       no text file. Vision has not finished this document. REFUSE.
``EMPTY``        a zero-length file. Vision was killed mid-write (the write is
                 atomic now, so this is a pre-fix artefact) or wrote nothing.
                 Indistinguishable from unread, so it REFUSES too.
``UNRECORDED``   a text file with no ``ocr_results`` row: the pass died between
                 writing the text and recording the read. REFUSE; a resume
                 completes it cheaply.
``CONTENTLESS``  read, recorded, and carrying nothing a chronology can cite.
                 NOT a refusal -- a disposition. The document is carried into
                 the composition set with a ``compose_skip`` so the coverage
                 gate has its reason, and the client is told what was on it.
``PRESENT``      read, recorded, with content. The ordinary case.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

#: Fewer WORDS than this and a transcription carries no citable clinical
#: content. Words, not bytes, because transcription is not byte-identical
#: between runs and the byte cutoff sat inside the noise: one phone screenshot
#: came back at 44, 53 and 57 bytes on three runs of the SAME document against
#: a threshold of 50, so it was disposed of once and unexplained twice.
#:
#: Measured on a real matter's 82 scanned documents (2026-09-15), counting
#: words of three or more letters after page headers and illegibility markers
#: are removed:
#:
#:     0 words   5 documents   (phone status bars, a bare [illegible])
#:     3 words   2 documents   ("View motion photo")
#:     --- nothing at all between 4 and 24 ---
#:    25 words   1 document    (a photograph with a real description)
#:    38+ words  74 documents  (records)
#:
#: 10 sits in an empty gap eight times wider than either cluster, and a word
#: count moves by ones where a byte count moves by tens.
#:
#: A false positive is cheap BY CONSTRUCTION: this disposition sets only
#: `compose_skip`, never `compose`, so the document is still composed and still
#: citable, and `coverage.py` tests CITED before it reads the explanation. The
#: reason only ever applies to a document nothing cited.
CONTENTLESS_WORDS = 10

#: Page headers (`[p.3] (machine transcription)`) and illegibility markers are
#: the transcriber's own furniture, not the document's content, so neither
#: counts toward the words a chronology could cite.
_PAGE_HEADER = re.compile(r"\[p\.\d+\][^\n]*")
_ILLEGIBLE = re.compile(r"\[illegible\]", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z]{3,}")


def citable_words(text: str) -> int:
    """Words of three or more letters, ignoring the transcriber's furniture."""
    return len(_WORD.findall(_ILLEGIBLE.sub(" ", _PAGE_HEADER.sub(" ", text))))


ABSENT = "absent"
EMPTY = "empty"
UNRECORDED = "unrecorded"
CONTENTLESS = "contentless"
PRESENT = "present"

#: The states that mean "this document has not been read yet". Anything not in
#: this set has been read, whatever its length.
UNREAD = frozenset({ABSENT, EMPTY, UNRECORDED})


def recorded_ids(slug_dir: Path) -> set[str]:
    """Document ids the vision stage recorded a completed read for.

    Read from ``ocr_results.jsonl`` rather than inferred: a text file can exist
    without a row (killed between the two writes), and that gap is exactly the
    case a size check cannot see.
    """
    out: set[str] = set()
    path = slug_dir / "ocr_results.jsonl"
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("id"):
            out.add(str(row["id"]))
    return out


def transcript_state(slug_dir: Path, rec_id: str, recorded: set[str] | None = None) -> tuple[str, int]:
    """``(state, measure)`` for one document's transcription.

    ``measure`` is the CITABLE WORD COUNT once the document has been read, and
    the byte size for the unread states (where there is no text to count and
    the size is what a refusal message needs to report).

    ``recorded`` is the id set from :func:`recorded_ids`, passed in when
    checking many documents so the results file is read once. Omit it for a
    single check.

    An ``ocr_results.jsonl`` that does not exist AT ALL is treated as "no
    record keeping here" rather than "nothing was read": that is the shape of
    a synthetic run, and refusing every document on it would turn the absence
    of a log into a client-facing failure. A results file that EXISTS and does
    not carry this id is the real signal -- the pass died between writing the
    text and recording the read -- and that refuses.
    """
    path = slug_dir / "text" / f"{rec_id}.txt"
    if not path.is_file():
        return ABSENT, 0
    size = path.stat().st_size
    if size == 0:
        return EMPTY, 0
    ids = recorded_ids(slug_dir) if recorded is None else recorded
    if ids and rec_id not in ids:
        return UNRECORDED, size
    words = citable_words(path.read_text(encoding="utf-8", errors="replace"))
    return (CONTENTLESS if words < CONTENTLESS_WORDS else PRESENT), words


def contentless_reason(words: int) -> str:
    """The `compose_skip` a contentless scan carries into the coverage gate.

    Measured, never authored: it states what was found and nothing else. It
    quotes no part of the document, because this text reaches a client-facing
    limitations section and the document's content is the firm's, not ours.
    """
    plural = "" if words == 1 else "s"
    # Measured, never inferred. "No citable clinical content" was an inference,
    # and it reaches a client's limitations section: a six-word prescription
    # label that happened to go uncited would have carried a false sentence.
    return f"read in full; {words} word{plural} of text; nothing in it was cited"
