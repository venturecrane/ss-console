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
from pathlib import Path

#: Below this, a transcription carries no citable clinical content. It is the
#: constant that was already here -- `units.py` and `vision.py` each had their
#: own copy of the literal 50 -- kept because the measurement was sound, and
#: moved here so the two can never again disagree about what it means.
CONTENTLESS_BYTES = 50

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
    """``(state, size_in_bytes)`` for one document's transcription.

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
    return (CONTENTLESS if size <= CONTENTLESS_BYTES else PRESENT), size


def contentless_reason(size: int) -> str:
    """The `compose_skip` a contentless scan carries into the coverage gate.

    Measured, never authored: it states what was found and nothing else. It
    quotes no part of the document, because this text reaches a client-facing
    limitations section and the document's content is the firm's, not ours.
    """
    return f"read in full; {size} bytes of text, no citable clinical content"
