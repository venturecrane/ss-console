"""Remove one person's data from a seat, and prove it is gone (stdlib only).

WHY (ss-console #3946, gate 8). On a guide seat a sales representative keeps
notes and leads in a store walled per person (``record_stores[].partition:
sender``). When SMD removes them, removing their enrollment row
(``sender_enrollment_cli remove``) used to be the whole of it: their notes,
their leads and everything the seat keeps keyed to their address stayed on the
volume. This module deletes what the seat keeps keyed to one address, then
reads the volume back and reports what is still there, so "removed" is a
reading of the world and not an echo of the statements that ran (Law 14).

WHAT IS DELETED, the PURGE SET, every place this seat's code keys data to a
person's address:

* their directory in every store the seat authors with ``partition: sender``,
  ``<store path>/<person_slug(address)>/``;
* their rows in the per-sender memory table, ``peer_preferences`` in
  ``agent-state.db`` on the volume (``peer_id`` is the lower-cased address);
* their rows in the request index ``requests.db`` on the volume: every request they
  sent (sender, subject, their words, the reply's words) and every row of the
  ``replies`` table answering one of those requests;
* their rows in the held-reply store ``held_replies.db`` on the volume (a reply held
  for them, its text until it is released);
* their rows in the help-pointer state ``smd/help_state.db`` on the volume, keyed by
  a salted hash of their address (the salt is in that file);
* every sent-line label file (``.smd/sent_lines/<ref>.json``) whose recorded
  recipients include them;
* every scheduled-send envelope (``.smd/pre_run/*.dispatch*.json``, pending or
  consumed) that names them as a recipient: the entries addressed to them are
  removed, and a file left with none is deleted.

Rows are deleted with ``PRAGMA secure_delete`` on and the write-ahead log
truncated afterwards, so the bytes are overwritten rather than left on a free
page where a raw read of the file would still find them.

WHAT IS KEPT, the NAMED-RETAINED SET (not deleted here, by decision; the purge
report and the probe never claim them):

* the seat's session transcripts, Hermes' ``state.db`` and ``sessions/`` under
  each profile home: every turn's messages, tool arguments and tool results,
  which include full copies of what the person sent and what was stored;
* the mail itself, at the mail vendor (their messages and the seat's replies);
* logs (the seat's log stream and Sentry) and the Fly volume's snapshots;
* the audit ledger (``audit/`` on the volume): append-only and hash-chained; it
  holds a hash of an address, never the text;
* the enrollment store's attempt rows (``private/codes.db``): a hash of the
  sender, pruned after 48 hours;
* Hermes' tool-result spillover files (``cache/spillover/``), which this code
  cannot attribute to a person;
* sent-line files and request rows written before this module, or after their
  text custody nulled the sender (seven days for the request index), which
  carry nothing that names the person.

WHO RUNS IT. ``sender_enrollment_cli remove ADDRESS`` and ``purge ADDRESS`` on
the seat, as root: a child process drops to the broker uid and removes the
enrollment row (the broker's store), and the parent, still root, deletes the
data on the agent's volume (the volume root is 0700 to every uid but the agent and
root). The row goes first, so a turn of theirs still in flight is refused by the
partitioned store's roster check before the directory is deleted, and cannot
recreate it afterwards. ``purge --probe ADDRESS`` is read-only.

STDLIB ONLY, so it runs from either Python on the seat. ``person_slug`` is a
copy of the overlay's ``shared/person_prefs.py``; a parity test pins the two
equal (``workspace_broker/tests/test_person_purge.py``).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from email.utils import parseaddr
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# The slug: a copy of the overlay's shared/person_prefs.py, pinned equal by test.
# ---------------------------------------------------------------------------

_SLUG_READABLE_MAX = 40


def normalize_person_address(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    norm = value.strip().lower()
    if not norm or norm.count("@") != 1 or norm.startswith("@"):
        return None
    local, _, domain = norm.partition("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        return None
    return norm


def person_slug(address: str) -> str:
    norm = normalize_person_address(address)
    if norm is None:
        raise ValueError(f"person_slug: not a valid person address: {address!r}")
    out: list[str] = []
    for ch in norm:
        if ch.isascii() and (ch.islower() or ch.isdigit()):
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    readable = "".join(out).strip("-")[:_SLUG_READABLE_MAX].rstrip("-")
    digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:8]
    return f"{readable}-{digest}" if readable else digest


def recipient_digest(address: str) -> str:
    """The digest ``shared/sent_lines.py`` records for each recipient."""
    return hashlib.sha256(address.strip().lower().encode("utf-8")).hexdigest()


def _bare(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return parseaddr(value)[1].strip().lower() or None


# ---------------------------------------------------------------------------
# Where a seat keeps person-keyed data
# ---------------------------------------------------------------------------


@dataclass
class PurgeTargets:
    """Every location the purge set covers on one seat."""

    data_root: Path
    partition_roots: list[Path] = field(default_factory=list)
    #: Why the partitioned stores could not be listed, when they could not.
    #: A probe that could not look must never read as one that found nothing.
    config_error: str = ""

    @property
    def agent_state_db(self) -> Path:
        return self.data_root / "agent-state.db"

    @property
    def request_index_db(self) -> Path:
        return self.data_root / "requests.db"

    @property
    def held_replies_db(self) -> Path:
        return self.data_root / "held_replies.db"

    @property
    def help_state_db(self) -> Path:
        return self.data_root / "smd" / "help_state.db"

    def homes(self) -> list[Path]:
        """The volume root and every profile home: where ``.smd`` lives."""
        out = [self.data_root]
        profiles = self.data_root / "profiles"
        if profiles.is_dir():
            out.extend(sorted(p for p in profiles.iterdir() if p.is_dir() and not p.is_symlink()))
        return out

    def sent_lines_files(self) -> list[Path]:
        return [f for h in self.homes() for f in sorted((h / ".smd" / "sent_lines").glob("*.json"))]

    def envelope_files(self) -> list[Path]:
        return [f for h in self.homes() for f in sorted((h / ".smd" / "pre_run").glob("*.dispatch*.json"))]


@dataclass
class Finding:
    """One place something of the person's is (or, after a purge, still is)."""

    layer: str
    location: str
    count: int

    def line(self) -> str:
        return f"{self.layer}: {self.count} at {self.location}"


@dataclass
class PurgeReport:
    removed: list[Finding]
    remaining: list[Finding]

    @property
    def clean(self) -> bool:
        return not self.remaining


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------


def _owner(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_uid, st.st_gid


def _restore_owner(db: Path, owner: tuple[int, int] | None) -> None:
    """Run as root, SQLite creates its journal and WAL files as root; the agent
    could then not open its own database. Give every sidecar back."""
    if owner is None or os.geteuid() != 0:
        return
    for suffix in ("", "-wal", "-shm", "-journal"):
        side = Path(str(db) + suffix)
        try:
            if side.exists() and (side.stat().st_uid, side.stat().st_gid) != owner:
                os.chown(side, *owner)
        except OSError:
            pass


def _connect_ro(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


#: Every table this module names. A table name reaches SQL only through
#: :func:`_known`, which refuses anything not listed here.
KNOWN_TABLES: frozenset[str] = frozenset(
    {
        "peer_preferences",
        "requests",
        "replies",
        "held_replies",
        "help_meta",
        "help_senders",
        "help_threads",
        "help_events",
    }
)
#: Every statement key this module runs (a table, or a table's older shape).
_STATEMENT_KEYS: frozenset[str] = KNOWN_TABLES | {"held_replies_old"}


def _known(name: str, allowed: frozenset[str] = KNOWN_TABLES) -> str:
    """``name`` if it is on the allowlist; raises ``ValueError`` otherwise. The
    one gate every table name passes before it selects a statement."""
    if not isinstance(name, str) or name not in allowed:
        raise ValueError(f"person_purge: {name!r} is not a table this purge knows")
    return name


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute("SELECT name FROM pragma_table_info(?)", (_known(table),))
    return {r[0] for r in rows}


#: Every statement this module runs, as literals: no SQL is ever composed, and
#: a statement is chosen only by a name :func:`_known` accepted.
_SELECT = {
    "peer_preferences": "SELECT rowid, peer_id FROM peer_preferences",
    "requests": "SELECT rowid, sender, vendor_message_id, internet_message_id FROM requests",
    "replies": "SELECT rowid, in_reply_to FROM replies",
    "held_replies": "SELECT rowid, sender, reply_to FROM held_replies",
    "held_replies_old": "SELECT rowid, sender, '' FROM held_replies",
    "help_senders": "SELECT rowid FROM help_senders WHERE sender_hash = ?",
    "help_threads": "SELECT rowid FROM help_threads WHERE sender_hash = ?",
    "help_events": "SELECT rowid FROM help_events WHERE sender_hash = ?",
}
_DELETE = {
    "peer_preferences": "DELETE FROM peer_preferences WHERE rowid = ?",
    "requests": "DELETE FROM requests WHERE rowid = ?",
    "replies": "DELETE FROM replies WHERE rowid = ?",
    "held_replies": "DELETE FROM held_replies WHERE rowid = ?",
    "help_senders": "DELETE FROM help_senders WHERE rowid = ?",
    "help_threads": "DELETE FROM help_threads WHERE rowid = ?",
    "help_events": "DELETE FROM help_events WHERE rowid = ?",
}


# ---------------------------------------------------------------------------
# One function per layer: (match rows) -> ids. Shared by purge and probe.
# ---------------------------------------------------------------------------


def _peer_rows(conn: sqlite3.Connection, address: str) -> list[Any]:
    if "peer_preferences" not in _tables(conn):
        return []
    return [
        row[0]
        for row in conn.execute(_SELECT[_known("peer_preferences")])
        if isinstance(row[1], str) and row[1].strip().lower() == address
    ]


def _request_rows(conn: sqlite3.Connection, address: str) -> tuple[list[Any], list[Any]]:
    """``(request rowids, reply rowids)`` belonging to ``address``."""
    tables = _tables(conn)
    if "requests" not in tables:
        return [], []
    rows = conn.execute(_SELECT[_known("requests")]).fetchall()
    mine = [r for r in rows if _bare(r[1]) == address]
    ids = {v for r in mine for v in r[2:] if isinstance(v, str) and v}
    replies: list[Any] = []
    if "replies" in tables and ids:
        replies = [row[0] for row in conn.execute(_SELECT[_known("replies")]) if row[1] in ids]
    return [r[0] for r in mine], replies


def _held_rows(conn: sqlite3.Connection, address: str) -> list[Any]:
    if "held_replies" not in _tables(conn):
        return []
    query = "held_replies" if "reply_to" in _columns(conn, "held_replies") else "held_replies_old"
    return [
        row[0]
        for row in conn.execute(_SELECT[_known(query, _STATEMENT_KEYS)])
        if _bare(row[1]) == address or _bare(row[2]) == address
    ]


def _help_key(conn: sqlite3.Connection, address: str) -> str | None:
    if "help_meta" not in _tables(conn):
        return None
    row = conn.execute("SELECT value FROM help_meta WHERE key = 'salt'").fetchone()
    if not row or not isinstance(row[0], str):
        return None
    return hashlib.sha256(f"{row[0]}\x00sender\x00{address}".encode()).hexdigest()


_HELP_TABLES = ("help_senders", "help_threads", "help_events")


def _help_rows(conn: sqlite3.Connection, address: str) -> dict[str, list[Any]]:
    key = _help_key(conn, address)
    if key is None:
        return {}
    present = _tables(conn)
    return {t: [r[0] for r in conn.execute(_SELECT[_known(t)], (key,))] for t in _HELP_TABLES if t in present}


def _envelope_mentions(entry: Any, address: str) -> bool:
    if not isinstance(entry, dict):
        return False
    people: list[Any] = []
    for key in ("recipients", "to", "cc", "bcc"):
        value = entry.get(key)
        if isinstance(value, list):
            people.extend(value)
        elif isinstance(value, str):
            people.append(value)
    return any(_bare(p) == address for p in people)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Probe (read-only) and purge
# ---------------------------------------------------------------------------


def _sql_findings(targets: PurgeTargets, address: str) -> list[Finding]:
    found: list[Finding] = []
    checks = (
        (targets.agent_state_db, "per-sender memory (peer_preferences)", _peer_rows),
        (targets.held_replies_db, "held replies (held_replies)", _held_rows),
    )
    for db, layer, rows_of in checks:
        if not db.exists():
            continue
        with _closing(_connect_ro(db)) as conn:
            n = len(rows_of(conn, address))
        if n:
            found.append(Finding(layer, str(db), n))
    if targets.request_index_db.exists():
        with _closing(_connect_ro(targets.request_index_db)) as conn:
            requests, replies = _request_rows(conn, address)
        if requests:
            found.append(Finding("request index (requests)", str(targets.request_index_db), len(requests)))
        if replies:
            found.append(Finding("request index (replies)", str(targets.request_index_db), len(replies)))
    if targets.help_state_db.exists():
        with _closing(_connect_ro(targets.help_state_db)) as conn:
            for table, ids in _help_rows(conn, address).items():
                if ids:
                    found.append(Finding(f"help state ({table})", str(targets.help_state_db), len(ids)))
    return found


def _file_findings(targets: PurgeTargets, address: str) -> list[Finding]:
    found: list[Finding] = []
    for root in targets.partition_roots:
        part = root / person_slug(address)
        if part.exists() or part.is_symlink():
            n = sum(1 for p in part.rglob("*") if p.is_file()) if part.is_dir() else 1
            found.append(Finding("partitioned store directory", str(part), max(n, 1)))
    digest = recipient_digest(address)
    for path in targets.sent_lines_files():
        raw = _read_json(path)
        if isinstance(raw, dict) and digest in (raw.get("to_sha256") or []):
            found.append(Finding("sent-line labels", str(path), 1))
    for path in targets.envelope_files():
        raw = _read_json(path)
        entries = raw.get("dispatches") if isinstance(raw, dict) else None
        n = sum(1 for e in entries or [] if _envelope_mentions(e, address))
        if n:
            found.append(Finding("scheduled-send envelope", str(path), n))
    return found


def probe(address: str, targets: PurgeTargets) -> list[Finding]:
    """Everything of ``address``'s the purge set covers that is on the volume
    now. READ-ONLY. A location that cannot be read is a finding, never an
    absence: the probe can only report clean having looked everywhere."""
    norm = normalize_person_address(address)
    if norm is None:
        return [Finding("refused", f"not a person's address: {address!r}", 1)]
    found: list[Finding] = []
    if targets.config_error:
        found.append(Finding("could not list the partitioned stores", targets.config_error, 1))
    try:
        found.extend(_file_findings(targets, norm))
    except OSError as exc:
        found.append(Finding("could not read the volume", str(exc), 1))
    try:
        found.extend(_sql_findings(targets, norm))
    except sqlite3.Error as exc:
        found.append(Finding("could not read a database", str(exc), 1))
    return found


class _closing:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def __enter__(self) -> sqlite3.Connection:
        return self.conn

    def __exit__(self, *_exc: Any) -> None:
        self.conn.close()


def _delete_rowids(conn: sqlite3.Connection, table: str, ids: Iterable[Any]) -> int:
    ids = list(ids)
    for rowid in ids:
        conn.execute(_DELETE[_known(table)], (rowid,))
    return len(ids)


def _purge_db(db: Path, work: Any) -> list[Finding]:
    """Open ``db`` for writing with secure delete on, run ``work(conn)`` (which
    returns findings for what it deleted), commit, truncate the WAL and give
    every sidecar back to the file's owner."""
    if not db.exists():
        return []
    owner = _owner(db)
    conn = sqlite3.connect(str(db), timeout=30)
    try:
        conn.execute("PRAGMA secure_delete = ON")
        with conn:
            removed = work(conn)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
        _restore_owner(db, owner)
    return removed


def _purge_sql(targets: PurgeTargets, address: str) -> list[Finding]:
    removed: list[Finding] = []

    def peers(conn: sqlite3.Connection) -> list[Finding]:
        n = _delete_rowids(conn, "peer_preferences", _peer_rows(conn, address))
        return [Finding("per-sender memory (peer_preferences)", str(targets.agent_state_db), n)] if n else []

    def held(conn: sqlite3.Connection) -> list[Finding]:
        n = _delete_rowids(conn, "held_replies", _held_rows(conn, address))
        return [Finding("held replies (held_replies)", str(targets.held_replies_db), n)] if n else []

    def requests(conn: sqlite3.Connection) -> list[Finding]:
        req, rep = _request_rows(conn, address)
        out = []
        if rep:
            out.append(
                Finding("request index (replies)", str(targets.request_index_db), _delete_rowids(conn, "replies", rep))
            )
        if req:
            out.append(
                Finding(
                    "request index (requests)", str(targets.request_index_db), _delete_rowids(conn, "requests", req)
                )
            )
        return out

    def help_state(conn: sqlite3.Connection) -> list[Finding]:
        return [
            Finding(f"help state ({t})", str(targets.help_state_db), _delete_rowids(conn, t, ids))
            for t, ids in _help_rows(conn, address).items()
            if ids
        ]

    removed += _purge_db(targets.agent_state_db, peers)
    removed += _purge_db(targets.held_replies_db, held)
    removed += _purge_db(targets.request_index_db, requests)
    removed += _purge_db(targets.help_state_db, help_state)
    return removed


def _rewrite_json(path: Path, payload: Any) -> None:
    owner = _owner(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".purge-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.chmod(tmp, path.stat().st_mode & 0o777)
        if owner is not None and os.geteuid() == 0:
            os.chown(tmp, *owner)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _purge_files(targets: PurgeTargets, address: str) -> list[Finding]:
    removed: list[Finding] = []
    for root in targets.partition_roots:
        part = root / person_slug(address)
        if part.is_symlink():
            part.unlink()
            removed.append(Finding("partitioned store directory (a link)", str(part), 1))
        elif part.is_dir():
            n = sum(1 for p in part.rglob("*") if p.is_file())
            shutil.rmtree(part)
            removed.append(Finding("partitioned store directory", str(part), n))
    digest = recipient_digest(address)
    for path in targets.sent_lines_files():
        raw = _read_json(path)
        if isinstance(raw, dict) and digest in (raw.get("to_sha256") or []):
            path.unlink(missing_ok=True)
            removed.append(Finding("sent-line labels", str(path), 1))
    for path in targets.envelope_files():
        raw = _read_json(path)
        entries = raw.get("dispatches") if isinstance(raw, dict) else None
        if not isinstance(entries, list):
            continue
        kept = [e for e in entries if not _envelope_mentions(e, address)]
        if len(kept) == len(entries):
            continue
        if kept:
            _rewrite_json(path, {**raw, "dispatches": kept})
        else:
            path.unlink(missing_ok=True)
        removed.append(Finding("scheduled-send envelope", str(path), len(entries) - len(kept)))
    return removed


def purge(address: str, targets: PurgeTargets) -> PurgeReport:
    """Delete ``address``'s data in every purge-set location, then probe.

    The report's ``remaining`` is a fresh read of the volume after the
    deletes, never a restatement of them. An address that was never enrolled,
    or was removed long ago, is purged the same way: orphan data is data."""
    norm = normalize_person_address(address)
    if norm is None:
        return PurgeReport([], [Finding("refused", f"not a person's address: {address!r}", 1)])
    removed = _purge_files(targets, norm)
    removed += _purge_sql(targets, norm)
    return PurgeReport(removed=removed, remaining=probe(norm, targets))


__all__ = [
    "Finding",
    "PurgeReport",
    "PurgeTargets",
    "normalize_person_address",
    "person_slug",
    "probe",
    "purge",
    "recipient_digest",
]
