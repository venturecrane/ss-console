"""A reply may say work is underway only when the job store holds that work.

THE LIVE FAILURE (A&P, 2026-11-04, 15:30 MST). Answering the attorney's "Yes"
on a formatting rule, the Operator wrote "The revised brief for matter 200045
is still being prepared with your corrections". No drafting job was running:
the last one had failed at 14:48. The sentence was the model's memory of the
conversation, not a fact about the seat, and the attorney waited on a brief
nobody was writing.

So the claim is checked against the record, not the model (Law 18): a sentence
asserting present work in progress ships only when a live job exists in one of
the lane ledgers, and, when the sentence names a matter, a live job on THAT
matter. "Live" is a state the ledger wrote: submitted, running or held on a
lane; queued, running, complete or delivering on the background ledger.

FAIL CLOSED ON A READ THAT CANNOT ANSWER. "Could not look" must not read as
"there is work" any more than as "there is none"; a refused claim costs one
rewrite of one sentence, while a false one costs the client's trust in every
status the seat reports.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

#: Lane ledger states that mean somebody's work is still in hand.
LIVE_LANE_STATES = frozenset({"submitted", "running", "held"})
#: The background job ledger's non-terminal statuses (job_ledger.CLAIMABLE_STATUSES).
LIVE_BACKGROUND_STATUSES = frozenset({"queued", "running", "complete", "delivering"})

#: Present-tense, in-progress assertions. Each is a claim about the seat's
#: state RIGHT NOW; a future promise ("we will send it") is another question.
_CLAIMS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"\bbeing (?:prepared|drafted|redrafted|revised|reworked|worked on|generated|"
        r"built|put together|finali[sz]ed|updated|corrected|reformatted|processed)\b",
        r"\b(?:is|are|'s|remains?) (?:now |currently |still )?"
        r"(?:in progress|under ?way|in the works|in process|being handled)\b",
        r"\b(?:we|i)(?:'re| are|'m| am) (?:now |currently |still )?"
        r"(?:preparing|drafting|redrafting|revising|reworking|working on|finali[sz]ing|"
        r"updating|correcting|reformatting|putting together|generating)\b",
        r"\b(?:currently|still) (?:preparing|drafting|revising|working on|running|generating)\b",
        r"\b(?:is|are) (?:now |currently |still )?running\b",
    )
)

#: A negation in the run of text before a claim turns it into its opposite:
#: "nothing is being prepared", "it is no longer in progress".
#: Read only within the claim's own clause, so "No worries, the brief is still
#: being prepared" stays a claim.
_NEGATION = re.compile(r"\b(?:not|no|nothing|none|never)\b|n't\b")
_NEGATION_WINDOW = 40
_CLAUSE_BREAK = re.compile(r"[,;:–—]")

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_MATTER = re.compile(r"\bmatter\s*(?:no\.?|number|#)?\s*([0-9]{4,8})\b")


def _flatten(text: str) -> str:
    folded = text.lower().replace("’", "'").replace("‘", "'")
    return re.sub(r"[ \t]+", " ", folded)


def underway_claims(text: Any) -> list[str]:
    """Every sentence of ``text`` that asserts work is in progress now."""
    if not isinstance(text, str) or not text:
        return []
    found: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(_flatten(text)):
        for pattern in _CLAIMS:
            match = pattern.search(sentence)
            if match is None:
                continue
            window = sentence[max(0, match.start() - _NEGATION_WINDOW) : match.start()]
            window = _CLAUSE_BREAK.split(window)[-1]
            if _NEGATION.search(window):
                continue
            found.append(sentence.strip())
            break
    return found


def named_matters(sentences: Iterable[str]) -> set[str]:
    """The matter numbers the claim sentences name ("matter 200045")."""
    out: set[str] = set()
    for sentence in sentences:
        out.update(_MATTER.findall(sentence))
    return out


def live_work(
    lane_reads: Iterable[Callable[[], list[dict[str, Any]]]],
    background_read: Callable[[], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Every live job, as ``{"lane", "matter_number", "id"}``. Raises when any
    read cannot answer: the caller then refuses the claim."""
    live: list[dict[str, Any]] = []
    for read in lane_reads:
        for job in read() or []:
            if not isinstance(job, dict):
                continue
            if str(job.get("state") or "") in LIVE_LANE_STATES:
                live.append(
                    {
                        "lane": "lane",
                        "matter_number": str(job.get("matter_number") or ""),
                        "id": str(job.get("id") or ""),
                    }
                )
    if background_read is not None:
        for job in background_read() or []:
            if isinstance(job, dict) and str(job.get("status") or "") in LIVE_BACKGROUND_STATUSES:
                live.append(
                    {"lane": "background", "matter_number": "", "id": str(job.get("id") or "")}
                )
    return live


def unsupported_claim(text: Any, read_live: Callable[[], list[dict[str, Any]]]) -> str | None:
    """The first in-progress claim the job store does not back, or ``None``.

    ``read_live`` is only called when there is a claim to check, so an ordinary
    reply costs no broker round trip. A read that raises refuses the claim.
    """
    claims = underway_claims(text)
    if not claims:
        return None
    try:
        live = read_live()
    except Exception:  # noqa: BLE001 -- "could not look" must not back a claim
        return claims[0]
    matters = named_matters(claims)
    if matters:
        if any(job.get("matter_number") in matters for job in live):
            return None
        return claims[0]
    return None if live else claims[0]


def default_live_reader() -> list[dict[str, Any]]:
    """The seat's own ledgers, through the capability broker."""
    from shared.job_ledger_client import BrokerJobClient
    from shared.jobs_client import JobsBrokerClient

    lanes = JobsBrokerClient()
    return live_work(
        (
            lanes.list_all,
            lambda: list(lanes.demand_status().get("jobs") or []),
            lambda: list(lanes.drafting_status().get("jobs") or []),
        ),
        BrokerJobClient().list_all,
    )
