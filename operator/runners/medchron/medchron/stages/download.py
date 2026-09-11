"""`download`: pull the extraction set for a matter into `raw/`, verified, deduped
by content, logged to `raw_manifest.jsonl`.

Selection is the authored `include.json` (`decide_selection` writes it from the
firm's rules; nothing here knows a folder name). Presigned URLs are minted in
small batches immediately before each pull because they expire, and the seat
paces itself between mints. A file whose sha256 was already pulled is recorded
with `duplicate_of` and its bytes deleted, so downstream stages read one copy
and pay for one (a delivered matter once carried 10/265 byte-identical files,
all paid for twice).

Exit 1 when any target is still not pulled after the pass: the frozen script
printed the failures and exited 0, which is exactly the kind of outcome an
agent reading stdout would catch and a driver would not.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from ..seat import SeatError
from .base import StageRun, append_jsonl, read_json, read_jsonl

BATCH = 8
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

    pulled = dupes = failed = 0
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
                failed += 1
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
                failed += 1
            append_jsonl(log_path, rec)
        sr.log(f"batch {i // BATCH + 1}/{batches} done ({pulled} pulled, {dupes} byte-duplicates skipped)")
        if i + BATCH < len(todo):
            time.sleep(BATCH_PAUSE_SECONDS)
    sr.log(f"DONE {pulled} pulled, {dupes} byte-duplicates skipped, {failed} failed")
    if failed:
        sr.log(f"{failed} of {len(targets)} targets are not pulled; the rows carry the reason")
        return 1
    return 0


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
