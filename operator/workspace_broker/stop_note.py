"""Whether a job's reply is a STOPPED note the bind must let through.

2026-11-06: a drafting job that held on our side, failed, or waited on its
lane's proof run is owed one note in the broker's words
(``fixed_wording.STOPPED``). ``reply_binding._verify_job`` asks this before it
refuses a failed or unfinished job; its own once-per-``attempt:state`` claim
still applies. Kept out of ``reply_binding.py``, which sits at its module size
ceiling, and out of ``fixed_wording`` (imported lazily: it imports
``reply_binding``).
"""

from __future__ import annotations

from typing import Any

from . import reply_text


def owed(kind: str, row: dict[str, Any]) -> bool:
    """A code-sent kind's job in a state ``fixed_wording.STOPPED`` words."""
    from . import fixed_wording

    return kind in reply_text.CODE_SENT_KINDS and fixed_wording.stopped_class(kind, row) is not None


__all__ = ["owed"]
