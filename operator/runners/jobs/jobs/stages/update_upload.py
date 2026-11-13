"""`upload` for an update: file the updated chronology beside the delivered
one, the safe way, then retire the delivered document. The only stage of an
update that writes to the firm's system.

The order is the whole point, and it is the order the laptop's replace tool
was rewritten to after a delete-first script once left a matter holding no
chronology at all:

1. the delivered chronology's folder is re-read and must still hold the
   delivered document BY ID (a firm that moved or replaced it since is the
   firm's decision; the update holds rather than act on a stale plan);
2. a file in that folder carrying a name this update would send, and that
   this run did not send (a same-day update's chronology name is the
   delivered document's own), is never written over: the new file is
   VERSIONED, "<name> (2)<ext>", and the manifest rewritten before any send so
   a resume keeps that name;
3. the updated document and the new exhibits go up through the one filing
   (``jobs/filing.file_into_folder``: intent rows written through, never sent
   twice, the listing read back at size, then every file downloaded by id and
   hashed);
4. ONLY when every new file is proven by hash, and no send raised in this
   attempt, is the delivered chronology document deleted, addressed by its id,
   never by name; and the folder is read back until that id reads gone. A send
   that raised and was then proven leaves the delivered chronology to the
   resume (wave 3): a deletion never rides on an attempt that saw an anomaly.

Nothing else is ever deleted: not the delivered exhibits (the updated document
still cites them), not a worksheet, not a file the firm authored in that
folder. A delete that fails, or an id that still reads present, holds (exit 4)
with both documents on the matter: a duplicate the firm can see is the safe
failure; a matter with no chronology is not. ``delivery.json`` records the
replacement, so a resume never deletes twice and the ledger names what was
retired.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .. import filing
from ..filing import SendRecord, full_name
from ..proof.readback import READBACK_PAUSE_SECONDS, READBACK_TRIES
from .base import StageRun
from .update_base import plan

#: The stage's exits (dag_update.py reads EXITS), split 2026-10-24 like the
#: full build's (stages/upload.py). Exit 2 used to cover every hold, under a
#: reason requeue.UPLOAD_SHORT does not match, so an update whose new files
#: Smokeball had not listed yet parked for a person instead of being re-run.
#: Now only the short read-back exits 2, under that prefix; a send whose
#: outcome is unknown exits 3; the delivered chronology's own state (moved,
#: not retired, still listed after its delete, files not proven) exits 4 and
#: its `update_held:` line is the reason (unit_proof.paged_line).
EXIT_REFUSED, EXIT_SHORT, EXIT_SEND_UNKNOWN, EXIT_HELD = 1, 2, 3, 4
EXITS = {
    EXIT_REFUSED: (
        "failed",
        "upload: refused (a name collision in the chronology folder, changed local bytes, or other bytes read back)",
    ),
    EXIT_SHORT: (
        "held",
        "upload: the folder read back short after the retries; the new files may still be materializing, and the "
        "delivered chronology is untouched",
    ),
    EXIT_SEND_UNKNOWN: (
        "held",
        "upload: a send failed and its outcome is unknown; a person reads the folder; the delivered chronology is "
        "untouched",
    ),
    EXIT_HELD: ("held", "update_held: the delivered chronology needs a person (moved, not retired, or still listed)"),
}


def _folder_rows(seat: Any, matter_id: str, folder_id: str) -> list[dict[str, Any]]:
    return [f for f in seat.list_files(matter_id) if str(f.get("folderId") or "") == folder_id and not f.get("deleted")]


def _collisions(rows: list[dict[str, Any]], manifest: list[dict[str, Any]], ours: set[str]) -> list[str]:
    """Manifest names already in the folder that this run did not send."""
    want = {m["name"] for m in manifest}
    return sorted(n for n in (full_name(r) for r in rows) if n in want and n not in ours)


def versioned(name: str, taken: set[str]) -> str:
    """`name` with the first " (k)" (k >= 2) before its extension that no file
    in `taken` carries: "X 10-13-26.docx" -> "X 10-13-26 (2).docx"."""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    k = 2
    while True:
        cand = f"{stem} ({k})" + (f".{ext}" if ext else "")
        if cand.lower() not in {t.lower() for t in taken}:
            return cand
        k += 1


def _chosen_before(name: str, ours: set[str]) -> str | None:
    """The numbered name an earlier attempt chose for ``name`` ("X (k).docx"),
    when this run's own record carries one."""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    rx = re.compile(re.escape(stem) + r" \((\d+)\)" + (re.escape("." + ext) if ext else "") + "$", re.I)
    found = sorted((int(m.group(1)), n) for n in ours if (m := rx.match(n)))
    return found[0][1] if found else None


def _keep_chosen(sr: StageRun, manifest: list[dict[str, Any]], ours: set[str]) -> bool:
    """A manifest rebuilt under its intended name (the records review is
    rebuilt every attempt) takes back the numbered name an earlier attempt
    chose and sent or started, so a resume never numbers it again and files a
    second copy (wave 3). True when any name was taken back."""
    kept = False
    for m in manifest:
        if m["name"] in ours:
            continue
        before = _chosen_before(m["name"], ours)
        if before:
            sr.log(f"keeping the name {before} an earlier attempt of this update chose for {m['name']}")
            m.setdefault("intended_name", m["name"])
            m["name"] = before
            kept = True
    return kept


def _version_clashes(
    sr: StageRun, out: Path, rows: list[dict[str, Any]], manifest: list[dict[str, Any]], ours: set[str]
) -> list[dict[str, Any]]:
    """A manifest name a file in the folder already carries, and that this run
    did not send (a same-day update's chronology name equals the delivered
    one's; the firm may have filed a file of that name), is VERSIONED: the new
    file goes up as "<name> (2)<ext>" and nothing in the folder is written
    over or renamed. The manifest is rewritten before anything is sent, so a
    resume sends under the same name and never versions twice."""
    kept = _keep_chosen(sr, manifest, ours)
    clash = set(_collisions(rows, manifest, ours))
    if not clash:
        if kept:
            (out / "upload_manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        return manifest
    taken = {full_name(r) for r in rows} | {m["name"] for m in manifest}
    for m in manifest:
        if m["name"] in clash:
            new = versioned(m["name"], taken)
            sr.log(
                f"the chronology folder already holds a file named {m['name']} that this update did not send; filing as {new}"
            )
            m.setdefault("intended_name", m["name"])
            m["name"] = new
            taken.add(new)
    (out / "upload_manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def record_of(sr: StageRun, p: dict[str, Any]) -> SendRecord:
    """The update's send record, marked an update into the delivered folder."""
    rec = SendRecord.load(sr.slug_dir / "runs" / sr.unit.unit / "delivery.json")
    rec.doc.update({"folder": p["folder_name"], "folder_id": str(p["folder_id"]), "update": True})
    rec.doc.setdefault("files", [])
    return rec


def prepare(
    sr: StageRun, out: Path, rec: SendRecord, rows: list[dict[str, Any]], manifest: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The manifest with every clash against ``rows`` versioned; the manifest
    and the record are both written before anything is sent. Shared with
    ``update_review_file``."""
    manifest = _version_clashes(sr, out, rows, manifest, rec.ours())
    rec.save()
    return manifest


def _retire(
    sr: StageRun,
    seat: Any,
    matter_id: str,
    folder_id: str,
    old: dict[str, Any],
    rec: SendRecord,
    tries: int,
    pause: float,
) -> int:
    """Delete the delivered chronology by id, then read the folder until it is gone."""
    done = rec.replaced(old)
    if not done.get("deleted"):
        try:
            seat.delete_file(matter_id, old["id"])
        except Exception as exc:  # noqa: BLE001 - a failed delete leaves both documents on the matter; that is the hold
            sr.log(
                f"update_held: the delivered chronology could not be retired ({str(exc)[:160]}); both are on the matter"
            )
            rec.save()
            return EXIT_HELD
        done["deleted"] = True
        rec.save()
    for attempt in range(tries):
        if not any(str(r.get("id")) == old["id"] for r in _folder_rows(seat, matter_id, folder_id)):
            done["confirmed_gone"] = True
            rec.save()
            sr.log(f"retired the delivered chronology {old['name']} (id {old['id']}); read back gone")
            return 0
        if attempt + 1 < tries:
            time.sleep(pause)
    sr.log(f"update_held: the delivered chronology (id {old['id']}) still reads present after its delete")
    return EXIT_HELD


def _filed_exit(sr: StageRun, filed: filing.Filed, tries: int) -> int | None:
    """The stage's exit for a filing that did not end proven, else None."""
    if filed.status in ("held", "refused"):
        return EXIT_SEND_UNKNOWN if filed.status == "held" else EXIT_REFUSED
    if filed.status == "short":
        sr.log(f"read-back short after {tries} tries: {', '.join(filed.short)}; the delivered chronology is untouched")
        return EXIT_SHORT
    if filed.status == "unproven":
        sr.log("the new files are not proven by hash; the delivered chronology is untouched")
        return filed.rc
    return None


def run(sr: StageRun, *, pause: float = READBACK_PAUSE_SECONDS, tries: int = READBACK_TRIES) -> int:
    out = sr.slug_dir / "out" / sr.unit.unit
    manifest = json.loads((out / "upload_manifest.json").read_text(encoding="utf-8"))
    p = plan(sr)
    if not manifest or not p:
        sr.log("update upload: no manifest or no update plan")
        return EXIT_REFUSED
    matter_id, folder_id, old = sr.job.matter_id, str(p["folder_id"]), p["chronology"]
    seat = sr.seat
    rows = _folder_rows(seat, matter_id, folder_id)
    rec = record_of(sr, p)
    retired = bool((rec.doc.get("replaced") or {}).get("deleted"))
    if not retired and not any(str(r.get("id")) == old["id"] for r in rows):
        sr.log(f"update_held: the delivered chronology (id {old['id']}) is no longer in its folder; nothing was filed")
        return EXIT_HELD
    manifest = prepare(sr, out, rec, rows, manifest)
    scratch = sr.slug_dir / "runs" / sr.unit.unit / "readback"
    filed = filing.file_into_folder(
        seat, matter_id, folder_id, manifest, rec, sr.log, scratch, tries=tries, pause=pause
    )
    rc = _filed_exit(sr, filed, tries)
    if rc is not None:
        return rc
    if not all(f.get("proven") for f in rec.files()):
        sr.log("update_held: not every new file is proven; the delivered chronology is untouched")
        return EXIT_HELD
    if filed.raised:
        sr.log(
            "update_held: every new file is proven by hash, but a send raised in this attempt; the delivered "
            "chronology is retired by the resume, never in an attempt that saw a send fail"
        )
        return EXIT_HELD
    return _retire(sr, seat, matter_id, folder_id, old, rec, tries, pause)
