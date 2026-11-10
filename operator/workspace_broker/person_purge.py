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
  consumed) that names them as a recipient or in its words (the weekly
  representative progress report to SMD's administrators names each
  representative in its body): those entries are removed, and a file left
  with none is deleted;
* their progress record in the training progress store (``rep-progress``),
  which is a store walled per person and so is covered by the first item;
* their rows in the per-sender daily count ``smd/sender_daily.db`` on the
  volume (``sender_enrollment.daily_limit``), keyed by the sha256 of their
  address (:func:`sender_hash`).

``person_orphans.py`` lists the walled-store directories that belong to nobody
listed now, by slug and count, through the same no-follow helpers.

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

COULD NOT LOOK IS NEVER ABSENT. Every "is it there" question goes through
``nofollow.look``, which calls ``os.lstat`` itself: only ``FileNotFoundError`` means
absent, and any other error (a permission error above all) makes that location
a finding of its own, "could not examine". ``Path.exists`` is not used: on
Python 3.14 it answers False for a path it was not allowed to look at, which is
how a run as the wrong uid once reported clean with everything still there. A
purge first examines every location and removes NOTHING unless all of them
could be examined; a probe that could not examine a location is never clean.

NOTHING HERE FOLLOWS A LINK THE AGENT COULD PLANT. The agent uid owns the
volume's contents, and this runs as root. So every directory below the volume
root is opened one component at a time with ``O_NOFOLLOW`` and every delete,
read and rewrite goes through that directory's descriptor (``nofollow.py``,
the sibling module that holds those helpers); a symbolic link
where a directory or file is expected is refused (could not examine), a link
in a partitioned store is removed as a link, and a database is opened only
after ``lstat`` showed it and every sidecar (``-wal``, ``-shm``, ``-journal``)
to be a plain regular file with one link. The SQLite work then runs in a forked
child that has dropped to the database file's owner (``setgroups([])``,
``setgid``, ``setuid``), so root never opens, checkpoints or creates a sidecar
of an agent-owned database and no ownership has to be given back.

WHO RUNS IT. ``sender_enrollment_cli remove ADDRESS`` and ``purge ADDRESS`` on
the seat, as root, and only as root: no other uid can examine both the
broker's store and the agent's volume, and a run that cannot examine every
location removes nothing and exits nonzero. A child process drops to the broker
uid and removes the enrollment row (the broker's store); the parent, still
root, deletes the files on the agent's volume (the volume root is 0700 to every
uid but the agent and root) and hands each database to a child running as its
owner. The row goes first, so a turn of theirs still in flight is refused by the
partitioned store's roster check before the directory is deleted, and cannot
recreate it afterwards. ``purge --probe ADDRESS`` is read-only.

STDLIB ONLY (this module and ``nofollow.py``), so it runs from either Python on
the seat; it is imported as ``workspace_broker.person_purge``. ``person_slug`` is a
copy of the overlay's ``shared/person_prefs.py``; a parity test pins the two
equal (``workspace_broker/tests/test_person_purge.py``).
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import stat
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from .nofollow import (
    CannotExamine,
    Volume,
    count_tree,
    look,
    read_json_at,
    remove_tree,
    require,
    rewrite_json_at,
    scan,
    why,
)


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
# Where a seat keeps person-keyed data, and what a run found
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

    @property
    def sender_daily_db(self) -> Path:
        """The per-sender daily count (``sender_enrollment.daily_limit``)."""
        return self.data_root / "smd" / "sender_daily.db"


@dataclass
class Finding:
    """One place something of the person's is (or, after a purge, still is),
    or, when ``problem`` is set, one place that could not be examined (or
    purged), which is never an absence."""

    layer: str
    location: str
    count: int
    problem: str = ""
    action: str = "examine"

    def line(self) -> str:
        if self.problem:
            return f"could not {self.action} {self.layer} at {self.location}: {self.problem}"
        return f"{self.layer}: {self.count} at {self.location}"


@dataclass
class PurgeReport:
    removed: list[Finding]
    remaining: list[Finding]
    #: True when a location could not be examined before anything was
    #: deleted, so nothing was: ``remaining`` names the locations.
    refused: bool = False

    @property
    def clean(self) -> bool:
        return not self.remaining

    @property
    def problems(self) -> list[Finding]:
        return [f for f in self.remaining if f.problem]


# ---------------------------------------------------------------------------
# A run: what it found, removed, and could not examine
# ---------------------------------------------------------------------------


@dataclass
class _Run:
    act: bool
    found: list[Finding] = field(default_factory=list)
    problems: list[Finding] = field(default_factory=list)

    def hit(self, layer: str, location: object, count: int) -> None:
        if count:
            self.found.append(Finding(layer, str(location), count))

    def cannot(self, layer: str, exc: BaseException, location: object = "") -> None:
        if isinstance(exc, CannotExamine):
            where, reason = exc.location, exc.why
        else:
            where, reason = str(location), why(exc)
        self.problems.append(Finding(layer, where, 1, problem=reason, action="purge" if self.act else "examine"))


# ---------------------------------------------------------------------------
# Files: partitioned stores, sent-line labels, scheduled-send envelopes
# ---------------------------------------------------------------------------

_PART = "partitioned store directory"


def _partition_one(vol: Volume, root: Path, slug: str, run: _Run) -> None:
    part = root / slug
    with vol.dir(root) as fd:
        if fd is None:
            return
        st = look(slug, fd, part)
        if st is None:
            return
        if stat.S_ISDIR(st.st_mode):
            n = count_tree(fd, slug, part)
            if run.act:
                remove_tree(fd, slug)
            run.hit(_PART, part, max(n, 1))
        else:  # a link (removed as a link, never followed) or a stray file
            if run.act:
                os.unlink(slug, dir_fd=fd)
            link = stat.S_ISLNK(st.st_mode)
            run.hit(f"{_PART} (a link)" if run.act and link else _PART, part, 1)


def _partitions(vol: Volume, targets: PurgeTargets, slug: str, run: _Run) -> None:
    for root in targets.partition_roots:
        try:
            _partition_one(vol, root, slug, run)
        except (CannotExamine, OSError) as exc:
            run.cannot(_PART, exc, root / slug)


def _homes(vol: Volume) -> list[Path]:
    """The volume root and every profile home: where ``.smd`` lives."""
    out = [vol.root]
    profiles = vol.root / "profiles"
    with vol.dir(profiles) as fd:
        if fd is not None:
            for entry in scan(fd, profiles):
                st = look(entry.name, fd, profiles / entry.name)
                if st is not None and stat.S_ISDIR(st.st_mode):
                    out.append(profiles / entry.name)
    return out


def _sent_line(fd: int, name: str, shown: Path, address: str, run: _Run) -> None:
    _st, raw = read_json_at(fd, name, shown)
    if isinstance(raw, dict) and recipient_digest(address) in (raw.get("to_sha256") or []):
        if run.act:
            os.unlink(name, dir_fd=fd)
        run.hit("sent-line labels", shown, 1)


def _envelope(fd: int, name: str, shown: Path, address: str, run: _Run) -> None:
    st, raw = read_json_at(fd, name, shown)
    entries = raw.get("dispatches") if isinstance(raw, dict) else None
    if not isinstance(raw, dict) or not isinstance(entries, list):
        return
    kept = [e for e in entries if not _envelope_mentions(e, address)]
    if run.act and len(kept) != len(entries):
        if kept:
            rewrite_json_at(fd, name, {**raw, "dispatches": kept}, st)
        else:
            os.unlink(name, dir_fd=fd)
    run.hit("scheduled-send envelope", shown, len(entries) - len(kept))


_FILE_LAYERS: tuple[tuple[str, str, str, Callable[[int, str, Path, str, _Run], None]], ...] = (
    ("sent-line labels", "sent_lines", "*.json", _sent_line),
    ("scheduled-send envelope", "pre_run", "*.dispatch*.json", _envelope),
)


def _labelled_files(vol: Volume, address: str, run: _Run) -> None:
    try:
        homes = _homes(vol)
    except CannotExamine as exc:
        for layer, *_ in _FILE_LAYERS:
            run.cannot(layer, exc)
        return
    for layer, sub, pattern, handle in _FILE_LAYERS:
        for home in homes:
            where = home / ".smd" / sub
            try:
                with vol.dir(where) as fd:
                    if fd is None:
                        continue
                    for name in (e.name for e in scan(fd, where) if fnmatch.fnmatchcase(e.name, pattern)):
                        try:
                            handle(fd, name, where / name, address, run)
                        except (CannotExamine, OSError) as exc:
                            run.cannot(layer, exc, where / name)
            except (CannotExamine, OSError) as exc:
                run.cannot(layer, exc, where)


#: The fields of a scheduled-send entry that carry its words. An entry whose
#: words name the person is theirs to purge too: the weekly representative
#: progress report (overlay ``shared/training_report.py``) goes to SMD's
#: administrators and names each representative in its body, so a recipient
#: match alone would leave the removed person's address in it.
_ENVELOPE_TEXT_FIELDS = ("subject", "full_body", "skeleton_body", "body_without_attachment")


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
    if any(_bare(p) == address for p in people):
        return True
    named = _address_in_words(address)
    return any(isinstance(entry.get(key), str) and named.search(entry[key]) for key in _ENVELOPE_TEXT_FIELDS)


def _address_in_words(address: str) -> re.Pattern[str]:
    """The whole address as a token in prose: ``a@x.com`` matches "(a@x.com)" and
    "a@x.com." at a sentence end, never "aa@x.com", "data@x.com" or
    "a@x.com.au". Case-insensitive, as addresses are compared everywhere here."""
    return re.compile(
        r"(?<![a-z0-9._%+\-])" + re.escape(address) + r"(?![a-z0-9\-]|\.[a-z0-9])",
        re.IGNORECASE,
    )


# ---------------------------------------------------------------------------
# SQLite: the allowlist and the per-layer row matchers
# ---------------------------------------------------------------------------


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
        "sender_daily",
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
    "sender_daily": "SELECT rowid FROM sender_daily WHERE sender_hash = ?",
}
_DELETE = {
    "peer_preferences": "DELETE FROM peer_preferences WHERE rowid = ?",
    "requests": "DELETE FROM requests WHERE rowid = ?",
    "replies": "DELETE FROM replies WHERE rowid = ?",
    "held_replies": "DELETE FROM held_replies WHERE rowid = ?",
    "help_senders": "DELETE FROM help_senders WHERE rowid = ?",
    "help_threads": "DELETE FROM help_threads WHERE rowid = ?",
    "help_events": "DELETE FROM help_events WHERE rowid = ?",
    "sender_daily": "DELETE FROM sender_daily WHERE rowid = ?",
}


def _peer_rows(conn: sqlite3.Connection, address: str) -> dict[str, list[Any]]:
    if "peer_preferences" not in _tables(conn):
        return {}
    ids = [
        row[0]
        for row in conn.execute(_SELECT[_known("peer_preferences")])
        if isinstance(row[1], str) and row[1].strip().lower() == address
    ]
    return {"peer_preferences": ids}


def _request_rows(conn: sqlite3.Connection, address: str) -> dict[str, list[Any]]:
    """Request rowids belonging to ``address`` and the reply rowids answering them."""
    tables = _tables(conn)
    if "requests" not in tables:
        return {}
    rows = conn.execute(_SELECT[_known("requests")]).fetchall()
    mine = [r for r in rows if _bare(r[1]) == address]
    ids = {v for r in mine for v in r[2:] if isinstance(v, str) and v}
    replies: list[Any] = []
    if "replies" in tables and ids:
        replies = [row[0] for row in conn.execute(_SELECT[_known("replies")]) if row[1] in ids]
    return {"replies": replies, "requests": [r[0] for r in mine]}


def _held_rows(conn: sqlite3.Connection, address: str) -> dict[str, list[Any]]:
    if "held_replies" not in _tables(conn):
        return {}
    query = "held_replies" if "reply_to" in _columns(conn, "held_replies") else "held_replies_old"
    ids = [
        row[0]
        for row in conn.execute(_SELECT[_known(query, _STATEMENT_KEYS)])
        if _bare(row[1]) == address or _bare(row[2]) == address
    ]
    return {"held_replies": ids}


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


def sender_hash(address: str) -> str:
    """The overlay's ``shared/audit_contract.py`` ``sender_key``: sha256 of the
    NFC-normalized, stripped, lower-cased address. The daily count keys on it."""
    canonical = unicodedata.normalize("NFC", address).strip().lower()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _daily_rows(conn: sqlite3.Connection, address: str) -> dict[str, list[Any]]:
    if "sender_daily" not in _tables(conn):
        return {}
    ids = [r[0] for r in conn.execute(_SELECT[_known("sender_daily")], (sender_hash(address),))]
    return {"sender_daily": ids}


def _delete_rowids(conn: sqlite3.Connection, table: str, ids: Iterable[Any]) -> int:
    ids = list(ids)
    for rowid in ids:
        conn.execute(_DELETE[_known(table)], (rowid,))
    return len(ids)


#: The finding layer of each table's rows.
_LAYER_OF = {
    "peer_preferences": "per-sender memory (peer_preferences)",
    "held_replies": "held replies (held_replies)",
    "requests": "request index (requests)",
    "replies": "request index (replies)",
    **{t: f"help state ({t})" for t in _HELP_TABLES},
    "sender_daily": "per-sender daily count (sender_daily)",
}
_RowsOf = Callable[[sqlite3.Connection, str], dict[str, list[Any]]]


def _databases(targets: PurgeTargets) -> list[tuple[Path, str, _RowsOf]]:
    """``(database, its layer when it cannot be examined, its row matcher)``."""
    return [
        (targets.agent_state_db, "per-sender memory (peer_preferences)", _peer_rows),
        (targets.held_replies_db, "held replies (held_replies)", _held_rows),
        (targets.request_index_db, "request index", _request_rows),
        (targets.help_state_db, "help state", _help_rows),
        (targets.sender_daily_db, "per-sender daily count (sender_daily)", _daily_rows),
    ]


# ---------------------------------------------------------------------------
# SQLite: checked before it is opened, and opened only as its owner
# ---------------------------------------------------------------------------

_SIDECARS = ("-wal", "-shm", "-journal")

#: Run the SQLite work in a forked child even when not root (a test seam: the
#: child path is then exercised without the uid drop, which needs root).
_ALWAYS_FORK = False


def _plain_file(st: os.stat_result, shown: object) -> None:
    require(st, shown, stat.S_ISREG, "a regular file")
    if st.st_nlink != 1:
        raise CannotExamine(shown, f"has {st.st_nlink} hard links; refused, not opened")


def _db_check(vol: Volume, db: Path) -> os.stat_result | None:
    """The database's ``lstat`` when it may be opened, ``None`` when absent.
    Raises when the database, its directory or a sidecar is a link or not a
    plain regular file: such a database is refused and never opened."""
    with vol.dir(db.parent) as fd:
        if fd is None:
            return None
        st = look(db.name, fd, db)
        if st is None:
            return None
        _plain_file(st, db)
        for suffix in _SIDECARS:
            side = look(db.name + suffix, fd, f"{db}{suffix}")
            if side is not None:
                _plain_file(side, f"{db}{suffix}")
        if os.geteuid() == 0 and st.st_uid == 0 and os.fstat(fd).st_uid != 0:
            raise CannotExamine(db, "owned by root in a directory another uid controls; refused, not opened")
        return st


class _ChildFailed(Exception):
    pass


def _as_owner(st: os.stat_result, work: Callable[[], Any]) -> Any:
    """``work()``'s JSON-able result, run as the database file's owner.

    As root, a forked child drops to the file's uid and gid (``setgroups([])``,
    ``setgid``, ``setuid``, the order the enrollment CLI's broker child uses),
    so SQLite never opens, checkpoints or creates a sidecar of the database as
    root. Not root, it runs here: this uid can reach nothing it could not
    already."""
    if os.geteuid() != 0 and not _ALWAYS_FORK:
        return work()
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:  # the child: the owner's uid, this one database, then exit at once
        os.close(read_fd)
        try:
            if os.geteuid() == 0 and st.st_uid != 0:
                os.setgroups([])
                os.setgid(st.st_gid)
                os.setuid(st.st_uid)
            out: dict[str, Any] = {"ok": work()}
        except BaseException as exc:  # noqa: BLE001 - reported to the parent, never raised
            out = {"error": why(exc)}
        with os.fdopen(write_fd, "wb") as pipe:
            pipe.write(json.dumps(out).encode("utf-8"))
        os._exit(0)
    os.close(write_fd)
    with os.fdopen(read_fd, "rb") as pipe:
        data = pipe.read()
    _, status = os.waitpid(pid, 0)
    try:
        result = json.loads(data.decode("utf-8"))
    except ValueError:
        raise _ChildFailed(f"the database child exited ({status}) without a result") from None
    if not isinstance(result, dict) or "ok" not in result:
        raise _ChildFailed(str(result.get("error") if isinstance(result, dict) else result))
    return result["ok"]


def _db_work(db: Path, rows_of: _RowsOf, address: str, act: bool) -> Callable[[], list[list[Any]]]:
    def read() -> list[list[Any]]:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        try:
            return [[t, len(ids)] for t, ids in rows_of(conn, address).items() if ids]
        finally:
            conn.close()

    def delete() -> list[list[Any]]:
        conn = sqlite3.connect(str(db), timeout=30)
        try:
            conn.execute("PRAGMA secure_delete = ON")
            with conn:
                done = [[t, _delete_rowids(conn, t, ids)] for t, ids in rows_of(conn, address).items() if ids]
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return done
        finally:
            conn.close()

    return delete if act else read


def _sql(vol: Volume, targets: PurgeTargets, address: str, run: _Run) -> None:
    for db, label, rows_of in _databases(targets):
        try:
            st = _db_check(vol, db)
            if st is None:
                continue
            for table, n in _as_owner(st, _db_work(db, rows_of, address, run.act)):
                run.hit(_LAYER_OF[_known(table)], db, n)
        except (CannotExamine, _ChildFailed, sqlite3.Error, OSError) as exc:
            run.cannot(label, exc, db)


# ---------------------------------------------------------------------------
# Probe (read-only), preflight and purge
# ---------------------------------------------------------------------------


def _survey(norm: str, targets: PurgeTargets, act: bool) -> _Run:
    run = _Run(act=act)
    if targets.config_error:
        run.problems.append(Finding("the partitioned stores", "the seat config", 1, problem=targets.config_error))
    try:
        vol = Volume(targets.data_root)
    except CannotExamine as exc:
        run.cannot("the volume", exc)
        return run
    try:
        _partitions(vol, targets, person_slug(norm), run)
        _labelled_files(vol, norm, run)
        _sql(vol, targets, norm, run)
    finally:
        vol.close()
    return run


def probe(address: str, targets: PurgeTargets) -> list[Finding]:
    """Everything of ``address``'s the purge set covers that is on the volume
    now, plus every location that could not be examined (``Finding.problem``
    set). READ-ONLY. A location that cannot be examined is a finding, never an
    absence: the probe can only report clean having looked everywhere."""
    norm = normalize_person_address(address)
    if norm is None:
        return [Finding("refused", f"not a person's address: {address!r}", 1)]
    run = _survey(norm, targets, act=False)
    return run.problems + run.found


def preflight(address: str, targets: PurgeTargets) -> list[Finding]:
    """The locations that could not be examined. A purge removes nothing,
    the enrollment row included, unless this is empty."""
    return [f for f in probe(address, targets) if f.problem or f.layer == "refused"]


def purge(address: str, targets: PurgeTargets) -> PurgeReport:
    """Delete ``address``'s data in every purge-set location, then probe.

    Nothing is deleted unless every location could first be examined. The
    report's ``remaining`` is a fresh read of the volume after the deletes,
    never a restatement of them, plus anything that could not be purged. An
    address that was never enrolled, or was removed long ago, is purged the
    same way: orphan data is data."""
    norm = normalize_person_address(address)
    if norm is None:
        return PurgeReport([], [Finding("refused", f"not a person's address: {address!r}", 1)], refused=True)
    blocked = preflight(norm, targets)
    if blocked:
        return PurgeReport([], blocked, refused=True)
    run = _survey(norm, targets, act=True)
    return PurgeReport(removed=run.found, remaining=run.problems + probe(norm, targets))


__all__ = [
    "Finding",
    "PurgeReport",
    "PurgeTargets",
    "normalize_person_address",
    "person_slug",
    "preflight",
    "probe",
    "purge",
    "recipient_digest",
    "sender_hash",
]
