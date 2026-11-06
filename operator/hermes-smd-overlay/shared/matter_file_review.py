"""SMD review of a reply that carries matter files, before it is sent (ADR 0102).

WHY. The Captain wants to see what the Operator will send, and to whom, before
a client's files leave the seat. A seat authors that with
``matter_file_delivery.review: smd``. Every reply carrying matter files is then
written here instead of sent: who it goes to, the words, and for each file its
Smokeball file id, attachment name, page range, page count, size and sha256.
SMD reads it, and either approves it (the seat then sends exactly that, the
same broker-spool handles and hashes) or rejects it (it is discarded).

WHO DECIDES. The held row is written by the gateway (the agent uid). The
decision is a file written by ROOT under :data:`DECISIONS_DIR`, which the agent
uid cannot write: the gateway acts only on a decision file owned by uid 0 that
names the row's digest. So a turn cannot approve its own reply, and a decision
for a row that changed after SMD read it matches nothing.

USAGE, as root on the seat::

    python -m shared.matter_file_review list
    python -m shared.matter_file_review approve <id> <digest> [--job <id> | --no-job]
    python -m shared.matter_file_review reject <id> <digest>
    python -m shared.matter_file_review history           (every row, read-only)
    python -m shared.matter_file_review mark-smd <id>     (a hand reply SMD wrote)

A reply SMD writes by hand is held with ``hold(..., author="smd")``: its
release is SMD's own, so it never spends one of the Operator's
``matter_file_delivery.review_first`` releases.

WHICH JOB A RELEASE ANSWERS (2026-11-06). A reply that is a job's reply (a
completion SMD sends by hand) is recorded as that job's bound reply when it is
sent, so a later ``jobs rewake`` cannot email the requester a second
completion: hold it with ``job=`` or approve it with ``--job <id>``. An approve
of SMD's own reply that says neither ``--job`` nor ``--no-job`` is refused, and
so is any unmarked approve in a thread with a finished job whose reply is not
recorded (``shared.review_job``).

The gateway's held-reply sweeper thread acts on a decision within a minute.
A held row expires after :data:`TTL_SECONDS` (the broker spool keeps the files
that long); approving an expired row is refused and nothing is sent.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

# The row, its digest, the decision-file name and the released count are the
# records contract's (ADR 0102 amendment 4): the records lane writes rows as
# root with no turn, and this gateway sends them, so both build and read the
# same bytes from one copy (operator/connectors/_contract).
from records_contract.review_row import (
    AUTHOR_SMD,
    DECISIONS,
    DECISIONS_DIR_ENV,
    DEFAULT_DECISIONS_DIR,
    DEFAULT_REVIEW_DIR,
    DIGEST_RE,
    HOLD_REASON,
    ID_RE,
    JOB_NONE,
    REVIEW_DIR_ENV,
    STATUS_EXPIRED,
    STATUS_FAILED,
    STATUS_REJECTED,
    STATUS_REVIEW,
    STATUS_SENT,
    TTL_SECONDS,
    ReviewError,
    decision_file,
    decision_for,
    decisions_dir,
    digest,
    expired,
    hold_row,
    read_rows,
    released_count,
    review_dir,
    row_job,
    smd_authored,
    write_row,
)

from shared import review_job

__all__ = [
    "AUTHOR_SMD",
    "DECISIONS_DIR_ENV",
    "DEFAULT_DECISIONS_DIR",
    "DEFAULT_REVIEW_DIR",
    "HOLD_REASON",
    "JOB_NONE",
    "REVIEW_DIR_ENV",
    "STATUS_EXPIRED",
    "STATUS_FAILED",
    "STATUS_REJECTED",
    "STATUS_REVIEW",
    "STATUS_SENT",
    "TTL_SECONDS",
    "ReviewError",
    "decide",
    "decision",
    "decisions_dir",
    "describe",
    "digest",
    "expired",
    "finish",
    "hold",
    "main",
    "mark_smd",
    "released_count",
    "review_dir",
    "row_job",
    "rows",
]


def hold(
    *,
    message_id: str,
    to: str,
    text: str,
    html: str,
    batches: list[list[dict[str, Any]]],
    matter_id: str,
    session_id: str,
    reason: str = "",
    redirect: bool = False,
    now: float | None = None,
    directory: Path | None = None,
    author: str = "",
    job: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Write one held reply for review; return the row (with ``id`` and ``digest``).

    ``batches`` may be empty: a records reply from a turn that searched a file's
    pages is held whether or not it attached anything. ``reason`` is why the
    reply is here when it is not the seat's review setting (its facts did not
    check out, ``shared.matter_file_facts``); it is shown, not digested.
    ``redirect`` means ``to`` is a device's authored person, not the email's
    sender: the release aims the reply there (and the broker re-checks it).
    ``author="smd"`` marks a reply SMD wrote by hand: its release is not one of
    the Operator's ``review_first`` releases. ``job`` (``{"kind", "job_id"}``)
    marks the reply as that job's reply: the release records it as the job's
    bound reply, atomically with the send (2026-11-06)."""
    row = hold_row(
        message_id=message_id,
        to=to,
        text=text,
        html=html,
        batches=batches,
        matter_id=matter_id,
        session_id=session_id,
        reason=reason,
        redirect=redirect,
        now=now,
        author=author,
        job=job,
    )
    write_row(row, directory or review_dir())
    return row


def rows(directory: Path | None = None) -> list[dict[str, Any]]:
    """Every held row, the gateway's and the records lane's alike (a lane row's
    ``session_id`` is ``records:<job_id>``, which no hermes session owns)."""
    return read_rows(directory)


def finish(
    row: dict[str, Any], status: str, *, note: str = "", directory: Path | None = None
) -> None:
    """Move a row to a terminal status and drop its words (the hashes stay)."""
    write_row(
        {
            **row,
            "status": status,
            "decided_at": time.time(),
            "note": note[:300],
            "text": "",
            "html": "",
        },
        directory or review_dir(),
    )


def mark_smd(row_id: str, *, directory: Path | None = None) -> dict[str, Any]:
    """Record that SMD wrote row ``row_id`` by hand (``author: smd``), so its
    release stops counting toward ``review_first``. Root's correction for a
    hand reply held before rows carried an author. The digest is unchanged (the
    author is not digested) and the file keeps its owner and mode, so the
    gateway still reads it."""
    if not ID_RE.fullmatch(row_id or ""):
        raise ReviewError("give the row id exactly as `history` printed it")
    base = directory or review_dir()
    row = next((r for r in read_rows(base) if r.get("id") == row_id), None)
    if row is None:
        raise ReviewError("no review row has that id")
    path = base / f"{row_id}.json"
    st = os.stat(path)
    write_row({**row, "author": AUTHOR_SMD}, base)
    if os.geteuid() == 0:
        os.chown(path, st.st_uid, st.st_gid)
    os.chmod(path, st.st_mode & 0o777)
    return {**row, "author": AUTHOR_SMD}


def history_line(row: dict[str, Any]) -> str:
    """One row, every status, for finding a row to correct: no words, no files."""
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(row.get("created_at") or 0)))
    who = "SMD" if smd_authored(row) else "Operator"
    return (
        f"{row.get('id')}  {row.get('status'):<8}  held {when}  by {who:<8}  to {row.get('to')}  "
        f"matter {row.get('matter_id')}  session {row.get('session_id')}"
    )


def decision(row: dict[str, Any], *, directory: Path | None = None) -> str:
    """``approve`` / ``reject`` when ROOT decided this exact row, else ``""``."""
    return decision_for(directory, row) or ""


def describe(row: dict[str, Any]) -> str:
    """What SMD approves: every fact the digest covers, in words."""
    lines = [
        f"id:       {row.get('id')}   status: {row.get('status')}",
        f"held at:  {time.strftime('%Y-%m-%d %H:%M', time.localtime(float(row.get('created_at') or 0)))}",
        f"to:       {row.get('to')} (the reply is in-thread; the broker copies only the request's own firm staff)"
        + (
            " [the authored person for the device that sent the email]"
            if row.get("redirect")
            else ""
        ),
        f"answers:  graph message {row.get('message_id')}",
        f"matter:   {row.get('matter_id')}",
        *(
            ["written:  by SMD by hand (its release does not count toward review_first)"]
            if smd_authored(row)
            else []
        ),
        *([f"held because: {row.get('reason')}"] if row.get("reason") else []),
        *(
            [
                f"job:      the reply of {review_job.named(row_job(row) or {})} (recorded as its reply)"
            ]
            if row_job(row)
            else []
        ),
        "words (sent exactly as below):",
        "-" * 60,
        str(row.get("text") or "").rstrip("\n"),
        "-" * 60,
    ]
    batches = row.get("batches") or []
    if not batches:
        lines.append("files:    none (the reply goes as words alone)")
    for n, batch in enumerate(batches, start=1):
        lines.append(f"reply {n} of {len(batches)}:")
        for f in batch:
            lines.append(
                f"  file {f.get('name')}  smokeball {f.get('file_id')}  pages {f.get('pages') or 'all'} "
                f"({f.get('page_count')} p)  {f.get('size')} bytes  sha256 {f.get('sha256')}"
            )
    lines.append(f"digest:   {row.get('digest')}")
    return "\n".join(lines)


def _rewrite(row: dict[str, Any], base: Path) -> dict[str, Any]:
    """Root rewrites a held row in place, keeping the file's owner and mode so
    the gateway (the agent uid) still reads it."""
    path = base / f"{row['id']}.json"
    st = os.stat(path)
    write_row(row, base)
    if os.geteuid() == 0:
        os.chown(path, st.st_uid, st.st_gid)
    os.chmod(path, st.st_mode & 0o777)
    return row


def _with_job(
    row: dict[str, Any], job: dict[str, str] | None, no_job: bool, base: Path
) -> dict[str, Any]:
    """The row an approve releases, with which job it is the reply of settled
    (2026-11-06; ``shared.review_job`` says why). Refused when that is unknown."""
    if job is not None and no_job:
        raise ReviewError("--job and --no-job are two different answers; give one")
    current = row_job(row)
    if current is not None:
        if no_job or (job is not None and job != current):
            raise ReviewError(
                f"this reply is already held as the reply of {review_job.named(current)}"
            )
        return row
    if job is not None:
        if len(row.get("batches") or []) > 1:
            raise ReviewError(
                "a reply sent in several parts cannot be recorded as one job's reply; "
                "hold it again with its files in one part"
            )
        new = {**row, "job": dict(job)}
        new["digest"] = digest(new)
        return _rewrite(new, base)
    if no_job:
        return _rewrite({**row, "job": JOB_NONE}, base)
    if smd_authored(row):
        raise ReviewError(
            "this is SMD's own reply: say which job it answers (--job <id>) or that it answers none (--no-job)"
        )
    owed = review_job.unreplied_jobs(str(row.get("message_id") or ""))
    if owed:
        names = ", ".join(f"{review_job.named(j)} ({j['state']})" for j in owed)
        raise ReviewError(
            f"this reply's thread has a finished job with no reply recorded: {names}. "
            "If this is its reply, approve with --job <id>; if not, with --no-job"
        )
    return row


def decide(
    row_id: str,
    word: str,
    dig: str,
    *,
    directory: Path | None = None,
    reviews: Path | None = None,
    job: dict[str, str] | None = None,
    no_job: bool = False,
) -> Path:
    """Root writes the decision file the gateway acts on.

    An approve settles which job the reply is the reply of first
    (:func:`_with_job`): ``job`` records it as that job's reply when it is sent;
    ``no_job`` says it answers none. Naming a job changes the row's digest (the
    job is digested), so the decision names the new one."""
    if word not in DECISIONS:
        raise ReviewError("the decision is approve or reject")
    if not ID_RE.fullmatch(row_id or "") or not DIGEST_RE.fullmatch(dig or ""):
        raise ReviewError("give the row id and the digest exactly as `list` printed them")
    row = next((r for r in rows(reviews) if r.get("id") == row_id), None)
    if row is None or row.get("status") != STATUS_REVIEW:
        raise ReviewError("no reply is waiting for review under that id")
    if row.get("digest") != dig or digest(row) != dig:
        raise ReviewError("the digest does not match the held reply; list it again")
    if word == "approve" and expired(row):
        raise ReviewError(
            "that reply has expired (its files are gone from the broker's spool); nothing was sent"
        )
    if word == "approve":
        row = _with_job(row, job, no_job, reviews or review_dir())
        dig = str(row["digest"])
    return decision_file(directory, row_id, dig, word)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m shared.matter_file_review", description=__doc__
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("history")
    p = sub.add_parser("mark-smd")
    p.add_argument("row_id")
    for word in DECISIONS:
        p = sub.add_parser(word)
        p.add_argument("row_id")
        p.add_argument("digest")
        if word == "approve":
            p.add_argument(
                "--job",
                default="",
                help="the job this reply is the reply of (<kind>:<id>, or the bare id): "
                "recorded as its reply when sent",
            )
            p.add_argument("--no-job", action="store_true", help="this reply answers no job")
    args = parser.parse_args(argv)
    if args.cmd == "list":
        waiting = [r for r in rows() if r.get("status") == STATUS_REVIEW]
        for row in waiting:
            print(
                describe(row) + ("\n(EXPIRED: approve is refused)" if expired(row) else "") + "\n"
            )
        print(f"{len(waiting)} repl{'y' if len(waiting) == 1 else 'ies'} waiting for review.")
        print(
            f"{released_count()} of the Operator's replies released after review on this seat so far "
            "(review_first counts these; a reply SMD wrote itself does not)."
        )
        return 0
    if args.cmd == "history":
        for row in rows():
            print(history_line(row))
        print(f"{released_count()} counted toward review_first.")
        return 0
    if os.geteuid() != 0:
        print(
            "refused: a decision is root's (the gateway trusts only a root-owned decision file)",
            file=sys.stderr,
        )
        return 2
    if args.cmd == "mark-smd":
        try:
            mark_smd(args.row_id)
        except (ReviewError, OSError) as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        print(
            f"marked {args.row_id} as SMD's own reply; {released_count()} now counted toward review_first."
        )
        return 0
    try:
        job_text = str(getattr(args, "job", "") or "").strip()
        path = decide(
            args.row_id,
            args.cmd,
            args.digest,
            job=review_job.resolve_job(job_text) if job_text else None,
            no_job=bool(getattr(args, "no_job", False)),
        )
    except (ReviewError, OSError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {path}; the seat acts on it within a minute.")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a script on the seat
    sys.exit(main())
