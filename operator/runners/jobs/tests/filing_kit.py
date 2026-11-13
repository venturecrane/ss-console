"""The recording seat every filing golden runs through (wave 3, PR A).

WHY. Filing finished work into a firm's Smokeball is written four times
(``stages/upload.py`` and the two update stages over it, demand and drafting
through ``demand/deliver.file_to_matter``; ``litigation/deliver.file_workbook``
for litigation and casefile; ``drafting/revise/versions.file_versions``;
``library_setup/file.file_library``). Wave 3 folds them onto one module, and a
duplicate file on a firm's matter is the failure that must never happen. So
before any of that code moves, this records what TODAY's code does, cell by
cell (``test_filing_goldens.py``), and the integrity job holds the move to it.

WHAT IS RECORDED, per run, in ONE ordered stream:

* every connector call: the method, its ids and names, the sha256 and length
  of any bytes (never the bytes), what it returned (rows compacted), or the
  exception it raised;
* the send record on disk AT that call (only when it changed since the last
  one): this is what proves an intent row is written before its send;
* every line the code logged, in order with the calls;
* how the run ended (its return value, the exception class and message, or the
  kill), then the vendor's final state and the count of each write.

THE VENDOR is ``FakeSeat``'s model (``jobs_testkit``): index lag, a send that
lands its POST and then raises (full size, or the 0-byte placeholder a failed
PUT leaves), the vendor's split of name and extension. On top of it: a send
that raises before anything lands, a listing that raises once a send has
raised, other bytes stored at the right size, a vendor that echoes the
extension inside the name too, a kill after any call (``Killed``, a
``BaseException``: nothing the code under test catches as ``Exception``), and
the two calls revise and the library make (``file_meta``,
``add_file_version``; the library's connector-client shape over the same model,
``LibraryClient``).

Every name, id and byte here is invented.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

from goldens_kit import plain
from jobs_testkit import FakeSeat

LANDED = "the response was lost after the POST"
NOT_LANDED = "connection dropped mid-upload"
LISTING_FAILED = "the listing failed (the seat stopped answering)"
#: An ISO timestamp (``versions.json`` stamps its intent row).
_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


class Killed(BaseException):
    """The process died right after a connector call returned: nothing after it ran."""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normal(doc: Any, *roots: Path) -> Any:
    """``doc`` as plain JSON with every temp root written ``<TMP>`` and every
    ISO timestamp ``<AT>``: two runs of the same code give the same bytes."""
    text = json.dumps(plain(doc), sort_keys=True, default=str)
    for r in sorted({str(p) for root in roots for p in (root, root.resolve())}, key=len, reverse=True):
        text = text.replace(r, "<TMP>")
    return json.loads(_AT.sub("<AT>", text))


def _row(r: dict[str, Any]) -> str:
    gone = " deleted" if r.get("deleted") else ""
    return f"{r.get('id')} {r.get('name')!r} {r.get('ext')!r} {r.get('size')} in {r.get('folderId')}{gone}"


class RecordingSeat(FakeSeat):
    """``FakeSeat`` with every call recorded and the faults the goldens need.

    ``watch``: the send-record files (paths) snapshotted at each call.
    ``write_fault``: how the FIRST write of the run fails (``landed``,
    ``placeholder``, ``not_landed``), or None. ``kill_after``: the call count
    after whose return ``Killed`` is raised; ``kill_before``: the method on
    whose entry it is raised, before the call reaches the vendor. ``corrupt``: names stored with
    other bytes of the same length. ``list_fails_after_raise``: every listing
    raises once a write has raised. ``whole_names``: the vendor keeps the
    extension in ``name`` as well as in ``ext``. ``version_lag``: metadata
    reads before a new version shows."""

    def __init__(self, docs: list[dict] | None = None, folders: list[dict] | None = None) -> None:
        super().__init__(list(docs or []), list(folders or []), {})
        self.events: list[dict[str, Any]] = []
        self.watch: list[Path] = []
        self.root: Path | None = None
        self.kill_after: int | None = None
        self.kill_before: str | None = None
        self.write_fault: str | None = None
        self.corrupt: set[str] = set()
        self.list_fails_after_raise = False
        self.whole_names = False
        self.version_lag = 0
        self.version: dict[str, int] = {}
        self.calls = 0
        self.writes: dict[str, int] = {}
        self._raised = False
        self._last: Any = None
        self._meta_reads = 0
        self._moved_at: dict[str, int] = {}

    # ---- the stream -------------------------------------------------------------
    def log(self, line: str) -> None:
        self.events.append({"log": str(line)})

    def _snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for p in self.watch:
            key = str(p.relative_to(self.root)) if self.root else p.name
            try:
                out[key] = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
            except ValueError:
                out[key] = "<not json>"
        return out

    def _call(self, method: str, args: dict[str, Any], fn: Callable[[], Any], shown: Callable[[Any], Any]) -> Any:
        if self.kill_before == method:
            self.kill_before = None
            raise Killed(f"before {method}")
        self.calls += 1
        ev: dict[str, Any] = {"call": method, **args}
        snap = self._snapshot()
        if snap != self._last:
            ev["records"], self._last = snap, snap
        self.events.append(ev)
        try:
            out = fn()
        except Exception as exc:
            ev["raised"] = {"type": type(exc).__name__, "message": str(exc)}
            raise
        ev["returned"] = shown(out)
        if self.kill_after is not None and self.calls >= self.kill_after:
            ev["killed"] = True
            self.kill_after = None
            raise Killed(method)
        return out

    def _wrote(self, method: str, *, faults: bool = True) -> str | None:
        """Count a write; the run's first FILE write takes the run's fault."""
        self.writes[method] = self.writes.get(method, 0) + 1
        if not faults:
            return None
        fault, self.write_fault = self.write_fault, None
        if fault:
            self._raised = True
        return fault

    # ---- reads ------------------------------------------------------------------
    def list_files(self, matter_id: str) -> list[dict]:
        def go() -> list[dict]:
            if self.list_fails_after_raise and self._raised:
                raise RuntimeError(LISTING_FAILED)
            return FakeSeat.list_files(self, matter_id)

        return self._call("list_files", {"matter": matter_id}, go, lambda rows: [_row(r) for r in rows])

    def folder_tree(self, matter_id: str) -> list[dict]:
        return self._call(
            "folder_tree",
            {"matter": matter_id},
            lambda: FakeSeat.folder_tree(self, matter_id),
            lambda rows: [f"{f.get('id')} {f.get('name')!r} under {f.get('parentId')}" for f in rows],
        )

    def mint(self, matter_id: str, file_ids: list[str]) -> list[dict]:
        return self._call(
            "mint",
            {"matter": matter_id, "ids": list(file_ids)},
            lambda: FakeSeat.mint(self, matter_id, file_ids),
            plain,
        )

    def fetch(self, url: str, dest: Path, expected_size: int | None) -> int:
        return self._call(
            "fetch",
            {"url": url, "expected_size": expected_size},
            lambda: FakeSeat.fetch(self, url, dest, expected_size),
            plain,
        )

    def file_meta(self, matter_id: str, file_id: str) -> dict[str, Any]:
        return self._call("file_meta", {"matter": matter_id, "id": file_id}, lambda: self._meta(file_id), plain)

    # ---- writes -----------------------------------------------------------------
    def create_folder(self, matter_id: str, name: str) -> dict:
        def go() -> dict:
            self._wrote("create_folder", faults=False)
            return FakeSeat.create_folder(self, matter_id, name)

        return self._call("create_folder", {"matter": matter_id, "name": name}, go, plain)

    def add_file(self, matter_id: str, folder_id: str, name: str, data: bytes) -> dict:
        args = {"matter": matter_id, "folder": folder_id, "name": name, "bytes": len(data), "sha256": sha(data)}
        return self._call("add_file", args, lambda: self._add(matter_id, folder_id, name, data), plain)

    def _add(self, matter_id: str, folder_id: str, name: str, data: bytes) -> dict:
        fault = self._wrote("add_file")
        if fault == "not_landed":
            raise RuntimeError(NOT_LANDED)
        if fault in ("landed", "placeholder"):
            self.post_then_fail[name] = len(data) if fault == "landed" else 0
        try:
            return FakeSeat.add_file(self, matter_id, folder_id, name, data)
        finally:
            self._after_add(name, data)

    def _after_add(self, name: str, data: bytes) -> None:
        """The vendor's copy of the row just queued: whole name, other bytes."""
        if not self._pending:
            return
        row = self._pending[-1][1]
        if self.whole_names:
            row["name"] = name
        if name in self.corrupt and row["id"] in self.blobs:
            self.blobs[row["id"]] = bytes(b ^ 0xFF for b in data)

    def delete_file(self, matter_id: str, file_id: str) -> dict:
        def go() -> dict:
            self._wrote("delete_file", faults=False)
            return FakeSeat.delete_file(self, matter_id, file_id)

        return self._call("delete_file", {"matter": matter_id, "id": file_id}, go, plain)

    def add_file_version(
        self,
        matter_id: str,
        file_id: str,
        data: bytes,
        *,
        expected_version: str,
        expected_folder: str | None = None,
        expected_name: str | None = None,
    ) -> dict:
        args = {
            "matter": matter_id,
            "id": file_id,
            "bytes": len(data),
            "sha256": sha(data),
            "expected_version": expected_version,
            "expected_folder": expected_folder,
            "expected_name": expected_name,
        }
        return self._call(
            "add_file_version",
            args,
            lambda: self._version(file_id, data, expected_version, expected_folder, expected_name),
            plain,
        )

    # ---- the version model (revise, the library) ---------------------------------
    def doc(self, file_id: str) -> dict[str, Any] | None:
        return next((d for d in self.docs if str(d.get("id")) == str(file_id)), None)

    def _vid(self, file_id: str) -> str:
        n = self.version.get(file_id, 1)
        if file_id in self._moved_at and self._meta_reads < self._moved_at[file_id]:
            n -= 1  # the new version is not visible yet (the vendor's lag)
        return f"{file_id}-v{n}"

    def _meta(self, file_id: str) -> dict[str, Any]:
        self._meta_reads += 1
        d = self.doc(file_id)
        if d is None:
            raise RuntimeError(f"404: no file {file_id}")
        return {
            "id": file_id,
            "versionId": self._vid(file_id),
            "isDeleted": bool(d.get("deleted")),
            "folder": {"id": d.get("folderId")},
            "name": d.get("name"),
            "fileExtension": d.get("ext"),
            "sizeBytes": d.get("size"),
        }

    def _version(self, file_id: str, data: bytes, expected: str, folder: str | None, name: str | None) -> dict:
        fault = self._wrote("add_file_version")
        meta = self._meta(file_id)
        why = []
        if meta["versionId"] != expected:
            why.append("its version changed")
        if folder is not None and str(meta["folder"]["id"]) != folder:
            why.append("it is in another folder")
        if name is not None and f"{meta['name'] or ''}{meta['fileExtension'] or ''}" != name:
            why.append("it has another name")
        if why:
            raise RuntimeError(f"add_file_version: file {file_id!r}: {'; '.join(why)}; nothing sent")
        if fault == "not_landed":
            raise RuntimeError(NOT_LANDED)
        self.land_version(file_id, data)
        if fault:
            raise RuntimeError(LANDED)
        return {"fileId": file_id, "versionBefore": expected, "uploaded": True}

    def land_version(self, file_id: str, data: bytes) -> None:
        """A new version of ``file_id`` with ``data`` (other bytes if corrupt)."""
        d = self.doc(file_id) or {}
        self.version[file_id] = self.version.get(file_id, 1) + 1
        self._moved_at[file_id] = self._meta_reads + self.version_lag
        full = f"{d.get('name') or ''}{d.get('ext') or ''}"
        self.blobs[file_id] = bytes(b ^ 0xFF for b in data) if full in self.corrupt else data
        d["size"] = len(data)

    # ---- the end of a run ----------------------------------------------------------
    def vendor_state(self) -> list[str]:
        out = []
        for d in self.docs:
            blob = self.blobs.get(str(d.get("id")))
            tail = f" sha {sha(blob)[:16]}" if blob is not None else " (no bytes)"
            out.append(_row(d) + f" v{self.version.get(str(d.get('id')), 1)}" + tail)
        for _due, d in self._pending:  # on the matter, not in its index yet (the vendor's lag)
            blob = self.blobs.get(str(d.get("id")))
            out.append(_row(d) + " not indexed yet" + (f" sha {sha(blob)[:16]}" if blob is not None else " (no bytes)"))
        return out


class LibraryClient:
    """The connector-client calls the library filing makes (``SmokeballLibrary``,
    ``lookup_matter``, ``find_folder_id``, ``add_file``, ``add_file_version``,
    ``download_file``), answered from a ``RecordingSeat``'s model and recorded in
    its stream. Shapes as ``library_setup_testkit.LibraryTenant`` answers them."""

    def __init__(self, seat: RecordingSeat, matter: str, number: str, folder: str, folder_name: str) -> None:
        self.seat, self.matter, self.number, self.folder, self.folder_name = seat, matter, number, folder, folder_name

    def _entry(self, d: dict[str, Any]) -> dict[str, Any]:
        fid = str(d["id"])
        return {
            "id": fid,
            "name": d.get("name"),
            "fileExtension": d.get("ext"),
            "versionId": self.seat._vid(fid),
            "isDeleted": bool(d.get("deleted")),
            "sizeBytes": d.get("size"),
            "folder": {"id": d.get("folderId")},
        }

    def _answer(self, path: str) -> Any:
        parts = path.strip("/").split("/")
        if path == "/matters":
            return {"value": [{"id": self.matter, "number": self.number}]}
        if parts[-1] == "folders":
            return {"value": [{"folders": [{"id": self.folder, "name": self.folder_name}]}]}
        if parts[-1] == "files":
            self.seat._materialize()
            return {"value": [self._entry(d) for d in self.seat.docs]}
        if len(parts) == 5 and parts[2:4] == ["documents", "files"]:
            fid = parts[4]
            self.seat._meta_reads += 1
            d = self.seat.doc(fid)
            new_and_lagging = self.seat.version.get(fid) == 1 and self.seat._meta_reads < self.seat._moved_at.get(
                fid, 0
            )
            if d is None or new_and_lagging:
                raise RuntimeError(f"404: no file {fid} (yet)")
            return self._entry(d)
        raise AssertionError(f"the library filing never asks {path}")

    def get(self, path: str, **params: Any) -> Any:
        args = {"path": path, **{k: str(v) for k, v in params.items()}}
        return self.seat._call("client.get", args, lambda: self._answer(path), plain)

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        return self.seat._call("client.request", {"method": method, "path": path}, lambda: self._answer(path), plain)

    def download_file(self, matter_id: str, file_id: str) -> tuple[dict[str, Any], bytes]:
        return self.seat._call(
            "client.download_file",
            {"matter": matter_id, "id": file_id},
            lambda: ({}, self.seat.blobs[str(file_id)]),
            lambda out: {"bytes": len(out[1]), "sha256": sha(out[1])},
        )

    def add_file(self, matter_id: str, file_name: str, data: bytes, *, folder_id: str | None = None) -> dict:
        args = {"matter": matter_id, "folder": folder_id, "name": file_name, "bytes": len(data), "sha256": sha(data)}
        return self.seat._call("client.add_file", args, lambda: self._add(file_name, data, folder_id), plain)

    def _add(self, file_name: str, data: bytes, folder_id: str | None) -> dict:
        fault = self.seat._wrote("add_file")
        if fault == "not_landed":
            raise RuntimeError(NOT_LANDED)
        fid = f"lib-new-{self.seat.writes['add_file']}"
        stem, dot, ext = file_name.rpartition(".")
        row = {"id": fid, "name": file_name if self.seat.whole_names else (stem if dot else file_name)}
        row.update(ext=f".{ext}" if dot else "", folderId=folder_id, deleted=False)
        row["size"] = 0 if fault == "placeholder" else len(data)
        self.seat.docs.append(row)
        if fault != "placeholder":
            self.seat.blobs[fid] = bytes(b ^ 0xFF for b in data) if file_name in self.seat.corrupt else data
            self.seat.version[fid] = 1
            self.seat._moved_at[fid] = self.seat._meta_reads + self.seat.version_lag
        if fault:
            raise RuntimeError(LANDED)
        return {"fileId": fid}

    def add_file_version(self, matter_id: str, file_id: str, data: bytes, *, expected_version: str, **kw: Any) -> dict:
        return self.seat.add_file_version(matter_id, file_id, data, expected_version=expected_version, **kw)


__all__ = ["LANDED", "LISTING_FAILED", "NOT_LANDED", "Killed", "LibraryClient", "RecordingSeat", "normal", "sha"]
