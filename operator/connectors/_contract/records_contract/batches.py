"""How staged matter files split into replies (ADR 0102).

Lifted from ``operator/hermes-smd-overlay/shared/matter_file_reply.py``
(``_batches``, ``continuation_text`` and the refusals ``on_stage_result`` and
``prepare`` make on the count). Operates on plain file rows, the shape
``StagedFile.as_row()`` writes and a review row's ``batches`` hold:
``{file_id, name, sha256, size, handle, pages, page_count}``. Only ``size``
and ``name`` are read here; every key a row carries rides through unchanged.
"""

from __future__ import annotations

from typing import Any

#: The fleet's per-message cap.
MAX_FILES_PER_REPLY = 3
MAX_BYTES_PER_REPLY = 25 * 1024 * 1024
#: Replies one request may take (the person's reply plus continuations).
MAX_REPLIES = 3


class BatchesRefused(ValueError):
    """The files cannot go on one request's replies; the message says why, in the overlay's words."""


def split(files: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Files in order, a new reply whenever the next one would pass three files
    or 25 MiB. A single file over 25 MiB still goes alone (the per-file cap is
    the stager's check, not this one). Never refuses."""
    out: list[list[dict[str, Any]]] = []
    for f in files:
        if (
            not out
            or len(out[-1]) >= MAX_FILES_PER_REPLY
            or sum(int(x.get("size") or 0) for x in out[-1]) + int(f.get("size") or 0) > MAX_BYTES_PER_REPLY
        ):
            out.append([])
        out[-1].append(f)
    return out


def batches(files: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """:func:`split`, refused as the overlay refuses today: more than nine files
    for one request (the staging refusal), or a split needing more than three
    replies (the reply refusal). An empty list is an empty list."""
    if len(files) > MAX_FILES_PER_REPLY * MAX_REPLIES:
        raise BatchesRefused(f"more than {MAX_FILES_PER_REPLY * MAX_REPLIES} files were staged for one request")
    out = split(files)
    if len(out) > MAX_REPLIES:
        raise BatchesRefused(f"the files need more than {MAX_REPLIES} replies")
    return out


def continuation_text(index: int, total: int, batch: list[dict[str, Any]]) -> str:
    """The code-fixed words of a further reply carrying the rest of the files."""
    names = ", ".join(str(f.get("name")) for f in batch)
    return f"Part {index} of {total} of the files you asked for, attached: {names}."


__all__ = [
    "MAX_BYTES_PER_REPLY",
    "MAX_FILES_PER_REPLY",
    "MAX_REPLIES",
    "BatchesRefused",
    "batches",
    "continuation_text",
    "split",
]
