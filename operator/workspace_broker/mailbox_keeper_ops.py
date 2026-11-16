"""Which SMD Ops mail needs the owner, read from the subject grammar SMD writes.

Every SMD Ops thread leaves the inbox under the authored ``alert_label``
(``mailbox_keeper.py`` Rule 1B), so the kinds that are HIS to act on must be
named in the morning list, or labelling them would hide them. The handbook's
"What arrives in the inbox" table (``docs/handbook/administrators-week.md``)
marks them "You"; each row below is the subject one emitter writes, and
``tests/test_mailbox_keeper_classify.py`` builds each subject the way its
emitter builds it:

* ``[SMD Ops] Triage: <H> handled, <N> need you`` (N > 0), and the triage's own
  ``[SMD Ops] Triage: report (format not followed)``
  (``operator/ops-machine/bin/ops-triage``, ``operator/ops-machine/TRIAGE.md``);
* ``[SMD Ops] <seat>: NO REPLY after <N>m: ...`` and
  ``[SMD Ops] <seat>: NEW KIND OF ASK, job waiting|first use: ...``
  (``src/lib/operator/request-card-email.ts`` ``cardSubject``);
* ``[SMD Ops] <seat>: the <name> sign-in is dead``
  (``operator/bin/lib/work_health_auth.py``) and
  ``[SMD Ops] <seat>: the <name> sign-in is about to expire``, with a reminder's
  ``still open:`` (``workers/fleet-alerts/src/alert-words.ts``,
  ``transition-email.ts``);
* ``[SMD Ops] <N> cost anomaly|anomalies on <date>``
  (``workers/cost-anomaly/src/notify.ts``);
* the never-clears alerts (``mailbox_keeper.NEVER_CLEARS``: a scheduled message
  or a reply did not go out, a limit stopped the work), which have no recovery
  email (``send-refused.ts``, ``shortfalls.ts``).

A recovery (the first paragraph is ``mailbox_keeper.RECOVERY_PARAGRAPH``) never
needs him. No model reads SMD Ops mail. Pure.
"""

from __future__ import annotations

import re

from .mailbox_keeper import RECOVERY_PARAGRAPH, _clean, never_clears

_REPLY_PREFIX = re.compile(r"^(?:re:\s*)+", re.IGNORECASE)
_OPS = "[SMD Ops] "
TRIAGE_NEEDS = re.compile(r"^\[SMD Ops\] Triage: .*?\b(\d+) needs? you\b")
TRIAGE_UNREADABLE = re.compile(r"^\[SMD Ops\] Triage: report \(format not followed\)$")
#: (kind, subject pattern). The seat slug never holds a colon.
KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("no_reply", re.compile(r"^\[SMD Ops\] [^:]+: NO REPLY after \d+m\b")),
    ("new_kind_of_ask", re.compile(r"^\[SMD Ops\] [^:]+: NEW KIND OF ASK, ")),
    ("sign_in_dead", re.compile(r"^\[SMD Ops\] [^:]+: the .+ sign-in is dead$")),
    ("sign_in_expiring", re.compile(r"^\[SMD Ops\] [^:]+: (?:still open: )?the .+ sign-in is about to expire$")),
    ("cost_anomaly", re.compile(r"^\[SMD Ops\] \d+ cost anomal(?:y|ies) on \S+$")),
)


def needs_you(subject: str, first_paragraph: str) -> tuple[str, str] | None:
    """(kind, the line the morning list shows) when this SMD Ops message is the
    owner's to act on, else None."""
    if first_paragraph == RECOVERY_PARAGRAPH:
        return None
    text = _clean(_REPLY_PREFIX.sub("", (subject or "").strip()), 200)
    found = TRIAGE_NEEDS.match(text)
    if found:
        count = int(found.group(1))
        return ("triage", f"Triage says {count} need you") if count > 0 else None
    if TRIAGE_UNREADABLE.match(text):
        return "triage_unreadable", "Triage sent a report it could not summarize; open it"
    if never_clears(text):
        return "never_clears", text[len(_OPS) :] if text.startswith(_OPS) else text
    for kind, pattern in KINDS:
        if pattern.match(text):
            return kind, text[len(_OPS) :]
    return None


__all__ = ["KINDS", "TRIAGE_NEEDS", "TRIAGE_UNREADABLE", "needs_you"]
