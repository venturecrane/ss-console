"""One downloader for a firm's files: every jobs lane mints, fetches, dedupes
and resolves a matter's files here. $0, read-only.

Before wave 5 the same four steps were written six times (the chronology's
``stages/download.py``, ``stages/msg.py`` and ``stages/update_base.py``, the
demand pull that litigation and negotiation borrow by its private names, the
revise base, and the far-end read-back), so a fix to one reached some lanes
and not others: the demand pull took a file edited since the listing
(2026-10-23) and the chronology still failed on it. The steps, each once:

* ``mint``: presigned URLs for a batch, with the caller's retry policy
  (``Mint``). The policies are declared, not merged: the chronology retries
  once after five seconds and records the second failure's reason; the lanes
  retry with a growing pause. A recorded call order is part of each lane's
  contract with the seat (the ``reader`` goldens' ``pulls`` cells).
* ``fetch_one``: one file's bytes, taking a file edited since it was listed:
  a size the listing no longer matches is pulled again unchecked, and two
  pulls of the same new size are the file as it is now (``size_changed`` on
  the row says so).
* ``keep``: the content dedupe; a byte duplicate's copy is deleted and the
  row names the original.
* ``pull``: the batched loop (mint a batch, then each file in it), with the
  caller's row and the caller's pause between batches.
* ``fetch_all``: the lanes' resumable pull (``pulled.jsonl``), retried after
  a pause for a failure time fixes.
* ``target`` and ``resolve``: a listing row as a pull target, and a file id's
  bytes through its duplicate chain.

The connector's own downloader (the records lane) is not here: it serves
pages to the agent's tools, not files to a job's disk.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..stages.base import append_jsonl, read_jsonl

BATCH = 8
#: The vendor's storage does not hold this object (a presigned GET's 404).
HTTP_ABSENT = 404
HTTP_REFUSED = 403
#: A file whose fetch failed for a reason time fixes is fetched again, after a
#: pause, before the run gives up on it. 2026-10-14: the A&P litigation proof
#: fetched ~400 files and Smokeball answered HTTP 429 on two of them after the
#: connector's own four attempts (1+2+4+8 s); the run failed `fetch_unfinished`
#: and would have needed a person to resume it. A rate limit clears in seconds;
#: a run of two hundred matters must not stop for one. Not retried: a file
#: Smokeball will not serve (403) or does not hold (404), which a retry does
#: not change, and which `litigation.fetch` records as findings.
RETRY_PASSES = 2
RETRY_PAUSE_SECONDS = 20.0
_FINAL = re.compile(r"HTTP 40[34]\b")
_MISMATCH = re.compile(r"size mismatch: got (\d+), expected (\d+)")


@dataclass(frozen=True)
class Mint:
    """How a lane mints: ``attempts`` calls before the batch is recorded as
    failed, the pause after each failed attempt (none past the tuple's end),
    the log line for a failure (``{msg}``, ``{n}``; after the last attempt only
    when ``say_last``), and the per-file error once every attempt failed."""

    attempts: int
    pauses: tuple[float, ...]
    say: str
    say_last: bool
    failed: str


#: The chronology's stages: one retry after five seconds; the second failure's
#: reason is on every file's row.
STAGES = Mint(2, (5.0,), "MINT FAIL ({msg}); retrying once", False, "mint failed twice: {msg}")
#: The demand, litigation and negotiation pulls: a pause after each failure.
LANES = Mint(2, (2.0, 4.0), "mint failed ({msg}); attempt {n} of 2", True, "mint failed twice")


def mint(
    seat: Any, matter_id: str, ids: list[str], log: Callable[[str], None], how: Mint = LANES
) -> dict[str, dict[str, Any]]:
    """``{file id: minted row}`` for one batch; after every attempt failed,
    each id's row carries the error (one file's failure is one row)."""
    msg = ""
    for n in range(1, how.attempts + 1):
        try:
            got = seat.mint(matter_id, ids)
        except Exception as exc:  # noqa: BLE001 - a mint failure is retried by policy, then recorded per file id
            msg = str(exc)[:120]
            if n < how.attempts or how.say_last:
                log(how.say.format(msg=msg, n=n))
            if n <= len(how.pauses):
                time.sleep(how.pauses[n - 1])
            continue
        return {m["id"]: m for m in got}
    return {i: {"id": i, "error": how.failed.format(msg=msg)} for i in ids}


def mint_one(seat: Any, matter_id: str, file_id: Any) -> dict[str, Any]:
    """One file's minted row, minted once; a failure raises to the caller
    (the revise base and the read-back decide their own retry)."""
    minted = seat.mint(matter_id, [file_id])
    return minted[0] if minted else {}


def mismatch_got(exc: BaseException) -> int | None:
    """The byte count a size-mismatch refusal fetched, or None for any other failure."""
    m = _MISMATCH.search(str(exc))
    return int(m.group(1)) if m else None


def storage_error_body(path: Path) -> bool:
    """True when the bytes on disk are the storage service's ``NoSuchKey``
    error document, served in place of the file (2026-10-07)."""
    try:
        head = path.read_bytes()[:600]
    except OSError:
        return False
    return head.lstrip().startswith(b"<?xml") and b"NoSuchKey" in head


def fetch_one(
    seat: Any, url: str, dest: Path, size: Any, *, refuse_error_body: bool = False
) -> tuple[Any, dict[str, Any]]:
    """``(bytes fetched, extra row fields)`` for one file.

    A file edited after the listing (2026-10-23: three A&P files edited
    mid-run failed a 230-matter list on every retry, because each re-pull was
    checked against the stale listed size) is pulled again unchecked: two
    pulls of the same new size are the file as it is now, and the row says the
    size changed. Two different sizes re-raise the first refusal.

    ``refuse_error_body``: a re-pull that is the storage service's error
    document is never taken as the edited file (the first refusal is
    re-raised). The chronology reads every ok row as a record; litigation
    keeps the body and records the file as missing in Smokeball."""
    try:
        return seat.fetch(url, dest, size), {}
    except Exception as exc:  # a listed size the file no longer has is checked below; else re-raised
        got = mismatch_got(exc)
        if got is None:
            raise
        again = seat.fetch(url, dest, None)
        if again != got or (refuse_error_body and storage_error_body(dest)):
            raise
        return again, {"size_changed": {"listed": size, "fetched": got}}


def http_status(exc: BaseException) -> Any:
    """The HTTP status a fetch failure carries, or None."""
    return getattr(getattr(exc, "response", None), "status_code", None)


def keep(dest: Path, seen: dict[str, str], file_id: str) -> tuple[str, str | None]:
    """``(sha256, original id or None)``: a byte duplicate of a file already
    pulled has its copy deleted, so downstream reads one copy and pays once."""
    sha = hashlib.sha256(dest.read_bytes()).hexdigest()
    first = seen.get(sha)
    if first is not None:
        dest.unlink()
        return sha, first
    seen[sha] = str(file_id)
    return sha, None


def pull(
    seat: Any,
    matter_id: str,
    todo: list[dict[str, Any]],
    row: Callable[[dict[str, Any], dict[str, Any]], None],
    *,
    log: Callable[[str], None],
    how: Mint = LANES,
    done: Callable[[int], None] | None = None,
    pause: float = 0.0,
) -> None:
    """Every target in batches of ``BATCH``: one mint per batch, then
    ``row(target, its minted row)`` for each; ``done(pulled so far)`` after a
    batch, and ``pause`` seconds before the next one."""
    for i in range(0, len(todo), BATCH):
        batch = todo[i : i + BATCH]
        minted = mint(seat, matter_id, [b["id"] for b in batch], log, how)
        for b in batch:
            row(b, minted.get(b["id"]) or {})
        if done is not None:
            done(i)
        if pause and i + BATCH < len(todo):
            time.sleep(pause)


# ---- the lanes' resumable pull (demand, litigation, negotiation) ----------------
def fetch_row(seat: Any, d: dict[str, Any], minted: dict[str, Any], raw: Path, seen: dict[str, str]) -> dict[str, Any]:
    """One target's ``pulled.jsonl`` row: ok with its sha256 and path, a byte
    duplicate naming its original, or the failure with its HTTP status."""
    rec = {k: d.get(k) for k in ("id", "name", "ext", "folder")}
    url = minted.get("url")
    if not url:
        return {**rec, "ok": False, "error": minted.get("error", "no url")}
    dest = raw / (str(d["id"]) + (d.get("ext") or ""))
    try:
        _got, changed = fetch_one(seat, url, dest, d.get("size"))
    except Exception as exc:  # noqa: BLE001 - one file's failure is one row
        dest.unlink(missing_ok=True)
        return {**rec, "ok": False, "error": str(exc)[:200], "http_status": http_status(exc)}
    sha, first = keep(dest, seen, str(d["id"]))
    if first is not None:
        return {**rec, "ok": True, "sha256": sha, "duplicate_of": first, **changed}
    return {**rec, "ok": True, "sha256": sha, "path": str(dest), **changed}


def retryable(row: dict[str, Any]) -> bool:
    """A failed row a retry can fix: not a 403 (Smokeball will not serve it)
    and not a 404 (it does not hold it)."""
    if row.get("ok"):
        return False
    if row.get("http_status") in (HTTP_ABSENT, HTTP_REFUSED):
        return False
    return not _FINAL.search(str(row.get("error") or ""))


def refused(row: dict[str, Any]) -> bool:
    return row.get("http_status") == HTTP_REFUSED or bool(re.search(r"HTTP 403\b", str(row.get("error") or "")))


def pull_rows(
    seat: Any,
    matter_id: str,
    todo: list[dict[str, Any]],
    raw: Path,
    seen: dict[str, str],
    log_path: Path,
    log: Any,
    progress: Callable[[int], None] | None = None,
) -> dict[str, dict[str, Any]]:
    """``fetch_row`` over every target, each row appended to ``log_path``;
    ``progress(pulled so far)`` after each batch."""
    rows: dict[str, dict[str, Any]] = {}

    def one(b: dict[str, Any], minted: dict[str, Any]) -> None:
        rows[b["id"]] = fetch_row(seat, b, minted, raw, seen)
        append_jsonl(log_path, rows[b["id"]])

    def done(i: int) -> None:
        n = min(i + BATCH, len(todo))
        log(f"pulled {n}/{len(todo)}")
        if progress is not None:
            progress(n)

    pull(seat, matter_id, todo, one, log=log, done=done)
    return rows


#: How far a pull has got, n of N files, rewritten after each batch: the
#: daemon's progress note reads it (``drafting/progress.py``), so a long pull
#: (502 files, 2026-11-24, its ledger row frozen for 26 minutes) reads as moving.
PROGRESS = "pull-progress.json"


def write_progress(data: Path, done: int, total: int) -> None:
    """``{"done", "total", "at"}``, written whole; a write that fails is skipped."""
    tmp = data / f".{PROGRESS}.tmp"
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        tmp.write_text(json.dumps({"done": int(done), "total": int(total), "at": at}), encoding="utf-8")
        tmp.replace(data / PROGRESS)
    except OSError:
        pass


def fetch_all(
    seat: Any,
    matter_id: str,
    targets: list[dict[str, Any]],
    data: Path,
    log: Callable[[str], None],
    *,
    progress: bool = False,
) -> list[dict[str, Any]]:
    """Resumable: a row already ``ok`` in ``pulled.jsonl`` is not fetched again.
    A row that failed for a reason time fixes is tried again, ``RETRY_PASSES``
    times after a pause, within this call. ``progress``: keep ``PROGRESS`` in
    ``data`` (the drafting lane's progress note reads it; no other lane does)."""
    raw = data / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    log_path = data / "pulled.jsonl"
    rows = {r["id"]: r for r in read_jsonl(log_path)}
    seen = {r["sha256"]: r["id"] for r in rows.values() if r.get("ok") and r.get("path")}
    todo = [t for t in targets if not (rows.get(t["id"]) or {}).get("ok")]
    already = len(targets) - len(todo)
    if progress:
        write_progress(data, already, len(targets))

    def moved(n: int) -> None:
        if progress:
            write_progress(data, already + n, len(targets))

    rows.update(pull_rows(seat, matter_id, todo, raw, seen, log_path, log, moved))
    for n in range(1, RETRY_PASSES + 1):
        again = [t for t in todo if retryable(rows.get(t["id"]) or {})]
        if not again:
            break
        log(
            f"{len(again)} not pulled for a reason a retry can fix; retry {n} of {RETRY_PASSES} in {RETRY_PAUSE_SECONDS:.0f}s"
        )
        time.sleep(RETRY_PAUSE_SECONDS)
        rows.update(pull_rows(seat, matter_id, again, raw, seen, log_path, log))
    return [rows[t["id"]] for t in targets]


def target(f: dict[str, Any]) -> dict[str, Any]:
    """A listing row as a pull target (``fetch_all``'s shape)."""
    return {
        "id": str(f["id"]),
        "name": f.get("name"),
        "ext": f.get("ext") or "",
        "size": f.get("size"),
        "folder": f.get("folderId"),
    }


def resolve(rows: dict[str, dict[str, Any]], file_id: str) -> Path | None:
    """The bytes on disk for a file id; a content duplicate resolves to its
    original, at most three links down."""
    r = rows.get(file_id)
    for _ in range(3):
        if not r or not r.get("ok"):
            return None
        if r.get("path") and Path(r["path"]).is_file():
            return Path(r["path"])
        r = rows.get(str(r.get("duplicate_of") or ""))
    return None


__all__ = [
    "BATCH",
    "HTTP_ABSENT",
    "LANES",
    "RETRY_PASSES",
    "RETRY_PAUSE_SECONDS",
    "STAGES",
    "Mint",
    "fetch_all",
    "fetch_one",
    "fetch_row",
    "http_status",
    "keep",
    "mint",
    "mint_one",
    "mismatch_got",
    "pull",
    "pull_rows",
    "refused",
    "resolve",
    "retryable",
    "storage_error_body",
    "target",
]
