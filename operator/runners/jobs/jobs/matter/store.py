"""The store of paid page text: a page read the same way is paid for once.

WHY (wave 5). The litigation lane kept a whole file's text per job method, so
any reader change (``EXTRACT_VERSION``) re-paid every scanned page, and the
chronology, demand and negotiation lanes kept nothing past the 72 hours a job
dir lives: on the first real A&P update, 158 of 222 files were scans already
paid for (``stages/vision_seed``).

THE LAYOUT, beside the litigation lane's whole-file entries (``jobs/textcache``)
under the one store dir::

    pages/<file sha256>/<method>/<page>.json   {text, matter_ids[]}
    pages/.lock                                the cross-process lock
    pages/.stats.json                          {lane: {hits, misses}}

``text`` is the model's raw reply for that page (each lane applies its own
handling after, exactly as for a fresh read). ``<method>`` is ``paid.Method``'s
version: a hash of the prompt, the model, the max tokens and how the page was
rendered, so a page read another way is a miss, never a stale hit. The same
bytes give the same page text whichever matter they arrive on (decided: a
cross-matter hit adds that matter's id, and a purge removes only its own id).

REFUSED, so no caller can forget: a write with no matter id (it could never be
purged or probed), a failure or refusal marker, a rehearsal's stub, a scan or
skip mark, and empty text. A read-only store (a rehearsal) never writes.

ON BY ``JOBS_TEXTCACHE_DIR`` only (every lane seat's boot sets it,
``templates/entrypoint-lanes.sh``). Unset, the store is off and counts what was
paid, so a job records ``store: off`` instead of looking like a store with no
hits. A write that fails is logged once and the store goes quiet; it is an
economy, never a gate.

THE CAP: ``pages/`` holds at most 512 MB; past it the oldest-touched pages go
first (a hit touches its page). The whole-file entries are never evicted.

Client data: page text is matter text. ``python -m jobs.textcache purge
--matter <id>`` removes a matter, ``probe --matter <id>`` exits nonzero while
anything still names it ("gone means gone").
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

#: The env var that switches the store on and names its dir.
ENV = "JOBS_TEXTCACHE_DIR"
PAGES = "pages"
LOCK = ".lock"
STATS = ".stats.json"
#: The bytes ``pages/`` holds, shared by every process that writes it (each
#: write adds its delta under the lock; only a write that crosses the cap scans).
BYTES = ".bytes"
CAP_BYTES = 512 * 1024 * 1024
#: Past the cap, eviction runs down to this share of it, so the next scan is
#: many writes away instead of on the very next one.
LOW_WATER = 0.9
#: The stop reasons of a reading that finished. Anything else (a refusal, a
#: reply cut off at its token limit, a tool call) is never stored.
COMPLETE_STOPS = frozenset({"end_turn", "stop_sequence"})
_SHA = re.compile(r"^[0-9a-f]{64}$")
#: Texts that are a failure, never a reading (``stages/vision`` FAILED / REFUSED).
FAILED_TEXTS = frozenset({"[page transcription failed]", "[page transcription refused]"})
#: Marks a lane writes for a page it did not read: a rehearsal's counting stub
#: (``textcache.REHEARSAL_MARK``), a scanned page left for ``view_page`` or not
#: read in this job (``casefile/pack.SCAN_MARK``), a page skipped as read
#: elsewhere (``medchron/update/pageid.SKIPPED``).
MARKS = ("[rehearsal OCR stub:", "[scanned page", "[not transcribed:")


def refused(text: Any) -> bool:
    """True for text the store never keeps."""
    if not isinstance(text, str) or not text.strip():
        return True
    s = text.strip()
    return s in FAILED_TEXTS or any(m in s for m in MARKS)


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _is_file(path: Path) -> bool:
    """Whether a page file is there; a store this process cannot read holds nothing it can serve."""
    try:
        return path.is_file()
    except OSError:
        return False


def _ids(doc: Any) -> list[str]:
    """A stored page's matter ids ([] for anything that is not one)."""
    ids = doc.get("matter_ids") if isinstance(doc, dict) else None
    return [str(m) for m in ids] if isinstance(ids, list) else []


def write_atomic(path: Path, text: str) -> None:
    """Atomic, 0600: a reader sees the old file or the new one, never half.
    The bytes reach the disk (fsync) before the name points at them, so a
    crash cannot leave the new name on a truncated file."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


@contextmanager
def locked(pages: Path) -> Iterator[None]:
    """The cross-process lock every change to ``pages/`` takes."""
    pages.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(pages / LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def page_files(root: Path) -> list[Path]:
    """Every stored page under ``root/pages`` (the lock and stats files aside)."""
    base = root / PAGES
    if not base.is_dir():
        return []
    return sorted(p for p in base.glob("*/*/*.json") if not p.name.startswith("."))


class PageStore:
    """One job's view of the store (``root`` None: off, counting only)."""

    def __init__(
        self,
        root: str | Path | None,
        *,
        lane: str,
        read_only: bool = False,
        log: Callable[[str], None] = print,
        cap_bytes: int = CAP_BYTES,
        record_to: Path | None = None,
    ) -> None:
        self.root = Path(root) if root else None
        self.lane = lane
        self.read_only = read_only
        self.log = log
        self.cap_bytes = cap_bytes
        #: Where the job's summary is written (``page_store.json`` in its job dir).
        self.record_to = record_to
        self.hits = self.misses = self.writes = 0
        #: Misses of a page the store already holds read this way (its page
        #: file is there, and was not served: unreadable, or text it refuses):
        #: a page a working store would have served, which the hourly
        #: work-health watches. A new page of a file the store holds is not one.
        self.repeats = 0
        self.quiet = False
        self.quiet_reason = ""
        self._flushed = (0, 0, 0)
        self._lock = threading.Lock()

    @property
    def on(self) -> bool:
        return self.root is not None

    def _path(self, sha: str, page: int, method: str) -> Path | None:
        if self.root is None or not _SHA.match(str(sha or "")) or not re.fullmatch(r"[0-9a-f]{12}", method):
            return None
        return self.root / PAGES / sha / method / f"{int(page)}.json"

    # ---- reads -----------------------------------------------------------------------
    def get(self, sha: str | None, page: int, method: str, matter_id: str = "") -> str | None:
        """The stored text of this page read this way, or None (a miss: the
        caller pays and then ``put``s). A hit adds ``matter_id`` to the page."""
        p = self._path(str(sha or ""), page, method)
        doc = _read(p) if p is not None else None
        if not isinstance(doc, dict) or refused(doc.get("text")):
            with self._lock:
                self.misses += 1
                if p is not None and _is_file(p):
                    self.repeats += 1
            return None
        with self._lock:
            self.hits += 1
        assert p is not None
        self._claim(p, matter_id)
        return str(doc["text"])

    def _claim(self, p: Path, matter_id: str) -> None:
        if self.read_only or self.quiet or self.root is None:
            return
        try:
            with self._lock, locked(self.root / PAGES):
                os.utime(p)  # a hit is a touch: the cap evicts the oldest-touched first
                doc = _read(p)
                if isinstance(doc, dict) and matter_id and matter_id not in _ids(doc):
                    doc["matter_ids"] = sorted({*_ids(doc), matter_id})
                    write_atomic(p, json.dumps(doc, indent=1))
        except FileNotFoundError:
            return  # a purge or an eviction removed the page after it was read: the text served stands
        except OSError as exc:
            self._quiet(exc)

    # ---- writes ----------------------------------------------------------------------
    def put(
        self, sha: str | None, page: int, method: str, text: str, matter_id: str, *, stop_reason: str | None
    ) -> bool:
        """Keep one fresh reading. ``stop_reason`` is the model's: only a
        reading that finished (:data:`COMPLETE_STOPS`) is kept, so a refusal's
        prose or a cut-off reply is never served as a page."""
        p = self._path(str(sha or ""), page, method)
        if p is None or self.read_only or self.quiet or not matter_id or refused(text):
            return False
        if stop_reason not in COMPLETE_STOPS:
            return False
        try:
            with self._lock, locked(p.parents[2]):
                p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                ids = sorted({*_ids(_read(p)), matter_id})
                body = json.dumps({"text": text, "matter_ids": ids}, indent=1)
                before = p.stat().st_size if p.is_file() else 0
                write_atomic(p, body)
                self.writes += 1
                self._account(p.stat().st_size - before)
        except OSError as exc:
            self._quiet(exc)
            return False
        return True

    def _account(self, delta: int) -> None:
        """Keep ``pages/`` under the cap (called holding the lock). The total
        is the shared ``.bytes`` file plus this write; only a total past the
        cap scans, and then it evicts down to :data:`LOW_WATER` of it."""
        if self.root is None:
            return
        counter = self.root / PAGES / BYTES
        raw = _read(counter)
        total = (raw if isinstance(raw, int) and raw >= 0 else _scan(self.root)) + delta
        if total > self.cap_bytes:
            total = _evict(self.root, int(self.cap_bytes * LOW_WATER), self.cap_bytes)
        write_atomic(counter, json.dumps(max(total, 0)))

    def _quiet(self, exc: BaseException) -> None:
        if not self.quiet:
            self.quiet_reason = f"{type(exc).__name__}: {str(exc)[:160]}"
            self.log(
                f"[page store] WARN: writes stopped for this run ({self.quiet_reason}); the job pays on without it"
            )
        self.quiet = True

    def missed(self) -> None:
        """A page paid for that the store could not key (off, or no file sha)."""
        with self._lock:
            self.misses += 1

    # ---- the job's record ------------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        return {"store": "on" if self.on else "off", "hits": self.hits, "misses": self.misses, "repeats": self.repeats}

    def flush(self) -> None:
        """Add what this run counted since the last flush to the job's
        ``page_store.json`` and to the store's per-lane stats. Never raises."""
        with self._lock:
            hits, misses = self.hits - self._flushed[0], self.misses - self._flushed[1]
            repeats = self.repeats - self._flushed[2]
            self._flushed = (self.hits, self.misses, self.repeats)
        try:
            if self.record_to is not None and (hits or misses):  # a job that read no page records nothing
                record(self.record_to, self.lane, self.on, hits, misses, self.quiet_reason, repeats)
            if self.root is not None and not self.read_only and (hits or misses) and (self.root / PAGES).is_dir():
                with locked(self.root / PAGES):
                    _add_stats(self.root / PAGES / STATS, self.lane, hits, misses)
        except OSError as exc:
            self.log(f"[page store] WARN: the job's page-store record was not written ({type(exc).__name__})")


def _scan(root: Path) -> int:
    """The bytes ``pages/`` holds now (a page removed mid-scan counts nothing)."""
    total = 0
    for f in page_files(root):
        try:
            total += f.stat().st_size
        except FileNotFoundError:
            continue
    return total


def _evict(root: Path, low: int, cap: int) -> int:
    """A fresh scan; past ``cap``, the oldest-touched pages go until ``low`` is
    reached. The bytes left. A page another process removed is skipped."""
    sized: list[tuple[float, int, Path]] = []
    for f in page_files(root):
        try:
            st = f.stat()
        except FileNotFoundError:
            continue
        sized.append((st.st_mtime, st.st_size, f))
    total = sum(size for _, size, _ in sized)
    if total <= cap:
        return total
    for _, size, f in sorted(sized, key=lambda x: x[0]):
        if total <= low:
            break
        f.unlink(missing_ok=True)
        total -= size
        _prune(f)
    return total


def _add_stats(path: Path, lane: str, hits: int, misses: int) -> None:
    """Add a run's counts to the store's per-lane stats (called holding the lock)."""
    raw = _read(path) if path.is_file() else None
    stats: dict[str, Any] = raw if isinstance(raw, dict) else {}
    row = stats.get(lane)
    row = row if isinstance(row, dict) else {}
    stats[lane] = {"hits": int(row.get("hits") or 0) + hits, "misses": int(row.get("misses") or 0) + misses}
    write_atomic(path, json.dumps(stats, indent=1, sort_keys=True))


#: The job's record of the store, in its job dir; the lane reads it into the
#: job's ledger row (``page_store``), which the hourly work-health reads.
RECORD = "page_store.json"


def record(path: Path, lane: str, on: bool, hits: int, misses: int, quiet: str = "", repeats: int = 0) -> None:
    """Merge one run's counts into a job's ``page_store.json``."""
    old = _read(path) if path.is_file() else None
    old = old if isinstance(old, dict) else {}
    doc = {
        "lane": lane,
        "store": "on" if on else "off",
        "hits": int(old.get("hits") or 0) + int(hits),
        "misses": int(old.get("misses") or 0) + int(misses),
        "repeats": int(old.get("repeats") or 0) + int(repeats),
    }
    if quiet:
        doc["quiet"] = quiet[:200]
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(doc, sort_keys=True))


def read_record(job_dir: Path) -> dict[str, Any] | None:
    """A job's store record as its ledger row carries it, or None when the job
    read no page (or ran before the store existed)."""
    doc = _read(Path(job_dir) / RECORD)
    if not isinstance(doc, dict) or doc.get("store") not in ("on", "off"):
        return None
    return {
        key: (doc["store"] if key == "store" else int(doc.get(key) or 0))
        for key in ("store", "hits", "misses", "repeats")
    }


def from_env(
    lane: str, *, read_only: bool = False, log: Callable[[str], None] = print, record_to: Path | None = None
) -> PageStore:
    """The store for a lane that reads a page by itself: on when ``JOBS_TEXTCACHE_DIR`` is set."""
    return PageStore(os.environ.get(ENV) or None, lane=lane, read_only=read_only, log=log, record_to=record_to)


def enabled() -> bool:
    return bool(os.environ.get(ENV))


def child_env() -> dict[str, str]:
    """The store's env var for a lane's child, when the daemon has it."""
    return {ENV: os.environ[ENV]} if os.environ.get(ENV) else {}


def job_fields(job_dir: Path) -> dict[str, Any]:
    """``{"page_store": ...}`` for a job's final ledger record, or {} when the
    job read no page (``workspace_broker/page_store_meta`` puts it on the row)."""
    rec = read_record(job_dir)
    return {"page_store": rec} if rec is not None else {}


# ---- purge, probe, stats ("gone means gone") ---------------------------------------------
def purge_matter(root: Path, matter_id: str, dry_run: bool = False) -> dict[str, int]:
    """The matter's id leaves every page; a page left with no matter is deleted
    (no keys at all when the store has no pages)."""
    if not (root / PAGES).is_dir():
        return {}
    out = {"pages_deleted": 0, "pages_kept_for_other_matters": 0}
    with locked(root / PAGES):
        for p in page_files(root):
            doc = _read(p)
            ids = _ids(doc)
            if matter_id not in ids:
                continue
            left = [m for m in ids if m != matter_id]
            out["pages_kept_for_other_matters" if left else "pages_deleted"] += 1
            if dry_run:
                continue
            if left:
                write_atomic(p, json.dumps({**(doc or {}), "matter_ids": left}, indent=1))
            else:
                p.unlink(missing_ok=True)
                _prune(p)
        if not dry_run:
            (root / PAGES / BYTES).unlink(missing_ok=True)  # the next write counts what is left afresh
    return out


def _prune(p: Path) -> None:
    for d in (p.parent, p.parent.parent):
        try:
            d.rmdir()
        except OSError:
            return


def purge_sha(root: Path, sha: str, dry_run: bool = False) -> int:
    """Every page of the file with this content (one wrong transcription
    dropped); the count deleted."""
    base = root / PAGES / sha
    if not _SHA.match(sha) or not base.is_dir():
        return 0
    with locked(root / PAGES):
        files = sorted(base.glob("*/*.json"))
        if not dry_run:
            for p in files:
                p.unlink(missing_ok=True)
                _prune(p)
            (root / PAGES / BYTES).unlink(missing_ok=True)
    return len(files)


def probe_matter(root: Path, matter_id: str) -> list[str]:
    """Every page that still names the matter (relative paths)."""
    return [p.relative_to(root).as_posix() for p in page_files(root) if matter_id in _ids(_read(p))]


def stats(root: Path) -> dict[str, Any]:
    files = page_files(root)
    lanes = _read(root / PAGES / STATS)
    return {
        "pages": len(files),
        "page_bytes": sum(f.stat().st_size for f in files),
        "files": len({f.parent.parent.name for f in files}),
        "by_lane": lanes if isinstance(lanes, dict) else {},
    }


__all__ = [
    "CAP_BYTES",
    "ENV",
    "RECORD",
    "PageStore",
    "child_env",
    "enabled",
    "from_env",
    "job_fields",
    "page_files",
    "probe_matter",
    "purge_matter",
    "purge_sha",
    "read_record",
    "record",
    "refused",
    "stats",
]
