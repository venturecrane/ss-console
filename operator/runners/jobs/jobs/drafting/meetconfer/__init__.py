"""The meet-and-confer memo, written inside an amended-responses job (design C, part 8).

Not a document class of its own: the amended job writes the memo as its last
unit, so its "amended response" lines are read by code from the amended files
it just drafted, never decided a second way. This package holds the shape the
amended job and the memo share; the challenge read and the memo are built in
their own changes, and nothing calls this yet.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Challenge:
    """One item the other side's letter challenges, verified against the letter:
    the set type, the request numbers, the letter's own words, which letter and
    page, and whether it is a general challenge that names no number."""

    set_type: str
    numbers: tuple[int, ...]
    quote: str
    letter: str
    page: int
    general: bool = False


__all__ = ["Challenge"]
