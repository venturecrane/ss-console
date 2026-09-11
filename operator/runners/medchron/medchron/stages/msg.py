"""`index_msg` and `fold_msg`: open every Outlook container on a matter, index
the attachments hiding inside by content hash, and fold the decided ones into
`raw/` as ordinary documents. $0.

Why it exists: the pull takes document kinds only, so every .msg is skipped
and the records attached to those emails are never seen. On one delivered
matter 19 of 74 distinct PDF attachments existed only inside emails, and four
became exhibits.

What this file does NOT do is decide which images are records. Two sessions
measured byte and aspect rules and both failed in both directions (a 0.20 MB
photographed bill page dropped, a 0.33 MB banner admitted). So it keeps every
attachment whose kind is not on the short never-a-record list, hashes ALL of
them including the ones it does not keep (a discarded item that was never
compared cannot prove the rule cost nothing), and reports shape. The fold
decision is `decisions.fold`; this stage folds exactly what was decided and
refuses to fold nothing.

Output: msg_manifest.jsonl (the container pull), msg_pdfs/ (deduped bytes),
msg_attachments.json (the report the fold hook reads), and on fold: rows
appended to raw_manifest.jsonl with `from_email`, files as msgatt-<sha12><ext>.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from .base import StageRefusal, StageRun, append_jsonl, read_json, read_jsonl
from .download import BATCH, BATCH_PAUSE_SECONDS, _mint_with_retry

CONTAINER_EXTS = {".msg", ".rpmsg"}
# Attachment kinds that are never a medical record.
SKIP_EXT = {".gif", ".ico", ".p7s", ".vcf", ".ics", ".htm", ".html", ".txt", ".xml", ".eml"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
IMAGE_MIN = 50_000  # only true icons fall below this; signature glyphs are single-digit KB
DOC_EXT = {".doc", ".docx", ".rtf"}
# Microsoft RMS-protected payloads: undecryptable without the recipient's
# credentials. Reported by name so the hole is visible, never "opened".
ENCRYPTED_EXT = {".rpmsg"}
MIN_BYTES = 20_000  # inline signature images and logos
KEEP = {"pdf", "image", "doc"}


def classify(name: str, size: int) -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext in ENCRYPTED_EXT or name.lower().startswith("message_v2"):
        return "encrypted"
    if ext in SKIP_EXT:
        return "skipped-kind"
    if ext in IMAGE_EXT:
        return "image" if size >= IMAGE_MIN else "skipped-tiny"
    if ext in DOC_EXT:
        return "doc"
    if size < MIN_BYTES:
        return "skipped-tiny"
    if ext == ".pdf":
        return "pdf"
    return "other"


def image_dims(data: bytes) -> tuple[int, int] | None:
    try:
        import pymupdf

        px = pymupdf.Pixmap(data)
        return px.width, px.height
    except Exception:  # noqa: BLE001 - a blob pymupdf cannot open has no dimensions; None is the answer and the caller records it
        return None


def already_pulled_hashes(d: Path) -> dict[str, str]:
    """sha256 of everything the normal pull brought down. An EMPTY set means
    "not yet answerable", never "all new"; the fold refuses on it."""
    have: dict[str, str] = {}
    for r in read_jsonl(d / "raw_manifest.jsonl"):
        if r.get("ok") and r.get("sha256"):
            have.setdefault(r["sha256"], r.get("name") or r["id"])
    return have


def pull_containers(sr: StageRun, targets: list[dict[str, Any]], fpath: dict[str, str]) -> list[dict[str, Any]]:
    raw = sr.slug_dir / "msg_raw"
    raw.mkdir(parents=True, exist_ok=True)
    log_path = sr.slug_dir / "msg_manifest.jsonl"
    done = {r["id"] for r in read_jsonl(log_path) if r.get("ok")}
    todo = [t for t in targets if t["id"] not in done]
    sr.log(f"{sr.slug}: {len(targets)} containers on the matter, {len(done)} already pulled, {len(todo)} to pull")
    for i in range(0, len(todo), BATCH):
        batch = todo[i : i + BATCH]
        byid = {m["id"]: m for m in _mint_with_retry(sr, [b["id"] for b in batch])}
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
            else:
                dest = raw / f"{b['id']}.msg"
                try:
                    got = sr.seat.fetch(url, dest, b.get("size"))
                    rec.update(ok=True, path=str(dest), size_got=got)
                except Exception as exc:  # noqa: BLE001 - a fetch failure is recorded on the email's record and the partial file removed; the loop continues
                    dest.unlink(missing_ok=True)
                    rec.update(ok=False, error=str(exc)[:160])
            append_jsonl(log_path, rec)
        sr.log(f"  pulled {min(i + BATCH, len(todo))}/{len(todo)}")
        if i + BATCH < len(todo):
            time.sleep(BATCH_PAUSE_SECONDS)
    return read_jsonl(log_path)


class _Index:
    def __init__(self, d: Path, have: dict[str, str]) -> None:
        self.d, self.have = d, have
        self.by_hash: dict[str, dict[str, Any]] = {}
        self.errors: list[dict[str, Any]] = []
        self.encrypted: list[dict[str, Any]] = []
        self.dropped: list[dict[str, Any]] = []
        self.counts = {"pdf": 0, "image": 0, "doc": 0, "other": 0, "encrypted": 0, "skipped-kind": 0, "skipped-tiny": 0}
        self.outdir = d / "msg_pdfs"
        self.outdir.mkdir(parents=True, exist_ok=True)

    def add_standalone_encrypted(self, rec: dict[str, Any]) -> None:
        self.counts["encrypted"] += 1
        self.encrypted.append(
            {
                "email": rec["name"],
                "attachment": rec["name"],
                "bytes": rec.get("size_got") or rec.get("size_expected") or 0,
                "standalone": True,
            }
        )

    def add_attachment(self, subject: str, name: str, data: bytes) -> None:
        kind = classify(name, len(data))
        self.counts[kind] = self.counts.get(kind, 0) + 1
        h = hashlib.sha256(data).hexdigest()  # hash EVERYTHING, kept or not
        if kind == "encrypted":
            self.encrypted.append({"email": subject, "attachment": name, "bytes": len(data)})
        if kind not in KEEP:
            self.dropped.append(
                {
                    "kind": kind,
                    "attachment": name,
                    "bytes": len(data),
                    "email": subject,
                    "sha12": h[:12],
                    "already_pulled_as": self.have.get(h),
                }
            )
            return
        if h not in self.by_hash:
            ext = os.path.splitext(name)[1].lower() or ".bin"
            out = self.outdir / f"{h[:12]}{ext}"
            out.write_bytes(data)
            rec_a: dict[str, Any] = {
                "sha256": h,
                "bytes": len(data),
                "kind": kind,
                "local": out.name,
                "already_pulled_as": self.have.get(h),
                "names": [],
                "emails": [],
            }
            if kind == "image":
                wh = image_dims(data)
                if wh:
                    rec_a["px"] = f"{wh[0]}x{wh[1]}"
                    rec_a["aspect"] = round(wh[0] / wh[1], 2)
            self.by_hash[h] = rec_a
        if name not in self.by_hash[h]["names"]:
            self.by_hash[h]["names"].append(name)
        if subject not in self.by_hash[h]["emails"]:
            self.by_hash[h]["emails"].append(subject)


def index_containers(sr: StageRun, pulled: list[dict[str, Any]], have: dict[str, str]) -> tuple[_Index, int]:
    import extract_msg

    ix = _Index(sr.slug_dir, have)
    msgs = [r for r in pulled if r.get("ok") and r.get("path")]
    for n, rec in enumerate(msgs, 1):
        if (rec.get("ext") or "").lower() in ENCRYPTED_EXT:
            ix.add_standalone_encrypted(rec)  # not OLE2; extract_msg cannot open it
            continue
        try:
            m = extract_msg.Message(rec["path"])
        except Exception as exc:  # noqa: BLE001 - extract_msg raising on a container is recorded as that email's error; the loop continues
            ix.errors.append({"email": rec["name"], "error": str(exc)[:160]})
            continue
        try:
            subject = (m.subject or rec["name"])[:120]
            for att in m.attachments:
                name = att.longFilename or att.shortFilename or "unnamed"
                try:
                    data = att.data
                except Exception as exc:  # noqa: BLE001 - an attachment whose payload cannot be read is recorded as that attachment's error; the rest are still indexed
                    ix.errors.append({"email": subject, "attachment": name, "error": str(exc)[:120]})
                    continue
                if not isinstance(data, bytes):
                    ix.counts["other"] += 1  # a nested .msg comes back as a Message
                    continue
                ix.add_attachment(subject, name, data)
        finally:
            try:
                m.close()
            except Exception:  # noqa: BLE001 - closing the message in finally must not mask an error already recorded for it
                pass
        if n % 25 == 0:
            sr.log(f"  opened {n}/{len(msgs)}, distinct attachments so far: {len(ix.by_hash)}")
    return ix, len(msgs)


def run_index(sr: StageRun) -> int:
    d = sr.slug_dir
    fpath = sr.folder_paths()
    targets = [x for x in sr.manifest() if not x.get("deleted") and (x.get("ext") or "").lower() in CONTAINER_EXTS]
    have = already_pulled_hashes(d)
    comparable = bool(have)
    if not targets:
        sr.log(f"{sr.slug}: no .msg on this matter, nothing to index")
        _write_report(d, comparable, [], {}, [], 0, [], [])
        return 0
    pulled = pull_containers(sr, targets, fpath)
    ix, opened = index_containers(sr, pulled, have)
    idx = sorted(ix.by_hash.values(), key=lambda r: -r["bytes"])
    new = [r for r in idx if not r["already_pulled_as"]]
    if not comparable:
        sr.log("raw_manifest.jsonl is empty for this slug: every attachment reads NEW by construction")
    _write_report(d, comparable, idx, ix.counts, ix.errors, opened, ix.encrypted, ix.dropped)
    sr.log(
        f"emails opened {opened}; distinct attachments {len(idx)}; already in the pull "
        f"{len(idx) - len(new)}; {'new to the corpus' if comparable else 'unverified (no baseline)'} "
        f"{len(new)}; encrypted {len(ix.encrypted)}; errors {len(ix.errors)}"
    )
    return 0


def _write_report(
    d: Path,
    comparable: bool,
    idx: list[dict[str, Any]],
    counts: dict[str, int],
    errors: list,
    opened: int,
    encrypted: list,
    dropped: list,
) -> None:
    new = [r for r in idx if not r["already_pulled_as"]]
    (d / "msg_attachments.json").write_text(
        json.dumps(
            {
                "comparable": comparable,
                "distinct_attachments": len(idx),
                "new_to_the_corpus": len(new) if comparable else None,
                "already_pulled": len(idx) - len(new),
                "attachment_counts": counts,
                "emails_opened": opened,
                "encrypted": encrypted,
                "dropped_unkept": dropped,
                "errors": errors,
                "attachments": idx,
            },
            indent=1,
        ),
        encoding="utf-8",
    )


def run_fold(sr: StageRun) -> int:
    """Fold the decided attachments into raw/ as documents. An allowlist, never
    a class; refuses on no baseline, on an empty list, on an unknown hash, and
    on an attachment the pull already holds."""
    d = sr.slug_dir
    chosen = list(sr.decided.get("fold") or read_json(d / "msg_fold.json", {}).get("fold") or [])
    report = read_json(d / "msg_attachments.json", {})
    idx = report.get("attachments") or []
    if not report.get("comparable"):
        raise StageRefusal("refusing to fold: no raw_manifest.jsonl baseline, so 'new' is every attachment")
    if not chosen:
        if not idx or not any(not r.get("already_pulled_as") for r in idx):
            sr.log("nothing new inside the emails; nothing to fold")
            return 0
        raise StageRefusal("refusing to fold: the decided list is empty while new attachments exist")
    by12 = {r["sha256"][:12]: r for r in idx}
    unknown = [c for c in chosen if c not in by12]
    if unknown:
        raise StageRefusal(f"refusing to fold: unknown hashes {unknown}")
    picks = [by12[c] for c in chosen]
    already = [r["names"][0] for r in picks if r.get("already_pulled_as")]
    if already:
        raise StageRefusal(f"refusing to fold {len(already)} attachment(s) already in the pull: {already}")
    raw = d / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for r in picks:
        ext = os.path.splitext(r["local"])[1] or ".pdf"
        dest = raw / f"msgatt-{r['sha256'][:12]}{ext}"
        shutil.copyfile(d / "msg_pdfs" / r["local"], dest)
        append_jsonl(
            d / "raw_manifest.jsonl",
            {
                "id": f"msgatt-{r['sha256'][:12]}",
                "name": r["names"][0],
                "ext": ext,
                "folder": "/(email attachment)",
                "from_email": r["emails"][0] if r["emails"] else None,
                "size_expected": r["bytes"],
                "size_got": r["bytes"],
                "sha256": r["sha256"],
                "path": str(dest),
                "ok": True,
            },
        )
    sr.log(f"FOLDED {len(picks)} attachment(s), {sum(r['bytes'] for r in picks):,} bytes, into raw/")
    return 0
