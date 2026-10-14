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
   this run did not send, refuses: a name collision is never resolved by
   writing over a file that is not ours;
3. the updated document and the new exhibits go up through the ordinary
   upload machinery (intent rows written through, never sent twice, the
   listing read back at size, then every file downloaded by id and hashed);
4. ONLY when every new file is proven by hash is the delivered chronology
   document deleted, addressed by its id, never by name; and the folder is
   read back until that id reads gone.

Nothing else is ever deleted: not the delivered exhibits (the updated document
still cites them), not a worksheet, not a file the firm authored in that
folder. A delete that fails, or an id that still reads present, holds (exit 2)
with both documents on the matter: a duplicate the firm can see is the safe
failure; a matter with no chronology is not. ``delivery.json`` records the
replacement, so a resume never deletes twice and the ledger names what was
retired.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .base import StageRun
from .update_base import plan
from .upload import (
    READBACK_PAUSE_SECONDS,
    READBACK_TRIES,
    _files_in,
    _prove,
    _send_missing,
    _write_readback,
    _vendor_name,
)


def _folder_rows(seat: Any, matter_id: str, folder_id: str) -> list[dict[str, Any]]:
    return [f for f in seat.list_files(matter_id) if str(f.get("folderId") or "") == folder_id and not f.get("deleted")]


def _collisions(rows: list[dict[str, Any]], manifest: list[dict[str, Any]], ours: set[str]) -> list[str]:
    """Manifest names already in the folder that this run did not send."""
    want = {m["name"] for m in manifest}
    return sorted(n for n in (_vendor_name(r) for r in rows) if n in want and n not in ours)


def _readback(
    sr: StageRun, seat: Any, matter_id: str, folder_id: str, manifest: list[dict], tries: int, pause: float
) -> list[str]:
    expected = {m["name"]: m["bytes"] for m in manifest}
    short = list(expected)
    for attempt in range(tries):
        present = _files_in(seat, matter_id, folder_id)
        short = [n for n, b in expected.items() if present.get(n) != b]
        if not short:
            break
        if attempt + 1 < tries:
            time.sleep(pause)
    return short


def _retire(
    sr: StageRun,
    seat: Any,
    matter_id: str,
    folder_id: str,
    old: dict[str, Any],
    delivery: dict,
    dpath: Path,
    tries: int,
    pause: float,
) -> int:
    """Delete the delivered chronology by id, then read the folder until it is gone."""
    rec = delivery.setdefault("replaced", {"file_id": old["id"], "name": old["name"], "deleted": False})
    if not rec.get("deleted"):
        try:
            seat.delete_file(matter_id, old["id"])
        except Exception as exc:  # noqa: BLE001 - a failed delete leaves both documents on the matter; that is the hold
            sr.log(
                f"update_held: the delivered chronology could not be retired ({str(exc)[:160]}); both are on the matter"
            )
            dpath.write_text(json.dumps(delivery, indent=1), encoding="utf-8")
            return 2
        rec["deleted"] = True
        dpath.write_text(json.dumps(delivery, indent=1), encoding="utf-8")
    for attempt in range(tries):
        if not any(str(r.get("id")) == old["id"] for r in _folder_rows(seat, matter_id, folder_id)):
            rec["confirmed_gone"] = True
            dpath.write_text(json.dumps(delivery, indent=1), encoding="utf-8")
            sr.log(f"retired the delivered chronology {old['name']} (id {old['id']}); read back gone")
            return 0
        if attempt + 1 < tries:
            time.sleep(pause)
    sr.log(f"update_held: the delivered chronology (id {old['id']}) still reads present after its delete")
    return 2


def run(sr: StageRun, *, pause: float = READBACK_PAUSE_SECONDS, tries: int = READBACK_TRIES) -> int:
    out = sr.slug_dir / "out" / sr.unit.unit
    manifest = json.loads((out / "upload_manifest.json").read_text(encoding="utf-8"))
    p = plan(sr)
    if not manifest or not p:
        sr.log("update upload: no manifest or no update plan")
        return 1
    matter_id, folder_id, old = sr.job.matter_id, str(p["folder_id"]), p["chronology"]
    dpath = sr.slug_dir / "runs" / sr.unit.unit / "delivery.json"
    delivery: dict[str, Any] = json.loads(dpath.read_text(encoding="utf-8")) if dpath.is_file() else {}
    delivery.update({"folder": p["folder_name"], "folder_id": folder_id, "update": True})
    delivery.setdefault("files", [])
    seat = sr.seat
    rows = _folder_rows(seat, matter_id, folder_id)
    retired = bool((delivery.get("replaced") or {}).get("deleted"))
    if not retired and not any(str(r.get("id")) == old["id"] for r in rows):
        sr.log(f"update_held: the delivered chronology (id {old['id']}) is no longer in its folder; nothing was filed")
        return 2
    ours = {str(f.get("name")) for f in delivery["files"] if f.get("sent") or f.get("pending") or f.get("confirmed")}
    clash = _collisions(rows, manifest, ours)
    if clash:
        sr.log(f"the chronology folder already holds file(s) named {clash} that this update did not send; refusing")
        return 1
    dpath.parent.mkdir(parents=True, exist_ok=True)
    dpath.write_text(json.dumps(delivery, indent=1), encoding="utf-8")
    present = _files_in(seat, matter_id, folder_id)
    counts, why = _send_missing(sr, seat, matter_id, folder_id, manifest, present, delivery, dpath)
    if counts is None:
        return 2 if why == "held" else 1
    short = _readback(sr, seat, matter_id, folder_id, manifest, tries, pause)
    _write_readback(delivery, manifest, _files_in(seat, matter_id, folder_id), dpath)
    if short:
        sr.log(f"read-back short after {tries} tries: {', '.join(short)}; the delivered chronology is untouched")
        return 2
    rc = _prove(sr, seat, matter_id, folder_id, manifest, delivery, dpath, pause=pause, tries=tries)
    if rc != 0:
        sr.log("the new files are not proven by hash; the delivered chronology is untouched")
        return rc
    delivery = json.loads(dpath.read_text(encoding="utf-8"))
    if not all(f.get("proven") for f in delivery.get("files") or []):
        sr.log("update_held: not every new file is proven; the delivered chronology is untouched")
        return 2
    return _retire(sr, seat, matter_id, folder_id, old, delivery, dpath, tries, pause)
