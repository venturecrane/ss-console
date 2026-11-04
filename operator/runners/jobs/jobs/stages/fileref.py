"""A file reference as the composer writes it, parsed in ONE place.

The chunk header names every file `=== FILE: <name> (fileId <id>) ===`, and
when two files share a name the model disambiguates the way a person would:
`image001.jpg [fileId msgatt-8d56]` in a citation, `image001.jpg (msgatt-8d56)`
in an INDEX cell, and several files on one INDEX row separated by `;` when
one entry drew on all of them. Live-caught 2026-09-15: three parsers each
read the raw cell and none agreed with the model, so 28 cited records fell
to the unattributed lane and the exhibit build refused.

A trailing parenthetical is an id marker only when it names a KNOWN id --
`clinic note (1).pdf` is a file name, not a reference to file "1".
"""

from __future__ import annotations

import re

MARKER = re.compile(r"^(.*?)\s*[\(\[]\s*(?:fileId\s+)?([A-Za-z0-9][\w.-]*)\s*[\)\]]\s*$")


def parse(ref: str, ids: set[str] | None = None) -> tuple[str, str | None]:
    """`(name, id)`; the id is None unless the marker names a known id."""
    ref = (ref or "").strip()
    m = MARKER.match(ref)
    if m and ids and m.group(2) in ids:
        return m.group(1).strip(), m.group(2)
    return ref, None


def split_cell(cell: str, ids: set[str] | None = None) -> list[tuple[str, str | None]]:
    """An INDEX file cell: one file, or several separated by `;`."""
    return [parse(part, ids) for part in (cell or "").split(";") if part.strip()]
