"""Invite-code enrollment: a person SMD invited joins a seat's listed senders.

ADR 0106. A guide seat (``seat.role: guide``) that authors
``sender_enrollment.enabled: true`` lets a person who holds an invite code SMD
issued email the seat from an address nobody listed, with the code in the
message, and be added to the people the seat answers. The webhook router
detects the code before any model sees the message and asks this broker to
redeem it (verb ``sender_enrollment_redeem``); this module decides.

WHY THE BROKER HOLDS IT. The roster is authority: who the seat answers and who
it writes to. ``customer.yaml`` was moved root-owned so the agent uid could
not widen its own roster (entrypoint, "the structural close of the
self-loopback"); an enrolled-sender list the agent uid could write would
reopen exactly that loop. So the store has the audit ledger's posture: owned
by the broker uid on the Fly volume, reached by the broker through a bind
mount, the enrolled list readable (never writable) by the agent uid through
the ``audit-readers`` group, and the codes in a 0700 subdirectory the agent
uid cannot enter at all.

THE STORE, under ``SMD_SENDER_ENROLLMENT_DIR`` (default
``/run/smd-sender-enrollment``, the bind of ``/opt/data/sender-enrollment``):

* ``enrolled.json`` (0640): ``{"schema": 1, "enrolled": [{address, label,
  code_id, enrolled_at}]}``. The ONLY file anything outside the broker reads.
* ``private/codes.db`` (0600, dir 0700): the issued codes, each a per-code
  salted scrypt hash, never the code; and every attempt (time, sender key,
  outcome) for the lockouts.
* ``private/lock``: serializes every read-modify-write of ``enrolled.json``
  between the broker and SMD's command line.

A CODE is ``SMD-XXXX-XXXX-XXXX`` over a 30-letter alphabet with no 0/O, 1/I/L
or U (12 random letters, about 59 bits). Only its scrypt hash is stored; it is
printed once, when issued (``sender_enrollment_cli issue``). ss-console is a
public repository: no code, phrase or hash is ever committed.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


from .recipient_policy import normalize_address, sender_key
from . import seat_config

DIR_ENV = "SMD_SENDER_ENROLLMENT_DIR"
DEFAULT_DIR = "/run/smd-sender-enrollment"
ENROLLED_FILE = "enrolled.json"
PRIVATE_DIR = "private"
CODES_DB = "codes.db"
LOCK_FILE = "lock"
SCHEMA = 1

#: The customer.yaml block and the one role it is accepted on (both validators
#: refuse it elsewhere; this reader honours it only there too).
BLOCK = "sender_enrollment"
ROLE = "guide"
#: SMD's demo seat (ADR 0107 purpose demo), read from ``seat.kind``: sales
#: representatives enroll on it the way they do on the guide (ADR 0106
#: amendment 2026-11-10). Mirrors the overlay's ``shared/sender_enrollment.py``.
KIND = "demo"

#: The code's shape. Mirrored by the overlay's ``shared/sender_enrollment.py``
#: (the router's detector); ``tests/sender-enrollment.test.ts`` pins the two
#: literals equal.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
CODE_PATTERN = r"\bSMD-[A-HJKMNP-TV-Z2-9]{4}-[A-HJKMNP-TV-Z2-9]{4}-[A-HJKMNP-TV-Z2-9]{4}\b"
_CODE_RE = re.compile(CODE_PATTERN, re.IGNORECASE)

#: scrypt cost: about 50 ms per check on a shared CPU, per code, per attempt.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32

#: Lockouts. Per sender: this many refused AUTHENTICATED attempts in the window
#: locks that address out for the window. Seat-wide: this many refused attempts
#: of any kind in the window refuses every attempt until the window passes.
#: Unauthenticated attempts count only seat-wide, so a forged From cannot lock a
#: real person out.
SENDER_MAX_FAILURES = 5
SENDER_WINDOW = timedelta(hours=24)
SEAT_MAX_FAILURES = 20
SEAT_WINDOW = timedelta(hours=1)

#: The closed outcome vocabulary of an attempt (audit metadata ``outcome``).
ENROLLED = "enrolled"
ALREADY_LISTED = "already_listed"
UNAUTHENTICATED = "unauthenticated"
WRONG_CODE = "wrong_code"
EXPIRED = "expired"
USED_UP = "used_up"
REVOKED = "revoked"
SENDER_LOCKED = "sender_locked"
SEAT_LOCKED = "seat_locked"
NOT_IN_MESSAGE = "code_not_in_message"
SENDER_MISMATCH = "sender_mismatch"
DISABLED = "disabled"
OUTCOMES = frozenset(
    {
        ENROLLED,
        ALREADY_LISTED,
        UNAUTHENTICATED,
        WRONG_CODE,
        EXPIRED,
        USED_UP,
        REVOKED,
        SENDER_LOCKED,
        SEAT_LOCKED,
        NOT_IN_MESSAGE,
        SENDER_MISMATCH,
        DISABLED,
    }
)
#: Outcomes that count toward a lockout. ``already_listed`` and ``disabled``
#: are not guesses; a lockout refusal is not counted again (it would extend
#: itself forever).
FAILURES = frozenset({UNAUTHENTICATED, WRONG_CODE, EXPIRED, USED_UP, REVOKED, NOT_IN_MESSAGE, SENDER_MISMATCH})

_CREATE_SQL = (
    """CREATE TABLE IF NOT EXISTS codes (
  code_id     TEXT PRIMARY KEY,
  label       TEXT NOT NULL,
  salt        BLOB NOT NULL,
  digest      BLOB NOT NULL,
  created_at  TEXT NOT NULL,
  expires_at  TEXT NOT NULL,
  max_uses    INTEGER NOT NULL,
  uses        INTEGER NOT NULL DEFAULT 0,
  revoked_at  TEXT,
  name        TEXT
)""",
    """CREATE TABLE IF NOT EXISTS attempts (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  at          TEXT NOT NULL,
  sender_key  TEXT NOT NULL,
  outcome     TEXT NOT NULL
)""",
)

_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")

#: A person's name as an administrator gives it: letters (any script), spaces,
#: apostrophes, hyphens and periods, starting with a letter, one line, at most
#: 60 characters. Mirrored by the overlay's ``shared/sender_enrollment.py``
#: NAME_PATTERN (``tests/sender-enrollment.test.ts`` pins the two equal). It is
#: interpolated by code into exactly two places (the welcome, and the standing
#: line the agent reads about who is writing), so anything else is refused.
NAME_MAX = 60
NAME_PATTERN = r"^[^\W\d_](?:[^\W\d_]|[ '.\-])*$"
_NAME_RE = re.compile(NAME_PATTERN)


def valid_name(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= NAME_MAX
        and value == value.strip()
        and "  " not in value
        and _NAME_RE.match(value) is not None
    )


class EnrollmentError(ValueError):
    """A request the store refuses (bad label, unknown code id, bad store)."""


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def store_dir() -> Path:
    return Path(os.environ.get(DIR_ENV) or DEFAULT_DIR)


def find_code(text: str) -> str | None:
    """The first code-shaped token in ``text``, upper-cased, or None."""
    if not isinstance(text, str):
        return None
    found = _CODE_RE.search(text)
    return found.group(0).upper() if found else None


def find_codes(text: str) -> list[str]:
    """Every code-shaped token in ``text``, upper-cased."""
    return [m.group(0).upper() for m in _CODE_RE.finditer(text)] if isinstance(text, str) else []


def normalize_code(value: Any) -> str | None:
    """A code exactly as issued (upper case), or None when not code-shaped."""
    if not isinstance(value, str):
        return None
    candidate = value.strip().upper()
    found = _CODE_RE.fullmatch(candidate)
    return candidate if found else None


def new_code() -> str:
    letters = "".join(secrets.choice(CODE_ALPHABET) for _ in range(12))
    return f"SMD-{letters[0:4]}-{letters[4:8]}-{letters[8:12]}"


def _digest(code: str, salt: bytes) -> bytes:
    return hashlib.scrypt(code.encode("ascii"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN)


def enabled_for(customer_path: Path) -> bool:
    """Does this seat's own config switch enrollment on? Only on a guide or demo seat.

    Fail closed: an unreadable config, another role, or anything but
    ``enabled: true`` is off."""
    try:
        data = seat_config.load(Path(customer_path), encoding="utf-8", on_missing="raise") or {}
    except Exception:  # noqa: BLE001 - unreadable config is "off", never "on"
        return False
    return enabled_in(data)


def enabled_in(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    seat = data.get("seat")
    block = data.get(BLOCK)
    return (
        isinstance(seat, dict)
        and (seat.get("role") == ROLE or seat.get("kind") == KIND)
        and isinstance(block, dict)
        and block.get("enabled") is True
    )


@dataclass(frozen=True)
class Attempt:
    outcome: str
    label: str = ""
    code_id: str = ""
    name: str | None = None


@dataclass(frozen=True)
class CodeRow:
    code_id: str
    label: str
    created_at: str
    expires_at: str
    max_uses: int
    uses: int
    revoked_at: str | None
    name: str | None = None


class EnrollmentStore:
    """The broker-owned store. Every method opens and closes its own handle."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else store_dir()

    # -- layout ---------------------------------------------------------

    @property
    def enrolled_path(self) -> Path:
        return self.root / ENROLLED_FILE

    @property
    def private(self) -> Path:
        return self.root / PRIVATE_DIR

    def ensure(self) -> None:
        """Create the private dir 0700 and the files, never widening a mode."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.private.mkdir(mode=0o700, exist_ok=True)
        # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions - owner-only on purpose; the codes dir
        os.chmod(self.private, 0o700)
        if not self.enrolled_path.exists():
            self._write_enrolled([])

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.ensure()
        fd = os.open(self.private / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        self.ensure()
        path = self.private / CODES_DB
        conn = sqlite3.connect(str(path), timeout=5.0)
        try:
            os.chmod(path, 0o600)
            for stmt in _CREATE_SQL:
                conn.execute(stmt)
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- enrolled list --------------------------------------------------

    def enrolled(self) -> list[dict[str, str]]:
        """Every enrolled entry. A missing file is nobody; a malformed one raises."""
        try:
            raw = json.loads(self.enrolled_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        entries = raw.get("enrolled") if isinstance(raw, dict) else None
        if not isinstance(entries, list):
            raise EnrollmentError("enrolled.json is not an enrolled list")
        return [e for e in entries if isinstance(e, dict) and isinstance(e.get("address"), str)]

    def enrolled_addresses(self) -> frozenset[str]:
        return frozenset(normalize_address(e["address"]) for e in self.enrolled() if normalize_address(e["address"]))

    def _write_enrolled(self, entries: list[dict[str, str]]) -> None:
        body = json.dumps({"schema": SCHEMA, "enrolled": entries}, indent=2, sort_keys=True) + "\n"
        fd, tmp = tempfile.mkstemp(dir=str(self.root), prefix=".enrolled.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o640)
            os.replace(tmp, self.enrolled_path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def remove(self, address: str) -> bool:
        """SMD removes one enrolled address. True when it was there."""
        wanted = normalize_address(address)
        with self._locked():
            entries = self.enrolled()
            kept = [e for e in entries if normalize_address(e["address"]) != wanted]
            if len(kept) == len(entries):
                return False
            self._write_enrolled(kept)
            # The person's name goes with them: off the code it was issued on.
            gone = {str(e.get("code_id")) for e in entries if e not in kept and e.get("name")}
            with self._db() as conn:
                for code_id in gone:
                    conn.execute("UPDATE codes SET name = NULL WHERE code_id = ?", (code_id,))
            return True

    # -- codes ----------------------------------------------------------

    def issue(
        self, *, label: str, days: int, max_uses: int = 1, name: str | None = None, now: datetime | None = None
    ) -> tuple[str, str]:
        """Mint one code; return ``(code, code_id)``. The code exists nowhere else."""
        if name is not None and not valid_name(name):
            raise EnrollmentError("name: plain letters, spaces, apostrophes, hyphens and periods, at most 60")
        if not isinstance(label, str) or not _LABEL_RE.match(label):
            raise EnrollmentError("label: 1-64 letters, digits, spaces, dots, dashes or underscores")
        if not isinstance(days, int) or not 1 <= days <= 90:
            raise EnrollmentError("days: a whole number from 1 to 90")
        if not isinstance(max_uses, int) or not 1 <= max_uses <= 100:
            raise EnrollmentError("max uses: a whole number from 1 to 100")
        moment = now or _now()
        code = new_code()
        code_id = "c_" + secrets.token_hex(6)
        salt = secrets.token_bytes(16)
        with self._db() as conn:
            conn.execute(
                "INSERT INTO codes (code_id,label,salt,digest,created_at,expires_at,max_uses,uses,name) "
                "VALUES (?,?,?,?,?,?,?,0,?)",
                (
                    code_id,
                    label,
                    salt,
                    _digest(code, salt),
                    _iso(moment),
                    _iso(moment + timedelta(days=days)),
                    max_uses,
                    name,
                ),
            )
        return code, code_id

    def codes(self, label: str | None = None) -> list[CodeRow]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT code_id,label,created_at,expires_at,max_uses,uses,revoked_at,name FROM codes "
                "ORDER BY created_at, code_id"
            ).fetchall()
        return [CodeRow(*row) for row in rows if not label or row[1] == label]

    def revoke(self, code_id: str, now: datetime | None = None) -> bool:
        with self._db() as conn:
            cur = conn.execute(
                "UPDATE codes SET revoked_at = ? WHERE code_id = ? AND revoked_at IS NULL",
                (_iso(now or _now()), code_id),
            )
            return cur.rowcount == 1

    # -- attempts and lockouts -----------------------------------------

    def _failures(self, conn: sqlite3.Connection, since: datetime, key: str | None) -> int:
        """Refused attempts since ``since``: seat-wide, or for one sender's
        key (where an unauthenticated attempt is not counted)."""
        rows = conn.execute("SELECT sender_key, outcome FROM attempts WHERE at >= ?", (_iso(since),)).fetchall()
        return sum(
            1
            for row_key, outcome in rows
            if outcome in FAILURES and (key is None or (row_key == key and outcome != UNAUTHENTICATED))
        )

    def record(self, sender: str, outcome: str, now: datetime | None = None) -> None:
        if outcome not in OUTCOMES:
            raise EnrollmentError(f"unknown outcome {outcome!r}")
        with self._db() as conn:
            conn.execute(
                "INSERT INTO attempts (at, sender_key, outcome) VALUES (?,?,?)",
                (_iso(now or _now()), sender_key(sender) or "", outcome),
            )

    # -- the redeem -----------------------------------------------------

    def redeem(self, *, sender: str, code: str, authenticated: bool, now: datetime | None = None) -> Attempt:
        """Decide one attempt and record it. Enrolls only on a valid code from
        an authenticated, not-locked-out sender; consumes a use only then."""
        moment = now or _now()
        address = normalize_address(sender)
        key = sender_key(address) or ""
        with self._locked(), self._db() as conn:
            if self._failures(conn, moment - SEAT_WINDOW, None) >= SEAT_MAX_FAILURES:
                return self._log(conn, key, Attempt(SEAT_LOCKED), moment)
            if not authenticated or not address:
                return self._log(conn, key, Attempt(UNAUTHENTICATED), moment)
            if self._failures(conn, moment - SENDER_WINDOW, key) >= SENDER_MAX_FAILURES:
                return self._log(conn, key, Attempt(SENDER_LOCKED), moment)
            wanted = normalize_code(code)
            match = self._match(conn, wanted) if wanted else None
            if match is None:
                return self._log(conn, key, Attempt(WRONG_CODE), moment)
            code_id, label, expires_at, max_uses, uses, revoked_at, name = match
            name = name if valid_name(name) else None
            if revoked_at:
                return self._log(conn, key, Attempt(REVOKED, label, code_id), moment)
            if _parse(expires_at) <= moment:
                return self._log(conn, key, Attempt(EXPIRED, label, code_id), moment)
            if uses >= max_uses:
                return self._log(conn, key, Attempt(USED_UP, label, code_id), moment)
            entries = self.enrolled()
            if address not in {normalize_address(e["address"]) for e in entries}:
                entry = {"address": address, "label": label, "code_id": code_id, "enrolled_at": _iso(moment)}
                if name:
                    entry["name"] = name
                entries.append(entry)
                self._write_enrolled(entries)
            conn.execute("UPDATE codes SET uses = uses + 1 WHERE code_id = ?", (code_id,))
            return self._log(conn, key, Attempt(ENROLLED, label, code_id, name), moment)

    @staticmethod
    def _match(conn: sqlite3.Connection, code: str) -> tuple[str, str, str, int, int, str | None, str | None] | None:
        """The issued row this code hashes to. Every row is checked (no early
        exit), so the time taken does not say which row matched."""
        found = None
        for code_id, label, salt, digest, expires_at, max_uses, uses, revoked_at, name in conn.execute(
            "SELECT code_id,label,salt,digest,expires_at,max_uses,uses,revoked_at,name FROM codes"
        ).fetchall():
            if hmac.compare_digest(_digest(code, bytes(salt)), bytes(digest)) and found is None:
                found = (code_id, label, expires_at, int(max_uses), int(uses), revoked_at, name)
        return found

    @staticmethod
    def _log(conn: sqlite3.Connection, key: str, attempt: Attempt, moment: datetime) -> Attempt:
        conn.execute(
            "INSERT INTO attempts (at, sender_key, outcome) VALUES (?,?,?)", (_iso(moment), key, attempt.outcome)
        )
        # Only the lockout windows read this table; the audit ledger keeps the
        # history. Older rows go, so a guessing run cannot grow it unbounded.
        conn.execute("DELETE FROM attempts WHERE at < ?", (_iso(moment - 2 * SENDER_WINDOW),))
        return attempt


def enrolled_for_policy(customer_data: Any) -> frozenset[str]:
    """The enrolled addresses the broker's recipient policy and participant
    fence add to the authored roster: none unless this seat switches
    enrollment on. An unreadable store adds nobody (the closed direction)."""
    if not enabled_in(customer_data):
        return frozenset()
    try:
        return EnrollmentStore().enrolled_addresses()
    except Exception:  # noqa: BLE001 - fewer people answered, never more
        return frozenset()


__all__ = [
    "Attempt",
    "CODE_PATTERN",
    "EnrollmentError",
    "EnrollmentStore",
    "enabled_for",
    "enabled_in",
    "enrolled_for_policy",
    "find_code",
    "find_codes",
    "normalize_code",
    "valid_name",
]
