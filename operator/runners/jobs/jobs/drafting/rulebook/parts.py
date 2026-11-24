"""A response read into its sentences, each with its role, phrase-book key and
ground (design C, "Parts checker"): a pure function, no I/O. Interface only:
it reads nothing yet."""

from __future__ import annotations

from .types import PartsDoc


def read(md: str, doc_type: str, hint: str | None = None) -> PartsDoc:
    """The document's items and their sentences; empty until it is built."""
    del md, doc_type, hint
    return PartsDoc()
