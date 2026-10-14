"""The values a claim states, for the checks that no edit loses one.

A condense, a merge or an audit repair rewrites claim text, and the audit
verifies only what remains: a value that silently disappears is never seen
again (2026-10-09: a home-medication list lost every dosage to a repair).
Numbers are compared outside citations (pages are not values) and in one
spelling per value, so a format variant is neither lost nor new: 209.0 = 209,
97.10 = 97.1, 02 = 2, 02/03/25 = 2/3/2025. Times and ratios keep their parts:
16:05 != 16:06, 132/96 != 132/94.
"""

from __future__ import annotations

import re

NUM = re.compile(r"\d+(?:[.,/:]\d+)*")
CITE = re.compile(r"\(Exhibit (\d+)\s*(?:-\s*p\.\s*([0-9,\s\-]+?))?\s*(?:,\s*machine transcription)?\)")


def norm_number(tok: str) -> str:
    if re.fullmatch(r"\d+\.\d+", tok):
        whole, frac = tok.split(".")
        frac = frac.rstrip("0")
        return str(int(whole)) + ("." + frac if frac else "")
    parts = re.split(r"([/:,.])", tok)
    nums, seps = parts[0::2], parts[1::2]
    if not all(p.isdigit() for p in nums):
        return tok
    vals = [str(int(p)) for p in nums]
    if len(vals) == 3 and seps == ["/", "/"] and len(nums[2]) == 2:
        vals[2] = str(2000 + int(nums[2]))  # m/d/yy -> m/d/yyyy
    return "".join(v + (seps[i] if i < len(seps) else "") for i, v in enumerate(vals))


def numbers(text: str | None) -> set[str]:
    """Normalized numbers outside citations."""
    return {norm_number(t) for t in NUM.findall(CITE.sub("", text or ""))}


def lost(before: str, after: str) -> list[str]:
    """Values `before` states that `after` no longer carries, sorted."""
    return sorted(numbers(before) - numbers(after))


def named_by(problems: list[str] | None) -> set[str]:
    """The values an audit finding names (its unsupported assertions and
    contradictions): the only values an edit may remove."""
    return numbers(" ".join(str(p) for p in problems or []))
