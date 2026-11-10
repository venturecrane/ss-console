"""The canary inventory: where did one person's words land, and is each place accounted for?

A rehearsal gives each person a unique marker string and puts it in everything
they send. Afterwards this module searches EVERY place the run could have left
it and sorts each hit into one of three bins:

* the PURGE SET: locations removing that person must clear
  (``workspace_broker/person_purge.py`` deletes them);
* the NAMED-RETAINED SET: locations SMD decided to keep and says so (session
  transcripts, logs, the model provider's copy, ...);
* UNDECLARED: anywhere else. A hit here is the finding this instrument exists
  for: a store nobody listed, so nobody purges it and nobody told the owner it
  is kept.

WHAT IS SEARCHED (a "surface" is one root directory or one extra file):

* every file under the seat's temp home, as raw BYTES, so a deleted SQLite row
  still sitting on a free page, a WAL frame or a log line is found;
* every SQLite database under it, table by table and row by row, so a hit can be
  attributed to the table that holds it;
* any extra file the caller names: the model stub's request log, the captured
  log records, card payloads.

HOW A HIT IS MATCHED. A :class:`Location` names a path glob relative to its
surface's root and, for a database, optionally a table. A table-level hit is
covered by a location naming that file and table (or the whole file). A raw
hit in a database file is covered only when the same file also has a
table-level hit that is covered, or when the whole file is declared: bytes that
no table claims are leftovers (a deleted row not overwritten), never assumed to
be one of the declared rows. A WAL, shared-memory or journal sidecar is judged
as its database file.

EXTENDING IT. A new store is one :class:`Location` in the purge set (and one
layer in the purge); a new kept place is one in the retained set with its
reason. Nothing else changes, and an undeclared store fails the run on the
first rehearsal that writes to it.
"""

from __future__ import annotations

import fnmatch
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

_SQLITE_MAGIC = b"SQLite format 3\x00"
_SIDECARS = ("-wal", "-shm", "-journal")


@dataclass(frozen=True)
class Location:
    """A place a marker may legitimately be. ``glob`` is matched against the
    hit's path relative to its surface root (``fnmatch``, so ``*`` crosses
    ``/``); ``table`` restricts a database location to one table."""

    surface: str
    glob: str
    table: str | None = None
    why: str = ""

    def covers(self, hit: Hit) -> bool:
        if hit.surface != self.surface or not fnmatch.fnmatch(hit.path, self.glob):
            return False
        return self.table is None or hit.table == self.table


@dataclass(frozen=True)
class Hit:
    """One place the marker was found. ``table`` is set for a row hit;
    ``raw`` hits are the file's bytes."""

    surface: str
    path: str
    table: str | None = None
    raw: bool = False
    #: A raw hit in a SQLite database or one of its sidecars.
    sqlite: bool = False

    def __str__(self) -> str:
        where = f"{self.surface}:{self.path}"
        if self.table:
            where += f" [table {self.table}]"
        return where + (" (bytes)" if self.raw else "")


@dataclass
class Inventory:
    hits: list[Hit]
    purge: list[Hit] = field(default_factory=list)
    retained: list[Hit] = field(default_factory=list)
    undeclared: list[Hit] = field(default_factory=list)

    def report(self) -> str:
        lines = []
        for name, hits in (
            ("UNDECLARED", self.undeclared),
            ("purge set", self.purge),
            ("named-retained", self.retained),
        ):
            lines.append(f"{name}: {len(hits)}")
            lines.extend(f"  {h}" for h in hits)
        return "\n".join(lines)


def _db_of(path: Path) -> Path:
    for suffix in _SIDECARS:
        if path.name.endswith(suffix):
            return path.with_name(path.name[: -len(suffix)])
    return path


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(len(_SQLITE_MAGIC)) == _SQLITE_MAGIC
    except OSError:
        return False


def _table_hits(db: Path, needle: str) -> set[str]:
    """Tables of ``db`` with a row whose any value contains ``needle``. Read
    through a copy-free immutable URI so the scan cannot change the file."""
    found: set[str] = set()
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return found
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            try:
                # A scan of every table must name each table; the names come
                # from the database's own sqlite_master, quoted as identifiers,
                # in a test-only instrument opened read-only.
                # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                rows = conn.execute("SELECT * FROM " + quoted)
            except sqlite3.Error:
                continue
            for row in rows:
                if any(_contains(value, needle) for value in row):
                    found.add(table)
                    break
    finally:
        conn.close()
    return found


def _contains(value: object, needle: str) -> bool:
    if isinstance(value, str):
        return needle in value
    if isinstance(value, bytes):
        return needle.encode() in value
    return False


def search(needle: str, surfaces: dict[str, Path]) -> list[Hit]:
    """Every hit of ``needle`` across ``surfaces`` (a name to a directory or a
    single file)."""
    raw_needle = needle.encode()
    hits: list[Hit] = []
    for surface, root in surfaces.items():
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        base = root.parent if root.is_file() else root
        dbs_seen: set[Path] = set()
        for path in files:
            try:
                data = path.read_bytes()
            except OSError:
                continue
            rel = str(path.relative_to(base))
            db = _db_of(path)
            if raw_needle in data:
                hits.append(Hit(surface, rel, raw=True, sqlite=db.exists() and _is_sqlite(db)))
            if db not in dbs_seen and db.exists() and _is_sqlite(db):
                dbs_seen.add(db)
                for table in sorted(_table_hits(db, needle)):
                    hits.append(Hit(surface, str(db.relative_to(base)), table=table))
    return hits


def classify(hits: Iterable[Hit], purge: list[Location], retained: list[Location]) -> Inventory:
    hits = list(hits)
    inv = Inventory(hits=hits)

    def bin_of(hit: Hit) -> str:
        for name, declared in (("purge", purge), ("retained", retained)):
            if any(loc.covers(hit) for loc in declared):
                return name
        return "undeclared"

    def whole_file_bin(surface: str, path: str) -> str:
        """The bin of a location declaring the WHOLE file (no table)."""
        probe = Hit(surface, path)
        for name, declared in (("purge", purge), ("retained", retained)):
            if any(loc.table is None and loc.covers(probe) for loc in declared):
                return name
        return "undeclared"

    row_bins: dict[tuple[str, str], set[str]] = {}
    for hit in hits:
        if hit.table is not None:
            row_bins.setdefault((hit.surface, hit.path), set()).add(bin_of(hit))
    for hit in hits:
        target = bin_of(hit)
        if hit.raw:
            db_rel = str(_db_of(Path(hit.path)))
            whole = whole_file_bin(hit.surface, db_rel)
            bins = row_bins.get((hit.surface, db_rel))
            if whole != "undeclared":
                target = whole
            elif bins is not None:
                # A database's bytes are attributed only through a row that
                # carries the marker: any undeclared row makes them undeclared,
                # and with no row at all they are leftovers (below).
                target = "undeclared" if "undeclared" in bins else min(bins)
            elif hit.sqlite:
                # Bytes in a database that no row claims: a deleted row's
                # content left on a free page or in a log. Never declared.
                target = "undeclared"
        getattr(inv, target).append(hit)
    return inv


def inventory(
    needle: str, surfaces: dict[str, Path], purge: list[Location], retained: list[Location]
) -> Inventory:
    """Search ``surfaces`` for ``needle`` and sort every hit into its bin."""
    return classify(search(needle, surfaces), purge, retained)


__all__ = ["Hit", "Inventory", "Location", "classify", "inventory", "search"]
