"""A firm's Word file as text: the two forms the jobs lanes read with.

They are two DECLARED forms, not one reader, because each lane's later stages
were written against its own (``policy``'s ``word`` column; wave 5 keeps both
byte for byte; one reader in document order is a later, reviewed step):

* ``paragraphs-then-rows`` (:func:`docx_text`): every paragraph, blank ones
  included, then each table row as its cells joined with `` | ``, from the
  file's bytes. The litigation lane, the case file manager and the
  negotiation watch (an email's Word attachment included).
* ``nonblank-paragraphs-then-stripped-rows`` (:func:`record_docx_text`): the
  paragraphs that carry text, then each table row with its cells stripped,
  from a path. The chronology and demand.
"""

from __future__ import annotations

import io
from pathlib import Path


def docx_text(data: bytes) -> str:
    import docx

    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            parts.append(" | ".join(c.text for c in row.cells))
    return "\n".join(parts)


def record_docx_text(path: Path) -> str:
    import docx

    d = docx.Document(str(path))
    out = [p.text for p in d.paragraphs if p.text.strip()]
    for tb in d.tables:
        for row in tb.rows:
            out.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(out)


#: Each declared form, by the name ``policy`` rows use.
FORMS = {"paragraphs-then-rows": docx_text, "nonblank-paragraphs-then-stripped-rows": record_docx_text}

__all__ = ["FORMS", "docx_text", "record_docx_text"]
