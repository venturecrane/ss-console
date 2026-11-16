"""The morning list, rendered by code only.

Subject ``Mailbox: N need you`` or ``Mailbox: nothing needs you``. Sections in
a fixed order: what needs him (each decision with its recommendation and
whether a draft is in the thread; each SMD Ops line his to act on; with
``classify`` off, each person waiting on a reply), the count still open from
earlier lists (counted by code, so a quiet line never hides an open item), what
was filed and archived (by sender and subject), what could not be read, and
what happened to SMD Ops mail. A quiet day says so.

Every line is header text or the model's validated, figure-checked value
(``mailbox_keeper_classify.py``), cleaned to one line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from .mailbox_keeper import _clean

#: Longest list of could-not-read threads named one by one.
UNREAD_NAMED = 15


@dataclass(frozen=True)
class Need:
    title: str
    recommend: str = ""
    #: "made" (Gmail returned a draft id), "exists" (a draft was already there), or "".
    draft: str = ""


@dataclass
class Digest:
    needs: list[Need] = field(default_factory=list)
    still_open: int = 0
    still_open_since: str = ""
    receipts: list[str] = field(default_factory=list)
    receipts_label: str = ""
    archived: list[str] = field(default_factory=list)
    unread: list[str] = field(default_factory=list)
    stopped: str = ""
    ops_label: str = ""
    ops_labelled: int = 0
    ops_trashed: int = 0
    ops_failed: int = 0
    ops_unverified: int = 0
    ops_unlabelled: int = 0


def local_day(ms: int, zone: str) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=ZoneInfo(zone)).strftime("%b %d").replace(" 0", " ")


def local_time(ms: int, zone: str) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=ZoneInfo(zone)).strftime("%b %d, %I:%M %p").replace(" 0", " ")


def ops_lines(pending: list[dict], zone: str) -> list[str]:
    """The SMD Ops needs-you lines as the list shows them: the same line said
    several times is one line with its count and latest time, and only the
    newest triage report is kept, because each one supersedes the last (the
    first labelled list on smd-office would otherwise have carried 32 lines,
    12 of them one sentence; 2026-11-16 dry run)."""
    triage = [p for p in pending if str(p.get("kind", "")).startswith("triage")]
    newest = max(triage, key=lambda p: int(p.get("ms") or 0)) if triage else None
    groups: dict[str, list[int]] = {}
    for p in pending:
        if p in triage and p is not newest:
            continue
        groups.setdefault(str(p.get("line") or ""), []).append(int(p.get("ms") or 0))
    out = []
    for line, stamps in sorted(groups.items(), key=lambda kv: -max(kv[1])):
        if len(stamps) > 1:
            out.append(f"{line} ({len(stamps)} times, latest {local_time(max(stamps), zone)})")
        else:
            out.append(line)
    return out


def who(name: str, address: str) -> str:
    """'Name (domain)' when the header names someone, else the address."""
    if name:
        return f"{_clean(name, 60)} ({address.partition('@')[2]})"
    return address


def _need_lines(needs: list[Need]) -> list[str]:
    lines = [f"Needs you ({len(needs)}):"]
    for n, need in enumerate(needs, 1):
        lines.append(f"{n}. {need.title}")
        tail = {"made": " Draft is in the thread.", "exists": " A draft is already in the thread."}.get(need.draft, "")
        if need.recommend:
            lines.append(f"   Recommend: {need.recommend.rstrip('.')}.{tail}")
    return lines


def _filed_line(d: Digest) -> str:
    parts = []
    if d.receipts:
        parts.append(f"Filed: receipts {', '.join(d.receipts)} (label {d.receipts_label}).")
    if d.archived:
        parts.append(f"Archived: {', '.join(d.archived)}.")
    if parts:
        parts.append("A thread that changed before it was filed stays in the inbox.")
    return " ".join(parts)


def _unread_line(d: Digest) -> str:
    if not d.unread:
        return ""
    named = "; ".join(d.unread[:UNREAD_NAMED])
    more = f"; and {len(d.unread) - UNREAD_NAMED} more" if len(d.unread) > UNREAD_NAMED else ""
    stop = f" Reading stopped early ({d.stopped})." if d.stopped else ""
    return f"Could not read {len(d.unread)}, left in inbox: {named}{more}.{stop}"


def _ops_line(d: Digest) -> str:
    parts = []
    if d.ops_labelled:
        parts.append(f"{d.ops_labelled} filed under the {d.ops_label} label")
    parts.append(f"{d.ops_trashed} settled alerts trashed")
    if d.ops_unlabelled:
        parts.append(f"{d.ops_unlabelled} left in the inbox (no label is authored for them)")
    if d.ops_failed:
        parts.append(f"{d.ops_failed} could not be filed or trashed and are still in the inbox")
    if d.ops_unverified:
        parts.append(f"{d.ops_unverified} failed the sender check and are left in the inbox")
    return "SMD Ops: " + ", ".join(parts) + "."


def render(d: Digest) -> tuple[str, str]:
    """(subject, body)."""
    count = len(d.needs)
    subject = f"Mailbox: {count} need{'s' if count == 1 else ''} you" if count else "Mailbox: nothing needs you"
    lines = _need_lines(d.needs) if d.needs else ["Nothing needs you."]
    lines.append("")
    if d.still_open:
        since = f" (first listed {d.still_open_since})" if d.still_open_since else ""
        lines.append(f"Still open from earlier: {d.still_open}{since}.")
    for line in (_filed_line(d), _unread_line(d), _ops_line(d)):
        if line:
            lines.append(line)
    return subject, "\n".join(lines).rstrip() + "\n"


__all__ = ["Digest", "Need", "local_day", "local_time", "render", "who"]
