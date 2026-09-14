"""`download`: pull the extraction set for a matter into `raw/`, verified, deduped
by content, logged to `raw_manifest.jsonl`.

Selection is the authored `include.json` (`decide_selection` writes it from the
firm's rules; nothing here knows a folder name). Presigned URLs are minted in
small batches immediately before each pull because they expire, and the seat
paces itself between mints. A file whose sha256 was already pulled is recorded
with `duplicate_of` and its bytes deleted, so downstream stages read one copy
and pay for one (a delivered matter once carried 10/265 byte-identical files,
all paid for twice).

Exit 1 when a target is still not pulled for a reason a retry could fix: the
frozen script printed the failures and exited 0, which is exactly the kind of
outcome an agent reading stdout would catch and a driver would not.

The one exception is a 404 on a freshly minted presigned URL, which is the
vendor's storage saying that object is not there. Minting again mints another
URL to the same absent object, so no retry, no rebuild and no reprovision ever
turns it into bytes -- and halting on it means a firm can never get a chronology
for that matter until their vendor repairs their own storage. Every downstream
stage already skips a row that is not `ok` (coverage, extract, vision,
exhibits), and the delivered document already names each unpulled file, its
folder and its reason under Records Reviewed and Limitations. So an absent
object is a disposition the firm is told about, not a halt. Everything else --
auth, throttling, timeouts, 5xx, a size mismatch, a mint that failed twice --
keeps the hard exit.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from ..seat import SeatError
from .base import StageRun, append_jsonl, read_json, read_jsonl

BATCH = 8
# The vendor's storage does not have this object. Structured off the response
# rather than parsed out of the message, because a presigned URL can itself
# contain the digits.
HTTP_ABSENT = 404
MINT_RETRY_PAUSE_SECONDS = 5.0
BATCH_PAUSE_SECONDS = 1.0


def wanted(
    doc: dict[str, Any],
    *,
    folder_path: str,
    prefixes: list[str],
    excludes: list[str],
    root_pdfs: bool,
    doc_exts: set[str],
) -> bool:
    if doc.get("deleted"):
        return False
    ext = (doc.get("ext") or "").lower()
    if ext not in doc_exts:
        return False
    if any(x in folder_path.upper() for x in excludes):
        return False
    if folder_path == "/(root)":
        return root_pdfs and ext == ".pdf"
    return any(folder_path.startswith(p) for p in prefixes)


def _already(rows: list[dict[str, Any]]) -> tuple[set[str], dict[str, str]]:
    done: set[str] = set()
    seen_sha: dict[str, str] = {}
    for r in rows:
        if r.get("ok"):
            done.add(r["id"])
            if r.get("sha256") and not r.get("duplicate_of"):
                seen_sha.setdefault(r["sha256"], r["id"])
    return done, seen_sha


def run(sr: StageRun) -> int:
    sel = read_json(sr.slug_dir / "include.json", None)
    if sel is None:
        raise SeatError("include.json is missing: decide_selection did not run")
    prefixes = list(sel["include_prefixes"])
    excludes = [x.upper() for x in sel.get("exclude_substrings", [])]
    root_pdfs = bool(sel.get("root_pdfs", True))
    doc_exts = {e.lower() for e in sr.cfg.get("selection", "doc_extensions")}
    fpath = sr.folder_paths()
    raw = sr.slug_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    log_path = sr.slug_dir / "raw_manifest.jsonl"

    targets = [
        d
        for d in sr.manifest()
        if wanted(
            d,
            folder_path=fpath.get(d.get("folderId"), "/(root)"),
            prefixes=prefixes,
            excludes=excludes,
            root_pdfs=root_pdfs,
            doc_exts=doc_exts,
        )
    ]
    file_ids = sel.get("include_file_ids")
    if file_ids:
        # ss#2616 append runs: only the named documents, from the full matter
        # listing (the id set overrides the folder grain). A named id the
        # matter does not carry is a HOLD naming it — a silent skip would let
        # an append claim coverage it never pulled.
        ids = {str(f) for f in file_ids}
        by_id = {d["id"]: d for d in sr.manifest()}
        missing = sorted(ids - set(by_id))
        if missing:
            sr.log(f"append: {len(missing)} named document id(s) not on the matter: {', '.join(missing[:5])}")
            return 2
        targets = [by_id[i] for i in sorted(ids)]
        sr.log(f"append: pull restricted to {len(targets)} named document(s)")
    done, seen_sha = _already(read_jsonl(log_path))
    todo = [t for t in targets if t["id"] not in done]
    sr.log(f"{sr.slug}: {len(targets)} targets, {len(done)} already done, {len(todo)} to pull")

    pulled = dupes = 0
    failed: list[dict[str, Any]] = []
    batches = (len(todo) + BATCH - 1) // BATCH
    for i in range(0, len(todo), BATCH):
        batch = todo[i : i + BATCH]
        ids = [b["id"] for b in batch]
        minted = _mint_with_retry(sr, ids)
        byid = {m["id"]: m for m in minted}
        for b in batch:
            m = byid.get(b["id"]) or {}
            rec: dict[str, Any] = {
                "id": b["id"],
                "name": b["name"],
                "ext": b["ext"],
                "folder": fpath.get(b.get("folderId"), "/(root)"),
                "size_expected": b.get("size"),
            }
            url = m.get("url")
            if not url:
                rec.update(ok=False, error=m.get("error", "no url"))
                failed.append(rec)
                append_jsonl(log_path, rec)
                continue
            dest = raw / (b["id"] + (b.get("ext") or ""))
            try:
                got = sr.seat.fetch(url, dest, b.get("size"))
                sha = hashlib.sha256(dest.read_bytes()).hexdigest()
                rec.update(ok=True, path=str(dest), size_got=got, sha256=sha)
                dup = seen_sha.get(sha)
                if dup:
                    dest.unlink()
                    rec.update(duplicate_of=dup, path=None)
                    dupes += 1
                else:
                    seen_sha[sha] = b["id"]
                    pulled += 1
            except Exception as exc:  # noqa: BLE001 - one file's failure is one row
                Path(dest).unlink(missing_ok=True)
                rec.update(ok=False, error=str(exc)[:200])
                rec["http_status"] = getattr(getattr(exc, "response", None), "status_code", None)
                failed.append(rec)
            append_jsonl(log_path, rec)
        sr.log(f"batch {i // BATCH + 1}/{batches} done ({pulled} pulled, {dupes} byte-duplicates skipped)")
        if i + BATCH < len(todo):
            time.sleep(BATCH_PAUSE_SECONDS)
    sr.log(f"DONE {pulled} pulled, {dupes} byte-duplicates skipped, {len(failed)} failed")
    return _settle(sr, failed, n_targets=len(targets), landed=len(done) + pulled)


def _mint_with_retry(sr: StageRun, ids: list[str]) -> list[dict[str, Any]]:
    try:
        return sr.seat.mint(sr.job.matter_id, ids)
    except Exception as exc:  # noqa: BLE001 - a mint failure of any kind is retried once; the second failure is recorded per file id below
        sr.log(f"MINT FAIL ({str(exc)[:120]}); retrying once")
        time.sleep(MINT_RETRY_PAUSE_SECONDS)
        try:
            return sr.seat.mint(sr.job.matter_id, ids)
        except Exception as exc2:  # noqa: BLE001 - the second mint failure is recorded per file id so the download stage reports exactly which files failed
            return [{"id": i, "error": f"mint failed twice: {str(exc2)[:120]}"} for i in ids]


def _settle(sr: StageRun, failed: list[dict[str, Any]], *, n_targets: int, landed: int) -> int:
    """The exit code, decided by what kind of failure is left rather than by how
    many there are. A caller that returns 1 here gets the pass repeated, so a
    failure a repeat cannot fix must not return 1."""
    if not failed:
        return 0
    absent = [r for r in failed if r.get("http_status") == HTTP_ABSENT]
    for r in absent:
        sr.log(f'NOT IN THE VENDOR\'S STORAGE (404): "{r.get("name")}" ({r.get("folder")})')
    retryable = [r for r in failed if r.get("http_status") != HTTP_ABSENT]
    if retryable:
        sr.log(
            f"{len(retryable)} of {n_targets} targets are not pulled for a reason a retry can fix; "
            f"the rows carry the reason"
        )
        return 1
    if not landed:
        # Disclosure is what makes an absent document safe to continue past, and
        # disclosure needs a document to sit in. With nothing pulled there is no
        # chronology to write, only an empty one to file on the firm's matter.
        sr.log(f"all {n_targets} targets are absent from the vendor's storage; there is nothing to chronicle")
        return 1
    sr.log(
        f"{len(absent)} of {n_targets} targets are absent from the vendor's storage and no retry can "
        f"produce them; each is named in the delivered document under Records Reviewed and "
        f"Limitations. Continuing with {landed} documents."
    )
    return 0
