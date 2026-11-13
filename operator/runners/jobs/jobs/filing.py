"""One way to file finished work into a firm's Smokeball (wave 3).

Before this module the filing was written four times: a new dated folder
(``stages/upload.py``, driven by demand and drafting through a stand-in and by
the chronology update), a standing folder (``litigation/deliver.py``, used by
litigation, casefile, deadlines and discovery12), a new version of a filed file
(``drafting/revise/versions.py``) and a library file
(``library_setup/file.py``). Each re-coded the send record, the read-back
retries and the full-filename rule, and two disagreed on what a send that
raises means. Now a fix to how a file is sent, recorded, read back or named
lands here once and reaches every job that files a new file. Revise and the
library write over a filed file by id, under their own compare-and-stop, and
keep it; they share the read-back constants and ``remote_sha``.

What every filing here keeps, each one a lesson paid for on a firm's matter:

* **the full filename** (``full_name``): the vendor gives the name and its
  extension in two fields, and some names already carry their extension
  (ss#2914; 6 of 356 rows on one live matter);
* **an intent row before every send** (``SendRecord``): ``add_file`` is a POST
  then a PUT, so a lost response leaves a file on the matter and no trace; a
  name with an intent or a sent row is never sent again by the runner
  (review 2026-10-06, N12). The vendor's listing lags minutes behind an upload,
  so absence from it is never evidence a file is missing (2026-09-24: a second
  copy of a 13-file package);
* **proven, not claimed**: the folder listed until every name is at its byte
  count, then each file downloaded by id and its sha256 compared with the
  bytes built (``proof/readback.py``, 2026-10-13).

THE ONE RULE FOR A SEND THAT RAISES (wave 3; before it the dated folder held
and the standing folder read back). The intent row stays and THAT ONE NAME is
read back by hash; any exception in that read-back counts as not proven. A
proven name is marked sent and the next file is sent. A name not proven stops
the sending: the job holds (exit 3, a person reads the folder), as before. An
update never retires the delivered chronology in an attempt where a send
raised; its resume does.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .proof import readback

Log = Callable[[str], None]

#: The new-folder filing's exits (``stages/upload.EXITS`` reads them). Only
#: EXIT_SHORT is a wait the daemon re-runs (``requeue.UPLOAD_SHORT``).
EXIT_REFUSED, EXIT_SHORT, EXIT_SEND_UNKNOWN, EXIT_PRE_FLAG = 1, 2, 3, 4
#: The highest " (n)" a delivery folder name is numbered to before the filing
#: stops: past this, something is creating same-name folders in a loop.
FOLDER_SUFFIX_LIMIT = 20


# ---- the filename rule ----------------------------------------------------------------
def folded_ext(row: dict[str, Any]) -> str:
    """The extension lower-cased with its dot, from ``ext`` or else the name."""
    ext = str(row.get("ext") or "").strip().lower()
    if ext and not ext.startswith("."):
        ext = "." + ext
    if ext:
        return ext
    name = str(row.get("name") or "")
    return ("." + name.rsplit(".", 1)[1].lower()) if "." in name else ""


def full_name(row: dict[str, Any], fold: bool = False) -> str:
    """A vendor row's full filename: its name, then its extension unless the
    name already ends with it (any case). Measured on all 356 rows of a live
    matter on 2026-09-24: every ``ext`` carries its leading dot, and 6 names
    already ended with their own extension (an e-signature vendor's), so an
    unconditional join would read a present file as missing (ss#2914, one axis
    over). ``drafting/revise/versions.py`` keeps its own unconditional join
    (left out of this rule in wave 3; the filing goldens' ``revise`` cells
    record the one name shape where the two differ).

    ``fold``: the name stripped and the extension lower-cased, taken from the
    name when ``ext`` is empty (what the negotiation watch matches on)."""
    name, ext = str(row.get("name") or ""), str(row.get("ext") or "")
    if fold:
        name, ext = name.strip(), folded_ext(row)
    return name if not ext or name.lower().endswith(ext.lower()) else name + ext


def files_in(seat: Any, matter_id: str, folder_id: str) -> dict[str, int]:
    """``{full name: size}`` for the live files in one folder."""
    out: dict[str, int] = {}
    for f in seat.list_files(matter_id):
        if str(f.get("folderId") or "") == str(folder_id) and not f.get("deleted"):
            out[full_name(f)] = int(f.get("size") or 0)
    return out


def file_row(seat: Any, matter_id: str, folder_id: str, name: str) -> dict[str, Any] | None:
    """The live vendor row of ``name`` in one folder, or None."""
    for f in seat.list_files(matter_id):
        if str(f.get("folderId") or "") == folder_id and not f.get("deleted") and full_name(f) == name:
            return f
    return None


def top_folder(seat: Any, matter_id: str, name: str) -> dict[str, Any] | None:
    for f in seat.folder_tree(matter_id):
        if str(f.get("name") or "").strip() == name and not f.get("parentId"):
            return f
    return None


# ---- the send record ------------------------------------------------------------------
class SendRecord:
    """Every send record shape on disk, written atomically, every key kept.

    * many files (``runs/<unit>/delivery.json``): ``folder``, ``folder_id``,
      ``files`` (rows ``name, sha256, bytes, sent, confirmed`` plus ``pending``
      while a send's outcome is unknown, ``file_id`` and ``proven`` once read
      back by hash), and on an update ``update`` and the ``replaced`` block;
    * one file in a standing folder (``delivery*.json``): the same facts flat,
      ``folder_id, name, sha256, bytes, pending, sent, confirmed, file_id``."""

    def __init__(self, path: Path, doc: dict[str, Any] | None = None) -> None:
        self.path = path
        self.doc: dict[str, Any] = doc if doc is not None else {}

    @classmethod
    def load(cls, path: Path) -> SendRecord:
        return cls(path, json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        tmp.write_text(json.dumps(self.doc, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    # many files
    def files(self) -> list[dict[str, Any]]:
        return list(self.doc.get("files") or [])

    def sent_before(self) -> dict[str, dict[str, Any]]:
        return {str(f.get("name")): f for f in self.files() if f.get("sent") or f.get("confirmed")}

    def pending(self) -> set[str]:
        return {str(f.get("name")) for f in self.files() if f.get("pending")}

    def recorded(self) -> dict[str, dict[str, Any]]:
        """Every row this record says was sent or started, by name."""
        return {str(f.get("name")): f for f in self.files() if f.get("sent") or f.get("pending") or f.get("confirmed")}

    def ours(self) -> set[str]:
        """Every name this record says was sent or started."""
        return set(self.recorded())

    def predates_sent_flag(self) -> bool:
        files = self.files()
        return bool(files) and not any("sent" in f for f in files)

    def put(self, name: str, sha: str, nbytes: int, *, pending: bool) -> None:
        """One row, written through: an intent before the send, sent after it."""
        files = [f for f in self.files() if str(f.get("name")) != name]
        row: dict[str, Any] = {"name": name, "sha256": sha, "bytes": nbytes, "sent": not pending, "confirmed": False}
        if pending:
            row["pending"] = True
        files.append(row)
        self.doc["files"] = files
        self.save()

    def write_readback(self, manifest: list[dict[str, Any]], present: dict[str, int]) -> set[str]:
        """Every manifest row from the read-back; returns the names still
        pending going in. ``sent`` is carried forward, never recomputed, a
        pending send stays pending until the folder shows it at size, and a
        sent or pending row for a name the manifest no longer carries is kept."""
        up_already, pending = set(self.sent_before()), self.pending()

        def final_row(m: dict[str, Any]) -> dict[str, Any]:
            confirmed = present.get(m["name"]) == m["bytes"]
            row: dict[str, Any] = {
                "name": m["name"],
                "sha256": m["sha256"],
                "bytes": m["bytes"],
                "sent": m["name"] in up_already or (m["name"] in pending and confirmed),
                "confirmed": confirmed,
            }
            if m["name"] in pending and not confirmed:
                row["pending"] = True
            return row

        names = {m["name"] for m in manifest}
        # A row for a name the manifest no longer carries keeps its flags: a
        # record that forgets a send is a record that can send it twice.
        kept = [f for f in self.files() if str(f.get("name")) not in names and (f.get("sent") or f.get("pending"))]
        self.doc["files"] = [final_row(m) for m in manifest] + kept
        self.save()
        return pending

    def mark_proven(self, proven: list[dict[str, Any]]) -> None:
        by_name = {f["name"]: f for f in proven}
        for row in self.doc.get("files") or []:
            p = by_name.get(str(row.get("name")))
            if p:
                row.update({"file_id": p["file_id"], "sha256": p["sha256"], "proven": True})
        self.save()

    # the update's retirement of the delivered chronology
    def replaced(self, old: dict[str, Any]) -> dict[str, Any]:
        return self.doc.setdefault("replaced", {"file_id": old["id"], "name": old["name"], "deleted": False})


# ---- the pieces -------------------------------------------------------------------------
@dataclass
class Sent:
    """What one pass of ``send_missing`` did."""

    sent: int = 0
    skipped: int = 0
    #: Sends that raised and were then proven on the matter by hash.
    raised: int = 0


def prove_one(
    seat: Any,
    matter_id: str,
    folder_id: str,
    name: str,
    sha: str,
    size: int,
    scratch: Path,
    *,
    tries: int,
    pause: float,
) -> dict[str, Any] | None:
    """The vendor row of ``name`` once it is in the folder at ``size`` and its
    downloaded bytes hash to ``sha``; None when it never got there. Raises
    ``ReadbackMismatch`` when the bytes there are not ours."""
    for attempt in range(tries):
        row = file_row(seat, matter_id, folder_id, name)
        if row is not None and int(row.get("size") or 0) == size:
            got = readback.remote_sha(seat, matter_id, str(row["id"]), scratch)
            if got == sha:
                return row
            if got is not None:
                raise readback.ReadbackMismatch(name, "the read-back bytes differ from the bytes built")
        if attempt + 1 < tries:
            time.sleep(pause)
    return None


def _proven_after_raise(
    seat: Any, matter_id: str, folder_id: str, m: dict[str, Any], exc: Exception, log: Log, scratch: Path, **kw: Any
) -> bool:
    """The one rule for a send that raised: that one name read back by hash.
    Any exception in the read-back counts as not proven."""
    log(f"{m['name']}: the send raised ({str(exc)[:160]}); reading that one name back by hash")
    try:
        row = prove_one(seat, matter_id, folder_id, m["name"], m["sha256"], int(m["bytes"]), scratch, **kw)
    except Exception as again:  # noqa: BLE001 - a read-back that fails proves nothing; the send holds, never resent
        log(f"{m['name']}: the read-back after the raised send failed too ({str(again)[:160]})")
        return False
    return row is not None


def send_missing(
    seat: Any,
    matter_id: str,
    folder_id: str,
    manifest: list[dict[str, Any]],
    present: dict[str, int],
    rec: SendRecord,
    log: Log,
    *,
    scratch: Path,
    tries: int,
    pause: float,
) -> tuple[Sent | None, str]:
    """Send whatever is not already up. ``(Sent, "ok")``; ``(None, "refused")``
    when a manifest row's local bytes no longer match its sha; ``(None, "held")``
    when a send raised and its file is not proven on the matter, or when a name
    the record says was sent or started now carries other bytes (wave 3: the
    file was rebuilt after it was sent; never a second copy).

    A name this record says was sent, or whose send started, is NOT sent again:
    the vendor's list lags, so absence from ``present`` is not evidence the file
    is missing, and the record is. Each row is written through before the next
    send starts."""
    sent_before, pending_before, done = rec.sent_before(), rec.pending(), Sent()
    recorded = rec.recorded()
    for m in manifest:
        data = Path(m["local_path"]).read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        if sha != m["sha256"]:
            log(f"{m['name']}: local bytes changed since the manifest (sha mismatch); refusing")
            return None, "refused"
        was = recorded.get(m["name"])
        if was and (was.get("sha256") != sha or was.get("bytes") != len(data)):
            # The record says this name went up (or started to) with other
            # bytes: the file was rebuilt after it was sent. Sending it again
            # puts a second file of that name on the firm's matter.
            log(
                f"{m['name']}: the built file changed after it was sent (the send record holds other bytes), so it "
                f"is not sent again. Holding for a person to read folder {folder_id}."
            )
            return None, "held"
        if present.get(m["name"]) == len(data):
            log(f"  present  {m['name']}")
            continue
        prior = sent_before.get(m["name"])
        if prior and prior.get("sha256") == sha and prior.get("bytes") == len(data):
            done.skipped += 1
            log(f"  sent earlier, not resending  {m['name']} (read-back will confirm)")
            continue
        if m["name"] in pending_before:
            # An earlier attempt started this send and never learned how it
            # ended. Re-POSTing would file a second copy, so the read-back
            # decides by name, and a person decides if it cannot.
            done.skipped += 1
            seen = f"yes, {present[m['name']]} bytes" if m["name"] in present else "not yet"
            log(f"  send started earlier, outcome unknown; not resending  {m['name']} (in the listing: {seen})")
            continue
        rec.put(m["name"], sha, len(data), pending=True)
        try:
            r = seat.add_file(matter_id, folder_id, m["name"], data)
        except Exception as exc:  # noqa: BLE001 - the outcome is unknown; the intent row stays and that name is read back by hash, never resent
            if not _proven_after_raise(seat, matter_id, folder_id, m, exc, log, scratch, tries=tries, pause=pause):
                log(
                    f"{m['name']}: the send failed ({str(exc)[:160]}); the file's record may already exist in folder "
                    f"{folder_id}, so it is not retried. Holding for a person to read the folder."
                )
                return None, "held"
            rec.put(m["name"], sha, len(data), pending=False)
            done.sent, done.raised = done.sent + 1, done.raised + 1
            log(f"  sent     {m['name']} ({len(data)} bytes; proven by hash after the send raised)")
            continue
        rec.put(m["name"], sha, len(data), pending=False)
        done.sent += 1
        log(f"  sent     {m['name']} ({len(data)} bytes; file id {(r or {}).get('fileId') or 'pending'})")
    return done, "ok"


def read_back(
    seat: Any,
    matter_id: str,
    folder_id: str,
    manifest: list[dict[str, Any]],
    *,
    tries: int,
    pause: float,
    log: Log | None = None,
    relist: bool = False,
) -> tuple[list[str], dict[str, int]]:
    """``(short names, the last listing)``: the folder listed until every name
    is at its byte count. ``log`` says each wait; ``relist`` lists once more
    after the last try to name what is still short."""
    expected = {m["name"]: m["bytes"] for m in manifest}
    short, present = list(expected), {}
    for attempt in range(tries):
        present = files_in(seat, matter_id, folder_id)
        short = [n for n, b in expected.items() if present.get(n) != b]
        if not short:
            break
        if attempt + 1 < tries:
            if log is not None:
                log(f"read-back: {len(short)} of {len(expected)} not yet at size; waiting")
            time.sleep(pause)
    else:
        if relist:
            short = [n for n, b in expected.items() if files_in(seat, matter_id, folder_id).get(n) != b]
    return short, present


def prove(
    seat: Any,
    matter_id: str,
    folder_id: str,
    manifest: list[dict[str, Any]],
    rec: SendRecord,
    log: Log,
    scratch: Path,
    *,
    pause: float,
    tries: int,
) -> int:
    """The bytes, not just the sizes: every file downloaded back by its id and
    its sha256 compared with the manifest's (``readback.confirm_files``). 0
    proven (rows gain ``file_id`` and ``proven``; no key is removed); 2 a file
    that cannot be fetched yet; 1 wrong bytes, with a ``readback_mismatch:``
    line as the LAST line (the daemon pages on that prefix, and
    ``demand/deliver.py`` reports the last line as the reason)."""
    expected = [{"name": m["name"], "sha256": m["sha256"], "bytes": m["bytes"]} for m in manifest]
    try:
        got = readback.confirm_files(
            seat, matter_id, folder_id, expected, scratch, tries=tries, pause=pause, log=log, sleep=time.sleep
        )
    except readback.ReadbackShort as exc:
        log(f"read-back by hash could not fetch every file yet: {exc}")
        return EXIT_SHORT
    except readback.ReadbackMismatch as exc:
        log(f"readback_mismatch: {exc.name}: {exc.why} (folder {folder_id}); nothing further is sent")
        return EXIT_REFUSED
    rec.mark_proven(got["files"])
    return 0


# ---- composer 1: a new dated folder -------------------------------------------------------
def _free_name(seat: Any, matter_id: str, base: str, log: Log) -> str | None:
    """``base``, or the first of "``base`` (2)", "(3)", ... no top-level folder
    carries. A same-name folder this run did not create is somebody else's: it
    is never written into and never touched (stop audit row 19, 2026-10-24)."""
    taken = {str(f.get("name") or "").strip() for f in seat.folder_tree(matter_id) if not f.get("parentId")}
    for n in range(1, FOLDER_SUFFIX_LIMIT + 1):
        name = base if n == 1 else f"{base} ({n})"
        if name not in taken:
            if n > 1:
                log(
                    f"a folder named '{base}' already exists on the matter and this run did not create it; "
                    f"delivering into a new folder '{name}' instead, and leaving the existing one untouched"
                )
            return name
    log(f"{FOLDER_SUFFIX_LIMIT} folders named '{base}' or '{base} (n)' already exist; refusing to add another")
    return None


def _new_folder(seat: Any, matter_id: str, base: str, rec: SendRecord, log: Log) -> str | None:
    """The folder this run delivers into, created and recorded BEFORE the first
    file goes up, so a crash before the last file resumes into it."""
    folder_name = _free_name(seat, matter_id, base, log)
    if folder_name is None:
        return None
    created = seat.create_folder(matter_id, folder_name)
    folder_id = str(created.get("id") or created.get("folderId") or "")
    if not folder_id:
        folder_id = str((top_folder(seat, matter_id, folder_name) or {}).get("id") or "")
    if not folder_id:
        log("create_folder returned no id and the folder is not visible on the matter")
        return None
    rec.doc = {"folder": folder_name, "folder_id": folder_id, "files": []}
    rec.save()
    log(f"created folder '{folder_name}' (id {folder_id})")
    return folder_id


def _note_unresolved(log: Log, unresolved: list[str], folder_id: str) -> None:
    if unresolved:
        log(
            f"{len(unresolved)} of them were sends whose outcome was never learned "
            f"({', '.join(unresolved)}); the runner will not resend them. A person reads folder {folder_id}: "
            f"if a file is missing or at 0 bytes, remove any partial copy there and clear its `pending` row "
            f"in delivery.json so the next attempt sends it once."
        )


def _predates_sent_flag(rec: SendRecord, folder_id: str, log: Log) -> bool:
    """ss#2901: a record written once, at the end, for every manifest row
    cannot say which went up; with a lagging list that filed a second 13-file
    package on 2026-09-24. A hold a person clears beats a copy they cannot."""
    if not rec.predates_sent_flag():
        return False
    log(
        f"delivery.json for folder {folder_id} predates the `sent` flag, so it cannot say which of "
        f"{len(rec.files())} file(s) are already on the matter; holding rather than risking a second copy. Read the "
        f"folder and, if the package is there, mark the run delivered by hand."
    )
    return True


def file_into_new_folder(
    seat_of: Callable[[], Any],
    matter_id: str,
    slug_dir: Path,
    unit: str,
    log: Log,
    *,
    pause: float = readback.READBACK_PAUSE_SECONDS,
    tries: int = readback.READBACK_TRIES,
) -> int:
    """``out/<unit>/upload_manifest.json`` onto the matter in a dated folder of
    its own; the record is ``runs/<unit>/delivery.json``. Returns 0 proven, or
    an ``EXIT_*``. ``seat_of`` is called once the manifest is read."""
    manifest = json.loads((slug_dir / "out" / unit / "upload_manifest.json").read_text(encoding="utf-8"))
    if not manifest:
        log("empty upload manifest")
        return EXIT_REFUSED
    runs = slug_dir / "runs" / unit
    rec = SendRecord.load(runs / "delivery.json")
    seat = seat_of()
    if rec.doc.get("folder_id"):
        folder_id = str(rec.doc["folder_id"])
        log(f"resuming into folder {folder_id} ('{manifest[0]['folder']}') created by this run")
    else:
        folder_id = _new_folder(seat, matter_id, str(manifest[0]["folder"]), rec, log) or ""
        if not folder_id:
            return EXIT_REFUSED
    if _predates_sent_flag(rec, folder_id, log):
        return EXIT_PRE_FLAG
    scratch = runs / "readback"
    present = files_in(seat, matter_id, folder_id)
    done, why = send_missing(
        seat, matter_id, folder_id, manifest, present, rec, log, scratch=scratch, tries=tries, pause=pause
    )
    if done is None:
        return EXIT_SEND_UNKNOWN if why == "held" else EXIT_REFUSED
    already = len(manifest) - done.sent - done.skipped
    log(f"{done.sent} file(s) sent, {already} already present, {done.skipped} sent by an earlier attempt")
    short, present = read_back(seat, matter_id, folder_id, manifest, tries=tries, pause=pause, log=log, relist=True)
    pending = rec.write_readback(manifest, present)
    if short:
        log(f"read-back short after {tries} tries: {', '.join(short)}")
        _note_unresolved(log, [n for n in short if n in pending], folder_id)
        return EXIT_SHORT
    log(f"read-back complete: {len(manifest)} file(s) at the expected byte counts in folder {folder_id}")
    return prove(seat, matter_id, folder_id, manifest, rec, log, scratch, pause=pause, tries=tries)


# ---- composer 2: an existing folder, by id --------------------------------------------------
@dataclass
class Filed:
    """How filing into an existing folder ended: ``ok``, ``refused``, ``held``
    (a raised send not proven), ``short`` (``short`` names it) or ``unproven``
    (``rc`` is ``prove``'s exit). ``raised`` counts sends that raised and were
    then proven by hash."""

    status: str
    short: tuple[str, ...] = ()
    rc: int = 0
    raised: int = 0


def file_into_folder(
    seat: Any,
    matter_id: str,
    folder_id: str,
    manifest: list[dict[str, Any]],
    rec: SendRecord,
    log: Log,
    scratch: Path,
    *,
    tries: int,
    pause: float,
) -> Filed:
    """The manifest into a folder this run did not create (the delivered
    chronology's own): sent once, read back at size, proven by hash."""
    present = files_in(seat, matter_id, folder_id)
    done, why = send_missing(
        seat, matter_id, folder_id, manifest, present, rec, log, scratch=scratch, tries=tries, pause=pause
    )
    if done is None:
        return Filed(why)
    short, _ = read_back(seat, matter_id, folder_id, manifest, tries=tries, pause=pause)
    rec.write_readback(manifest, files_in(seat, matter_id, folder_id))
    if short:
        return Filed("short", tuple(short), raised=done.raised)
    rc = prove(seat, matter_id, folder_id, manifest, rec, log, scratch, pause=pause, tries=tries)
    return Filed("ok" if rc == 0 else "unproven", rc=rc, raised=done.raised)


# ---- composer 3: a standing folder, one file --------------------------------------------------
class FilingHeld(RuntimeError):
    """A person at the firm decides (a name in the folder this run did not send)."""


class FilingFailed(RuntimeError):
    """The filing could not finish, or the bytes on the matter are not ours."""


def _standing_folder(seat: Any, matter_id: str, folder_name: str, rec: SendRecord, log: Log) -> str:
    folder_id = rec.doc.get("folder_id") or (top_folder(seat, matter_id, folder_name) or {}).get("id")
    if folder_id:
        return str(folder_id)
    created = seat.create_folder(matter_id, folder_name)
    folder_id = str(created.get("id") or created.get("folderId") or "")
    folder_id = folder_id or (top_folder(seat, matter_id, folder_name) or {}).get("id")
    if not folder_id:
        raise FilingFailed("stage_unfinished: file: the filing folder could not be created")
    log(f"created folder '{folder_name}'")
    return str(folder_id)


def _send_once(seat: Any, matter_id: str, folder_id: str, name: str, blob: bytes, rec: SendRecord, log: Log) -> None:
    """The one send, behind its intent row; a name with either row never again."""
    if rec.doc.get("pending") or rec.doc.get("sent"):
        return
    if file_row(seat, matter_id, folder_id, name) is not None:
        raise FilingHeld(f"filing_refused: a file named '{name}' is already in the folder and this run did not send it")
    rec.doc["pending"] = True
    rec.save()
    try:
        seat.add_file(matter_id, folder_id, name, blob)
    except Exception as exc:  # noqa: BLE001 - the outcome is unknown; the intent row stays, never resent, the read-back decides
        log(f"the send raised ({type(exc).__name__}); the read-back decides")
    else:
        rec.doc.update(pending=False, sent=True)
        rec.save()


def file_into_standing_folder(
    seat: Any,
    matter_id: str,
    folder_name: str,
    local: Path,
    data: Path,
    log: Log,
    *,
    tries: int = readback.READBACK_TRIES,
    pause: float = readback.READBACK_PAUSE_SECONDS,
    record: str = "delivery.json",
) -> dict[str, Any]:
    """``local`` into the matter's standing folder ``folder_name`` (created
    when absent), read back and equal by hash: ``{folder_id, file_id, name,
    size, sha256}``. ``record`` names this file's own send record, so a job
    that files several keeps one each and a resume never resends either.
    Raises ``FilingHeld`` or ``FilingFailed``."""
    blob = local.read_bytes()
    sha, name = hashlib.sha256(blob).hexdigest(), local.name
    rec = SendRecord.load(data / record)
    if rec.doc.get("sha256") and rec.doc["sha256"] != sha:
        raise FilingFailed("stage_unfinished: file: the built workbook changed after a send was recorded; not resent")
    folder_id = _standing_folder(seat, matter_id, folder_name, rec, log)
    rec.doc.update(folder_id=folder_id, name=name, sha256=sha, bytes=len(blob))
    rec.save()
    _send_once(seat, matter_id, folder_id, name, blob, rec, log)
    try:
        row = prove_one(seat, matter_id, folder_id, name, sha, len(blob), data, tries=tries, pause=pause)
    except readback.ReadbackMismatch:
        raise FilingFailed(
            "readback_mismatch: file: the read-back bytes differ from the workbook built; "
            "the file on the matter is not the one built here"
        ) from None
    if row is None:
        raise FilingFailed(
            "stage_unfinished: file: the read-back is short after its retries; the file may still be materializing"
        )
    rec.doc.update(confirmed=True, file_id=str(row["id"]))
    rec.save()
    return {"folder_id": folder_id, "file_id": str(row["id"]), "name": name, "size": len(blob), "sha256": sha}


__all__ = [
    "EXIT_PRE_FLAG",
    "EXIT_REFUSED",
    "EXIT_SEND_UNKNOWN",
    "EXIT_SHORT",
    "FOLDER_SUFFIX_LIMIT",
    "Filed",
    "FilingFailed",
    "FilingHeld",
    "SendRecord",
    "Sent",
    "file_into_folder",
    "file_into_new_folder",
    "file_into_standing_folder",
    "file_row",
    "files_in",
    "folded_ext",
    "full_name",
    "prove",
    "prove_one",
    "read_back",
    "send_missing",
    "top_folder",
]
