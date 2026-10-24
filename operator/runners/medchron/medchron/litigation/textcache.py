"""The litigation lane's text cache: a paid read is bought once, not per job.

WHY (2026-10-24). Text lived only in each job's dir, so every list re-fetched
every file and re-sent every scanned page to vision. Three lists over the same
230 matters bought the same pages three times.

On the volume (``$MEDCHRON_DATA_DIR/litigation/textcache/``, the runner's own;
``MEDCHRON_LITIGATION_TEXTCACHE_DIR`` overrides it)::

    <sha256>.txt          the extracted text of the file with that content
    <sha256>.json         {method_version, scanned_pages, chars, matter_ids[], job_id, at}
    files/<file_id>.json  {modified, size, sha256, matter_id}: the listing a
                          file had when its content was that sha

A text is reused only under the same ``method_version`` (a short hash of the
extract constants, the OCR prompt and the transcription model): a change to
how text is made is a miss, never a stale hit. A file's text is reused WITHOUT
downloading it only when its listing still shows the same ``(modified, size)``.

A cache is an economy, never a gate: a write that fails is logged once and the
cache goes quiet; the job carries on reading. A failure is never cached (the
next job tries the file again); a ``DEFERRED`` placeholder is, because it is
what extract writes for a page read on demand (``view_page`` fetches the raw
file then). A rehearsal opens the cache READ-ONLY (``read_only``) and its
counting OCR stub's placeholder is refused here even if one reached a write.

Client data: the cache holds matter text. It is a runtime layer of the seat's
volume (the handbook's layer inventory), and ``purge --matter <id>`` removes a
matter from it ("gone means gone").
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from . import extract as extract_mod

ENV = "MEDCHRON_LITIGATION_TEXTCACHE_DIR"
DATA_ENV = "MEDCHRON_DATA_DIR"
DEFAULT_DATA_DIR = "/opt/data/medchron"
#: The rehearsal's counting OCR stub marks every page it did not transcribe
#: with this; such text is never cached or seeded.
REHEARSAL_MARK = "[rehearsal OCR stub:"


def storage_error_text(text: str) -> bool:
    """The storage service's ``NoSuchKey`` XML (a file Smokeball lists but
    does not hold, ``fetch.MISSING``): never text worth keeping."""
    head = text.lstrip()[:600]
    return head.startswith("<?xml") and "NoSuchKey" in head


def cache_dir(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit)
    if os.environ.get(ENV):
        return Path(os.environ[ENV])
    return Path(os.environ.get(DATA_ENV) or DEFAULT_DATA_DIR) / "litigation" / "textcache"


def method_version(model: str) -> str:
    """How text is made, as a short hash: the extract constants that decide
    which pages are transcribed and how, ``extract.EXTRACT_VERSION`` (bumped by
    hand with any reader change: a new decoding, a new file kind, a fixed
    email body), the OCR prompt, and the model."""
    e = extract_mod
    parts = [e.EXTRACT_VERSION, e.HEAD, e.TAIL, e.SCAN_FLOOR, e.RENDER_LONG_SIDE, e.DEFERRED, e.OCR_PROMPT, model]
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:12]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(path: Path, text: str) -> None:
    """Atomic, 0600: a reader sees the old file or the new one, never half."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


class TextCache:
    def __init__(
        self,
        root: str | Path | None = None,
        *,
        method_version: str,
        job_id: str = "",
        read_only: bool = False,
        log: Callable[[str], None] = print,
    ) -> None:
        self.root = cache_dir(root)
        #: Why writes stopped on this run (a real run records it in its job state).
        self.quiet_reason = ""
        self.method_version = method_version
        self.job_id = job_id
        self.read_only = read_only
        self.log = log
        self.quiet = False  # set when a write failed: the cache stops writing
        self._lock = threading.Lock()

    # ---- reads ---------------------------------------------------------------------
    def get_text(self, sha: str | None) -> str | None:
        if not sha:
            return None
        meta = _read(self.root / f"{sha}.json")
        if not isinstance(meta, dict) or meta.get("method_version") != self.method_version:
            return None
        try:
            return (self.root / f"{sha}.txt").read_text(encoding="utf-8")
        except OSError:
            return None

    def meta(self, sha: str | None) -> dict[str, Any]:
        m = _read(self.root / f"{sha}.json") if sha else None
        return m if isinstance(m, dict) and m.get("method_version") == self.method_version else {}

    def lookup_file(self, file_id: str, modified: Any, size: Any) -> str | None:
        """The sha a file had when its listing showed this ``(modified, size)``."""
        rec = _read(self.root / "files" / f"{file_id}.json")
        if not isinstance(rec, dict) or not modified:
            return None
        if str(rec.get("modified") or "") != str(modified) or rec.get("size") != size:
            return None
        return str(rec.get("sha256") or "") or None

    def text_for(self, f: dict[str, Any]) -> tuple[str, str] | None:
        """(sha, text) for a listed file whose listing and content are cached."""
        sha = self.lookup_file(str(f.get("id")), f.get("modified"), f.get("size"))
        text = self.get_text(sha)
        return (sha, text) if sha and text is not None else None

    # ---- writes ----------------------------------------------------------------------
    def _writable(self) -> bool:
        if self.read_only or self.quiet:
            return False
        try:
            # owner-only (0700): the umask cannot widen it
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            (self.root / "files").mkdir(mode=0o700, exist_ok=True)
        except OSError as exc:
            self._quiet(exc)
            return False
        return True

    def _quiet(self, exc: BaseException) -> None:
        if not self.quiet:
            self.quiet_reason = f"{type(exc).__name__}: {str(exc)[:160]}"
            self.log(
                f"[textcache] WARN: writes stopped for this run ({self.quiet_reason}); the job reads on without it"
            )
        self.quiet = True

    def put_text(self, sha: str | None, text: str, *, scanned_pages: int, matter_id: str) -> bool:
        if not sha or REHEARSAL_MARK in text or storage_error_text(text) or not self._writable():
            return False
        with self._lock:
            try:
                meta = _read(self.root / f"{sha}.json")
                same = isinstance(meta, dict) and meta.get("method_version") == self.method_version
                if not same or not (self.root / f"{sha}.txt").is_file():
                    _write(self.root / f"{sha}.txt", text)
                    meta = {"scanned_pages": int(scanned_pages), "chars": len(text), "matter_ids": []}
                ids = sorted({*meta.get("matter_ids", []), matter_id} - {""})
                doc = {**meta, "method_version": self.method_version, "matter_ids": ids}
                doc.update(job_id=meta.get("job_id") if same else self.job_id, at=meta.get("at") if same else _now())
                _write(self.root / f"{sha}.json", json.dumps(doc, indent=1))
            except OSError as exc:
                self._quiet(exc)
                return False
        return True

    def add_matter(self, sha: str | None, matter_id: str) -> None:
        """A hit on another matter adds its id, so a purge of either matter
        removes only its own claim and never text the other still uses."""
        if not sha or not matter_id or not self._writable():
            return
        with self._lock:
            try:
                meta = _read(self.root / f"{sha}.json")
                if isinstance(meta, dict) and matter_id not in (meta.get("matter_ids") or []):
                    meta["matter_ids"] = sorted({*(meta.get("matter_ids") or []), matter_id})
                    _write(self.root / f"{sha}.json", json.dumps(meta, indent=1))
            except OSError as exc:
                self._quiet(exc)

    def put_file(self, file_id: str, f: dict[str, Any], row: dict[str, Any], matter_id: str) -> bool:
        """Index a listed file (``f``) against the content its pulled ``row``
        fetched. Refused here, so no caller can forget: a row not ``ok``, one
        with no sha, one whose file changed after the listing (``size_changed``:
        the listing no longer describes those bytes), and one whose bytes are
        the storage service's ``NoSuchKey`` XML (``fetch.MISSING``)."""
        sha, modified, size = row.get("sha256"), f.get("modified"), f.get("size")
        if not (row.get("ok") and sha and modified) or row.get("size_changed") or _missing(row):
            return False
        if not self._writable():
            return False
        rec = {"modified": str(modified), "size": size, "sha256": sha, "matter_id": matter_id}
        try:
            _write(self.root / "files" / f"{file_id}.json", json.dumps(rec))
        except OSError as exc:
            self._quiet(exc)
            return False
        return True


def _missing(row: dict[str, Any]) -> bool:
    from .fetch import _nosuchkey

    return bool(row.get("path")) and _nosuchkey(Path(row["path"]))


# ---- purge ("gone means gone") ------------------------------------------------------------
def purge_matter(root: str | Path | None, matter_id: str, dry_run: bool = False) -> dict[str, int]:
    """Remove a matter from the cache: its id leaves every text entry; an entry
    left with no matter is deleted, as is every file-index row of the matter or
    pointing at a deleted text. Another matter's entries are untouched."""
    base = cache_dir(root)
    out = {"entries_deleted": 0, "entries_kept_for_other_matters": 0, "file_rows_deleted": 0}
    gone: set[str] = set()
    for meta_p in sorted(base.glob("*.json")) if base.is_dir() else []:
        meta = _read(meta_p)
        if not isinstance(meta, dict) or matter_id not in (meta.get("matter_ids") or []):
            continue
        left = [m for m in meta.get("matter_ids") or [] if m != matter_id]
        if left:
            out["entries_kept_for_other_matters"] += 1
            if not dry_run:
                try:
                    _write(meta_p, json.dumps({**meta, "matter_ids": left}, indent=1))
                except OSError as exc:
                    out["errors"] = out.get("errors", 0) + 1
                    print(f"[textcache] purge could not rewrite {meta_p.name}: {type(exc).__name__}", file=sys.stderr)
            continue
        out["entries_deleted"] += 1
        gone.add(meta_p.stem)
        if not dry_run:
            (base / f"{meta_p.stem}.txt").unlink(missing_ok=True)
            meta_p.unlink(missing_ok=True)
    for row_p in sorted((base / "files").glob("*.json")) if (base / "files").is_dir() else []:
        rec = _read(row_p)
        if isinstance(rec, dict) and (rec.get("matter_id") == matter_id or rec.get("sha256") in gone):
            out["file_rows_deleted"] += 1
            if not dry_run:
                row_p.unlink(missing_ok=True)
    return out


# ---- seed: a finished job's texts into the cache ---------------------------------------------
def _seed_row(cache: TextCache, mdir: Path, mid: str, r: dict[str, Any], listing: dict[str, Any]) -> str:
    """One pulled row into the cache; the outcome's name."""
    sha = r.get("sha256")
    if not r.get("ok") or not sha:
        return "skipped_no_sha"
    tp = mdir / "txt" / f"{r['id']}.txt"
    text = tp.read_text(encoding="utf-8", errors="replace") if tp.is_file() else None
    if text is None and not r.get("duplicate_of"):
        return "skipped_no_text"
    if text is not None and REHEARSAL_MARK in text:
        return "skipped_rehearsal_text"
    if text is not None and storage_error_text(text):
        return "skipped_missing_in_smokeball"
    had = cache.get_text(sha) is not None
    if not had and text is None:
        return "skipped_no_text"  # a duplicate whose original's text is not cached either
    f = listing.get(str(r["id"])) or {}
    if not cache.read_only:
        if text is not None:
            cache.put_text(sha, text, scanned_pages=_scanned(mdir, str(r["id"])), matter_id=mid)
        cache.put_file(str(r["id"]), f, r, mid)
    return "already" if had else "seeded"


def _scanned(mdir: Path, fid: str) -> int:
    from ..stages.base import read_jsonl

    recs = [x for x in read_jsonl(mdir / "extracted.jsonl") if x.get("file_id") == fid and x.get("ok")]
    return int((recs[-1] if recs else {}).get("scanned_pages") or 0)


def seed_job(cache: TextCache, job_dir: Path) -> dict[str, int]:
    from ..stages.base import read_jsonl

    data = Path(job_dir) / "data"
    out: dict[str, int] = {"seeded": 0, "already": 0, "skipped_no_sha": 0, "skipped_no_text": 0}
    for mdir in sorted((data / "m").iterdir()) if (data / "m").is_dir() else []:
        mid = mdir.name
        lp = data / "files" / f"{mid}.json"
        listing = {str(f["id"]): f for f in (_read(lp) or []) if isinstance(f, dict)}
        rows = {r["id"]: r for r in read_jsonl(mdir / "pulled.jsonl")}
        for r in rows.values():
            k = _seed_row(cache, mdir, mid, r, listing)
            out[k] = out.get(k, 0) + 1
    return out


def _model(args: list[str]) -> str:
    if "--model" in args:
        return args[args.index("--model") + 1]
    from . import firm as firm_mod

    inputs = args[args.index("--inputs") + 1] if "--inputs" in args else None
    return firm_mod.load(inputs).model("transcription")


def main(argv: list[str] | None = None) -> int:
    """``python -m medchron.litigation.textcache seed --job <job_dir> [--dry-run]
    [--model M | --inputs DIR] [--dir CACHE]`` or ``purge --matter <id> [--dry-run] [--dir CACHE]``."""
    args = list(argv if argv is not None else sys.argv[1:])
    root = args[args.index("--dir") + 1] if "--dir" in args else None
    dry = "--dry-run" in args
    if args[:1] == ["seed"] and "--job" in args:
        job = Path(args[args.index("--job") + 1])
        jj = _read(job / "job.json") or {}
        cache = TextCache(root, method_version=method_version(_model(args)), job_id=str(jj.get("job_id") or job.name))
        cache.read_only = dry
        print(json.dumps({**seed_job(cache, job), "dry_run": dry, "method_version": cache.method_version}))
        return 0
    if args[:1] == ["purge"] and "--matter" in args:
        print(json.dumps({**purge_matter(root, args[args.index("--matter") + 1], dry), "dry_run": dry}))
        return 0
    print(main.__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
