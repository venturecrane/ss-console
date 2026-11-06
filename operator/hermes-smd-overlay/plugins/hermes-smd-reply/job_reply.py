"""The seat sends a drafting job's completion reply in the broker's words.

THE LIVE FAILURE (A&P, 2026-11-05, a discovery_set). The job filed its
documents in the matter. The completion turn did not send the delivery wording
the broker renders from the job's record; the model wrote its own text, which
the identifier check refused twice (an unverified date), then saved three
drafts the content check held as content_sensitive. None was threaded to the
attorney's email, and no alert reached SMD. The attorney heard nothing until
SMD sent a reply by hand. An earlier job that day had gone out in the broker's
words: whether the attorney heard depended on what the model chose to write.

WHAT THIS IS. For a job kind in :data:`CODE_SENT_KINDS` the broker words the
reply (ss-console ``workspace_broker/reply_text.CODE_SENT_KINDS``: the bind
carries it as ``fixed_wording`` on every seat, and the send refuses any other
words). This module sends exactly those words, and nothing the model wrote:

* when the model drafts in its bound turn, the relay hands the draft here and
  the broker's words go instead of the draft's;
* when the turn ends with the reply unsent (every draft refused upstream, or no
  draft at all), the end-of-turn worker sends them.

Either way the seat binds the job itself (``reply_bind``'s broker verb), so a
model that never bound still has its reply sent.

WHY THE TEXT IS NOT RE-GATED. As for ``act_delivery``: every floor the relay
applies (identifier, fabrication, content, checklist, matter) exists to catch
what the MODEL composed. These words are rendered by the broker from the job's
own ledger row, the recipient is the broker's, and the broker checks the words
again at the send.

ANY OTHER ENDING IS AN ALERT. A bind the broker refused (other than "already
answered", which is the success, and "failed on SMD's side", which already
pages through the job's own failure), no single wording, a broker that could
not be reached, or a send that failed: each writes one ``REPLY_FAILED`` row
(``job_reply_undelivered: ...``) naming the job, which the heartbeat pages as
a shortfall (``reply_failed:job_reply_undelivered``), and an error-level
Sentry event tagged with the job.
"""

from __future__ import annotations

import html as html_lib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: The job kinds whose reply the seat sends in code (the broker's set).
CODE_SENT_KINDS = frozenset({"drafting_job"})
#: The REPLY_FAILED reason prefix the heartbeat pages on.
UNDELIVERED = "job_reply_undelivered"
#: Broker refusal codes that are not an alert (reply_binding.BindingRefused).
_DONE_CODES = frozenset({"already_replied"})
_SMDS_ALREADY = frozenset({"job_failed"})


@dataclass(frozen=True)
class Outcome:
    """What one delivery did: ``sent``, ``done`` (already answered),
    ``not_owed`` (failed job, or not a code-sent wake) or ``failed``."""

    word: str
    job_id: str = ""
    graph_message_id: str = ""
    why: str = ""


class JobReplies:
    """In-process once-only per job: the relay's inline send and both
    end-of-turn hooks may all arrive for one job. The broker's durable claim is
    the rule across restarts; this only stops a second bind round trip."""

    def __init__(self, max_jobs: int = 512) -> None:
        self._max = max_jobs
        self._settled: dict[str, str] = {}
        self._lock = threading.Lock()

    def settled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._settled

    def settle(self, job_id: str, word: str) -> None:
        with self._lock:
            self._settled[job_id] = word
            while len(self._settled) > self._max:
                self._settled.pop(next(iter(self._settled)))

    def _reset_for_tests(self) -> None:
        with self._lock:
            self._settled.clear()


def owed(wake: tuple[str, str] | None, scheduled: bool) -> tuple[str, str] | None:
    """(kind, job id) when this turn is a code-sent job's request wake."""
    if not wake or scheduled or wake[0] not in CODE_SENT_KINDS:
        return None
    return wake


def plain_html(text: str) -> str:
    """The words as HTML that reads back as exactly the words: each paragraph
    escaped, line breaks kept. The Markdown renderer the relay uses could turn
    a file name's underscores into emphasis, and the broker would then refuse
    the HTML part as other words."""
    paragraphs = [p for p in text.replace("\r\n", "\n").split("\n\n") if p.strip()]
    return "".join(f"<p>{html_lib.escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs)


def _single_text(verdict: dict[str, Any]) -> str:
    texts = verdict.get("fixed_wording")
    if (
        isinstance(texts, list)
        and len(texts) == 1
        and isinstance(texts[0], str)
        and texts[0].strip()
    ):
        return texts[0]
    return ""


def deliver(
    wake: tuple[str, str],
    *,
    bind: Callable[[dict[str, str]], dict[str, Any]],
    send: Callable[[dict[str, str], str, str, str], str],
    on_sent: Callable[[Outcome, str, str], None],
    on_failed: Callable[[Outcome], None],
) -> Outcome:
    """Bind ``wake``'s job, send the broker's one text, report the ending.

    ``send(request, sender, graph_message_id, text)`` returns the sent id or
    raises. ``on_sent(outcome, text, sent_id)`` and ``on_failed(outcome)``
    write the rows. Never raises."""
    kind, job_id = wake
    request = {"kind": kind, "job_id": job_id}
    try:
        verdict = bind(request)
    except Exception as exc:  # noqa: BLE001 - could not ask is never "nothing owed"
        out = Outcome("failed", job_id, why=f"the binding could not be checked: {exc}")
        on_failed(out)
        return out
    verdict = verdict if isinstance(verdict, dict) else {}
    if verdict.get("bound") is not True:
        code = str(verdict.get("code") or "")
        if code in _DONE_CODES:
            return Outcome("done", job_id)
        if code in _SMDS_ALREADY:
            return Outcome("not_owed", job_id)
        out = Outcome(
            "failed",
            job_id,
            why=f"the broker refused the binding: {verdict.get('reason') or 'refused'}",
        )
        on_failed(out)
        return out
    graph_id = str(verdict.get("graph_message_id") or "")
    sender = str(verdict.get("sender") or "")
    text = _single_text(verdict)
    if not text:
        out = Outcome(
            "failed", job_id, graph_id, "the broker gave no single wording for this reply"
        )
        on_failed(out)
        return out
    try:
        sent_id = send(request, sender, graph_id, text)
    except Exception as exc:  # noqa: BLE001 - every send failure is reported
        out = Outcome("failed", job_id, graph_id, f"the send failed: {type(exc).__name__}")
        on_failed(out)
        return out
    out = Outcome("sent", job_id, graph_id)
    on_sent(out, text, sent_id)
    return out


__all__ = [
    "CODE_SENT_KINDS",
    "UNDELIVERED",
    "JobReplies",
    "Outcome",
    "deliver",
    "owed",
    "plain_html",
]
