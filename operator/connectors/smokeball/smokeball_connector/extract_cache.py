"""On-disk cache for vision transcriptions of scanned documents (ss#2464).

WHY. ``render_docx_draft`` re-extracts EVERY document on the matter on every
render (``_collect_matter_sources``). Without a cache, a matter with 24 scans
would bill 24 vision reads per draft attempt, and the drafting lane's whole
value is that you can iterate on a draft. With one, the transcription is paid
for once, by the deliberate ``read_document`` call that produced it.

THE SHAPE, and why each choice is the way it is:

- **Key = sha256 of the file's BYTES**, hex, used verbatim as the filename. Not
  the fileId: that is agent-supplied, so it is a path-traversal class the moment
  it becomes a filename. Not fileId+size: Smokeball versions a document in
  place, and a re-scanned page can land at the same size. The content hash is
  the only key that is both traversal-proof and correct under versioning.
- **Outside ``.smokeball-mcp/``.** That directory holds the OAuth refresh token.
  A cache of matter text does not belong next to the credential that reads the
  matter, and a bug in this file must not be able to touch that one.
- **0700 dir / 0600 files.** The cache holds firm document text — the same
  sensitivity as the documents themselves.
- **Success only.** A refusal is never cached: a missing credential or a
  disabled seat is a condition that gets fixed, and a cached "no" would outlive
  the fix.
- **Bounded and expiring.** 256 MB with LRU eviction, entries expire after 30
  days. The Fly volume is small and survives reprovision by design (ADR 0010),
  so an unbounded cache here becomes a disk-full incident later.
- **Fail-safe, never fail-closed.** Every operation swallows its own I/O errors:
  a seat with no writable cache directory still reads documents, it just pays
  for each transcription. Extraction must never break because a cache did.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

CACHE_DIR_ENV = "SMOKEBALL_EXTRACT_CACHE_DIR"
CACHE_MAX_BYTES_ENV = "SMOKEBALL_EXTRACT_CACHE_MAX_BYTES"
DEFAULT_CACHE_DIR = "/opt/data/smokeball-extract-cache"
DEFAULT_MAX_BYTES = 256 * 1024 * 1024
TTL_SECONDS = 30 * 24 * 60 * 60


def cache_root() -> Path:
    return Path((os.environ.get(CACHE_DIR_ENV) or "").strip() or DEFAULT_CACHE_DIR)


def max_bytes() -> int:
    raw = (os.environ.get(CACHE_MAX_BYTES_ENV) or "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            return DEFAULT_MAX_BYTES
        if value > 0:
            return value
    return DEFAULT_MAX_BYTES


def cache_key(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def entry_path(root: Path, key: str) -> Path | None:
    """The file for ``key``, or None if it would land outside ``root``. The key
    is a sha256 hex digest, so this cannot fire today — it is asserted anyway,
    because the day someone keys the cache on a name from the API is the day it
    can."""
    candidate = (root / f"{key}.json").resolve()
    try:
        if not candidate.is_relative_to(root.resolve()):
            return None
    except (OSError, ValueError):
        return None
    return candidate


def cache_get(blob: bytes) -> str | None:
    """The cached transcription for these bytes, or None. A corrupt, expired, or
    unreadable entry is a miss — never an error, and never partial text."""
    root = cache_root()
    path = entry_path(root, cache_key(blob))
    return None if path is None else _read_text(path)


def cache_put(blob: bytes, text: str, *, pages: int | None = None) -> None:
    """Store a SUCCESSFUL transcription. Silently does nothing on any I/O fault
    or for empty text."""
    if not text or not text.strip():
        return
    root = cache_root()
    path = entry_path(root, cache_key(blob))
    if path is None:
        return
    _write(root, path, {"method": "vision", "text": text, "pages": pages, "created_at": time.time()})


# ---- One page of a file (wave 5, K1) ------------------------------------------
#
# A mixed PDF (typed pages plus scans) has only its scanned pages read, so what
# is stored is a PAGE, under its own name: ``<file sha>.p<N>.<method version>``.
# The whole-file entry ``<sha>.json`` and its key are untouched, so every entry
# written before this keeps serving exactly as it did. The method version names
# the model, the prompt and the render the page was read with: a change to any
# of them is a new name, never a stale reading served as current.


def page_key(sha: str, page: int, version: str) -> str:
    return f"{sha}.p{int(page)}.{version}"


def page_get(sha: str, page: int, version: str) -> str | None:
    """The stored reading of one page, or None (the same miss rules as
    :func:`cache_get`)."""
    root = cache_root()
    path = entry_path(root, page_key(sha, page, version))
    return None if path is None else _read_text(path)


def page_put(sha: str, page: int, version: str, text: str) -> None:
    """Store one page's reading. Never raises; empty text is not stored."""
    if not text or not text.strip():
        return
    root = cache_root()
    path = entry_path(root, page_key(sha, page, version))
    if path is None:
        return
    _write(root, path, {"method": "vision_page", "text": text, "page": int(page), "created_at": time.time()})


def page_failure_get(sha: str, page: int, version: str) -> tuple[int, str] | None:
    """How many times a read of this page failed in a way that would fail again
    (the model refused it, or ran out of room), and the last reason; None when
    none is recorded. No expiry: a page that has used its attempts is never
    paid for again under this method version."""
    root = cache_root()
    path = entry_path(root, page_key(sha, page, version) + ".failed")
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    attempts, reason = (payload.get("attempts"), payload.get("reason")) if isinstance(payload, dict) else (None, None)
    if not isinstance(attempts, int) or not isinstance(reason, str):
        return None
    return attempts, reason


def page_failure_put(sha: str, page: int, version: str, attempts: int, reason: str) -> None:
    """Record a failed read of one page (its attempts so far). Never raises."""
    root = cache_root()
    path = entry_path(root, page_key(sha, page, version) + ".failed")
    if path is None:
        return
    record = {"method": "vision_page_failed", "attempts": int(attempts), "reason": reason, "created_at": time.time()}
    _write(root, path, record)


def _read_text(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    created = payload.get("created_at")
    if not isinstance(created, (int, float)) or time.time() - created > TTL_SECONDS:
        return None
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    try:  # LRU: eviction reads mtime, so a hit must be a touch.
        os.utime(path)
    except OSError:
        pass
    return text


def _write(root: Path, path: Path, payload: dict) -> None:
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8", opener=_private_opener) as fh:
            fh.write(json.dumps(payload))
        os.replace(tmp, path)
    except OSError:
        return
    _evict(root)


def _private_opener(path: str, flags: int) -> int:
    return os.open(path, flags, 0o600)


def _evict(root: Path) -> None:
    """Keep the cache under its byte ceiling, oldest-touched first."""
    ceiling = max_bytes()
    try:
        entries = [(p.stat().st_mtime, p.stat().st_size, p) for p in root.glob("*.json")]
    except OSError:
        return
    total = sum(size for _mtime, size, _p in entries)
    if total <= ceiling:
        return
    for _mtime, size, path in sorted(entries):
        if total <= ceiling:
            return
        try:
            path.unlink()
        except OSError:
            continue
        total -= size
