"""`upload`: the deliverable set onto the matter, into its own dated folder.
$0. The last stage; nothing is written to the firm's system before it and
nothing after.

Idempotent by construction (ss#2614). The folder id is recorded in
`runs/<unit>/delivery.json` BEFORE the first file goes up, so a crash between
create_folder and the last add_file resumes by reconciling: list the folder,
add only the names that are missing or the wrong size. A folder of the target
name that this run did not create is somebody else's and the stage refuses
(exit 1); the runner never deletes anything on the matter.

`add_file` returning a null file id is normal and is not confirmation. The
only confirmation is the folder read back with every name at its byte count,
retried across the vendor's index lag; a short read-back after the retries is
exit 2 (held: the files may still be materializing).

That lag is also why the send decision may NOT rest on the vendor's list
alone. Live 2026-09-24 the read-back held at exit 2 with every file already
on the matter, and re-running the stage re-sent all 13 into the same folder:
the list still did not carry them, so `present` was empty and each name read
as missing. A resume cannot tell "absent" from "not indexed yet", and on that
ambiguity it wrote a second copy of an entire client filing -- which only a
DESTRUCTIVE delete can undo, and that is fail-closed on a firm's seat by
design. So each send is recorded in delivery.json AS IT HAPPENS, and a name
this run already sent (same sha, same bytes) is never sent twice; it goes
straight to the read-back. Ambiguity now resolves to exit 2, which a human
can clear, instead of to a duplicate, which they largely cannot.

**Index lag was only half of that (ss#2914).** The hold above could never have
cleared, however long the vendor took, because `_files_in` keyed on the
vendor's bare `name` while `expected` keys on the manifest filename -- and the
vendor returns the extension in a separate field. Every file read as short
forever. Read back off the live matter on 2026-09-24, 15 hours after the hold:
all 13 files present at their exact manifest byte counts, and `confirmed`
false on every row of delivery.json. A hold whose own message says the files
"may still be materializing" must be able to clear once they have; this one
could not, so the deliverable sat on the firm's matter while the job said it
had not arrived. `_vendor_name` composes the two fields the way every other
stage already did.

**A send is recorded BEFORE it starts, too (review 2026-10-06, N12).** The
`sent` flag was written only after `add_file` returned, and `add_file` is two
requests: a POST that creates the file's record in the folder, then a
presigned PUT of the bytes. A lost response or a failed PUT left no trace in
delivery.json, so a resume re-POSTed a same-name duplicate into the client's
folder. Now an intent row (`pending`) is written through before the POST and
turned into `sent` when it returns. A pending name is NEVER sent again by the
runner: the read-back looks for it by name across the index lag, a 0-byte row
counts as presence (the POST's placeholder) but not as confirmation, and a
pending name the read-back cannot find at its size holds (exit 2) for a person
to read the folder. A send failing outright holds the same way.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .base import StageRun

READBACK_TRIES = 8
READBACK_PAUSE_SECONDS = 15.0


def _folder_by_name(seat: Any, matter_id: str, name: str) -> dict[str, Any] | None:
    for f in seat.folder_tree(matter_id):
        if str(f.get("name") or "").strip() == name and not f.get("parentId"):
            return f
    return None


def _vendor_name(row: dict[str, Any]) -> str:
    """The full filename, which the vendor does not give us in one field.

    `seat.normalize_file` maps the vendor's row to `name` WITHOUT its extension
    and `ext` separately, so a bare `name` never equals a manifest filename.
    Every other stage that matches vendor rows against our own names already
    composes the two (`coverage.py`, `units.py`, `assemble.py`, `exhibits.py`,
    `group.py`); this stage was the one that did not, which is ss#2914.

    Measured against all 356 rows of a live matter on 2026-09-24: every `ext`
    carries its leading dot, and 6 of those names ALREADY ended with their own
    extension (an e-signature vendor's own filenames). Appending unconditionally
    would ask the read-back for `<name>.pdf.pdf` and read a present file as
    missing -- the same false negative one axis over.
    """
    name = str(row.get("name") or "")
    ext = str(row.get("ext") or "")
    if not ext or name.lower().endswith(ext.lower()):
        return name
    return name + ext


def _files_in(seat: Any, matter_id: str, folder_id: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in seat.list_files(matter_id):
        if str(f.get("folderId") or "") == str(folder_id) and not f.get("deleted"):
            out[_vendor_name(f)] = int(f.get("size") or 0)
    return out


def _cannot_say_what_was_sent(sr: StageRun, delivery: dict[str, Any], folder_id: str) -> bool:
    """True when the delivery record predates the `sent` flag (ss#2901).

    Before that flag existed the record was written ONCE, at the end of a run,
    for every manifest row regardless of whether it went up -- so a name in the
    list means "this was in the manifest", not "this was sent". Combined with a
    vendor list that lags minutes behind an upload, a resume reading such a
    record has no way to tell a file that is already on the matter from one that
    never left.

    Live 2026-09-24 that ambiguity filed a second copy of a 13-file package onto
    a firm's matter, which only an irreversible delete can undo -- and that is
    fail-closed on a customer seat by design. So the stage HOLDS instead. A hold
    is something a person clears; a duplicate is something they largely cannot.
    That is the same rule the `sent` flag itself was written to enforce; it was
    simply never applied to records written before the flag.
    """
    files = delivery.get("files") or []
    if not files or any("sent" in f for f in files):
        return False
    sr.log(
        f"delivery.json for folder {folder_id} predates the `sent` flag, so it cannot say which of {len(files)} "
        f"file(s) are already on the matter; holding rather than risking a second copy. Read the folder and, if "
        f"the package is there, mark the run delivered by hand."
    )
    return True


def _send_missing(
    sr: StageRun,
    seat: Any,
    matter_id: str,
    folder_id: str,
    manifest: list[dict[str, Any]],
    present: dict[str, int],
    delivery: dict[str, Any],
    delivery_path: Path,
) -> tuple[tuple[int, int] | None, str]:
    """Send whatever is not already up. `((sent, skipped_known), "ok")`;
    `(None, "refused")` when a manifest row's local bytes no longer match its
    sha; `(None, "held")` when a send failed and its outcome is unknown.

    A name this run already sent is NOT sent again: the vendor's list lags, so
    absence from `present` is not evidence the file is missing, and the delivery
    record is. Each send is written through to disk before the next one starts,
    so a crash mid-loop cannot lose the fact that a file is already up.
    """
    sent_before = {str(f.get("name")): f for f in (delivery.get("files") or []) if f.get("sent") or f.get("confirmed")}
    pending_before = {str(f.get("name")) for f in (delivery.get("files") or []) if f.get("pending")}

    def record(name: str, sha: str, nbytes: int, *, pending: bool) -> None:
        files = [f for f in (delivery.get("files") or []) if str(f.get("name")) != name]
        row: dict[str, Any] = {"name": name, "sha256": sha, "bytes": nbytes, "sent": not pending, "confirmed": False}
        if pending:
            row["pending"] = True
        files.append(row)
        delivery["files"] = files
        delivery_path.write_text(json.dumps(delivery, indent=1), encoding="utf-8")

    sent = skipped_known = 0
    for m in manifest:
        data = Path(m["local_path"]).read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        if sha != m["sha256"]:
            sr.log(f"{m['name']}: local bytes changed since the manifest (sha mismatch); refusing")
            return None, "refused"
        if present.get(m["name"]) == len(data):
            sr.log(f"  present  {m['name']}")
            continue
        prior = sent_before.get(m["name"])
        if prior and prior.get("sha256") == sha and prior.get("bytes") == len(data):
            skipped_known += 1
            sr.log(f"  sent earlier, not resending  {m['name']} (read-back will confirm)")
            continue
        if m["name"] in pending_before:
            # An earlier attempt started this send and never learned how it
            # ended: the POST may have created the record and the PUT may or
            # may not have landed. Re-POSTing would file a second copy, so the
            # read-back decides by name, and a person decides if it cannot.
            skipped_known += 1
            seen = f"yes, {present[m['name']]} bytes" if m["name"] in present else "not yet"
            sr.log(f"  send started earlier, outcome unknown; not resending  {m['name']} (in the listing: {seen})")
            continue
        record(m["name"], sha, len(data), pending=True)
        try:
            r = seat.add_file(matter_id, folder_id, m["name"], data)
        except Exception as exc:  # noqa: BLE001 - a failed send holds with its intent row on disk; resending could duplicate
            sr.log(
                f"{m['name']}: the send failed ({str(exc)[:160]}); the file's record may already exist in folder "
                f"{folder_id}, so it is not retried. Holding for a person to read the folder."
            )
            return None, "held"
        record(m["name"], sha, len(data), pending=False)
        sent += 1
        sr.log(f"  sent     {m['name']} ({len(data)} bytes; file id {(r or {}).get('fileId') or 'pending'})")
    return (sent, skipped_known), "ok"


def _write_readback(
    delivery: dict[str, Any], manifest: list[dict[str, Any]], present: dict[str, int], delivery_path: Path
) -> set[str]:
    """Rewrite delivery.json from the read-back; returns the names still
    pending going in."""
    up_already = {str(f.get("name")) for f in (delivery.get("files") or []) if f.get("sent") or f.get("confirmed")}
    pending = {str(f.get("name")) for f in (delivery.get("files") or []) if f.get("pending")}

    def final_row(m: dict[str, Any]) -> dict[str, Any]:
        confirmed = present.get(m["name"]) == m["bytes"]
        row: dict[str, Any] = {
            "name": m["name"],
            "sha256": m["sha256"],
            "bytes": m["bytes"],
            "sent": m["name"] in up_already or (m["name"] in pending and confirmed),
            "confirmed": confirmed,
        }
        # A pending send stays pending until the folder shows it at size: the
        # next attempt must keep refusing to resend it.
        if m["name"] in pending and not confirmed:
            row["pending"] = True
        return row

    delivery["files"] = [final_row(m) for m in manifest]
    delivery_path.write_text(json.dumps(delivery, indent=1), encoding="utf-8")
    return pending


def _note_unresolved(sr: StageRun, unresolved: list[str], folder_id: str) -> None:
    if unresolved:
        sr.log(
            f"{len(unresolved)} of them were sends whose outcome was never learned "
            f"({', '.join(unresolved)}); the runner will not resend them. A person reads folder {folder_id}: "
            f"if a file is missing or at 0 bytes, remove any partial copy there and clear its `pending` row "
            f"in delivery.json so the next attempt sends it once."
        )


def run(sr: StageRun, *, pause: float = READBACK_PAUSE_SECONDS, tries: int = READBACK_TRIES) -> int:
    out = sr.slug_dir / "out" / sr.unit.unit
    manifest = json.loads((out / "upload_manifest.json").read_text(encoding="utf-8"))
    if not manifest:
        sr.log("empty upload manifest")
        return 1
    folder_name = str(manifest[0]["folder"])
    matter_id = sr.job.matter_id
    delivery_path = sr.slug_dir / "runs" / sr.unit.unit / "delivery.json"
    delivery: dict[str, Any] = json.loads(delivery_path.read_text(encoding="utf-8")) if delivery_path.is_file() else {}
    seat = sr.seat

    if delivery.get("folder_id"):
        folder_id = str(delivery["folder_id"])
        sr.log(f"resuming into folder {folder_id} ('{folder_name}') created by this run")
    else:
        existing = _folder_by_name(seat, matter_id, folder_name)
        if existing:
            sr.log(
                f"a folder named '{folder_name}' already exists on the matter (id {existing.get('id')}) and this "
                f"run did not create it; refusing to write into it"
            )
            return 1
        created = seat.create_folder(matter_id, folder_name)
        folder_id = str(created.get("id") or created.get("folderId") or "")
        if not folder_id:
            again = _folder_by_name(seat, matter_id, folder_name)
            folder_id = str((again or {}).get("id") or "")
        if not folder_id:
            sr.log("create_folder returned no id and the folder is not visible on the matter")
            return 1
        delivery = {"folder": folder_name, "folder_id": folder_id, "files": []}
        delivery_path.parent.mkdir(parents=True, exist_ok=True)
        delivery_path.write_text(json.dumps(delivery, indent=1), encoding="utf-8")
        sr.log(f"created folder '{folder_name}' (id {folder_id})")

    if _cannot_say_what_was_sent(sr, delivery, folder_id):
        return 2
    present = _files_in(seat, matter_id, folder_id)
    counts, why = _send_missing(sr, seat, matter_id, folder_id, manifest, present, delivery, delivery_path)
    if counts is None:
        return 2 if why == "held" else 1
    sent, skipped_known = counts
    already = len(manifest) - sent - skipped_known
    sr.log(f"{sent} file(s) sent, {already} already present, {skipped_known} sent by an earlier attempt")

    expected = {m["name"]: m["bytes"] for m in manifest}
    for attempt in range(tries):
        present = _files_in(seat, matter_id, folder_id)
        short = [n for n, b in expected.items() if present.get(n) != b]
        if not short:
            break
        if attempt + 1 < tries:
            sr.log(f"read-back: {len(short)} of {len(expected)} not yet at size; waiting")
            time.sleep(pause)
    else:
        short = [n for n, b in expected.items() if _files_in(seat, matter_id, folder_id).get(n) != b]
    # `sent` is carried forward, never recomputed: losing it here would hand the
    # next attempt the same empty-list ambiguity that caused the duplicate. The
    # set is read back off delivery.json, which `_send_missing` has already
    # written through for every file it sent, so it covers this attempt and
    # every earlier one.
    pending = _write_readback(delivery, manifest, present, delivery_path)
    if short:
        sr.log(f"read-back short after {tries} tries: {', '.join(short)}")
        _note_unresolved(sr, [n for n in short if n in pending], folder_id)
        return 2
    sr.log(f"read-back complete: {len(expected)} file(s) at the expected byte counts in folder {folder_id}")
    return 0
