"""Seat access: the one seam through which the runner touches a matter.

Every read of the firm's system of record goes through a `Seat`: list the
files on a matter, walk its folder tree, mint a presigned download URL, fetch
the bytes. Two backends implement it, and the stages cannot tell them apart:

* `ClientSeat` wraps the Smokeball connector's plain client
  (`smokeball_connector.client`, never the MCP tool layer). This is the
  backend on the Machine (ss#2614), where the firm-delegated refresh token
  lives on the volume and `build_client_from_env` finds it.
* `SshSeat` runs a small listing/mint script ON the seat through
  `operator/bin/seat-probe.sh`, exactly as the frozen pipeline's
  `run_seat.sh` did. This is the laptop backend for the deliveries that
  continue during the port: the token never leaves the volume, so a laptop
  cannot build a client, and pretending otherwise would fail at the first
  mint. The bytes still move S3 -> local over https, never through ssh.

`FakeSeat` lives in the tests. Which backend runs is `MEDCHRON_SEAT`
(`client`, the default, or `ssh`); nothing here guesses.

Fetching is shared and deliberately narrow: https only, no redirects, streamed
to disk, size checked against what the mint advertised (the predecessor's
docstring CLAIMED byte verification and never compared, and a truncated pull
passed silently).
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Protocol

import httpx

SEAT_ENV = "MEDCHRON_SEAT"
SEAT_PROBE_ENV = "MEDCHRON_SEAT_PROBE"
PAGE = 500
MINT_PAUSE_SECONDS = 0.25
LIST_PAUSE_SECONDS = 0.3
FETCH_TIMEOUT_SECONDS = 120


class SeatError(RuntimeError):
    pass


class Seat(Protocol):
    def list_files(self, matter_id: str) -> list[dict[str, Any]]: ...
    def folder_tree(self, matter_id: str) -> list[dict[str, Any]]: ...
    def mint(self, matter_id: str, file_ids: list[str]) -> list[dict[str, Any]]: ...
    def fetch(self, url: str, dest: Path, expected_size: int | None) -> int: ...
    # ss#2614: the two writes the upload stage makes. Both are INTERNAL_WRITE
    # at the overlay; the runner never deletes.
    def create_folder(self, matter_id: str, name: str) -> dict[str, Any]: ...
    def add_file(self, matter_id: str, folder_id: str, name: str, data: bytes) -> dict[str, Any]: ...


# ---- shapes shared by both backends -----------------------------------------
def _items(r: Any) -> list[Any]:
    if isinstance(r, dict):
        for k in ("value", "items", "data", "results"):
            if isinstance(r.get(k), list):
                return r[k]
        return []
    return r if isinstance(r, list) else []


def normalize_file(d: dict[str, Any]) -> dict[str, Any] | None:
    """One document row in the manifest shape every downstream stage reads
    (`id, name, size, ext, folderId, created, modified, deleted`)."""
    fid = d.get("id") or d.get("fileId") or d.get("documentId")
    if not fid:
        return None
    return {
        "id": fid,
        "name": d.get("name") or d.get("fileName") or "",
        "size": d.get("sizeBytes") or d.get("size"),
        "ext": d.get("fileExtension"),
        "folderId": (d.get("folder") or {}).get("id"),
        "created": d.get("dateCreated"),
        "modified": d.get("dateModified"),
        "deleted": d.get("isDeleted"),
    }


def walk_folder_tree(kids, pause: float = MINT_PAUSE_SECONDS) -> list[dict[str, Any]]:
    """Per-folder GET, recursively: the top-level tree nests only one level
    (proven on a live matter 2026-08-24), so a single read misses every
    grandchild. `kids(folder_id_or_None)` returns that folder's children."""
    out: list[dict[str, Any]] = []

    def walk(fid: str | None, prefix: str) -> None:
        for f in kids(fid):
            p = prefix + "/" + (f.get("name") or "?")
            out.append({"id": f.get("id"), "name": f.get("name"), "parentId": fid, "path": p})
            if pause:
                time.sleep(pause)
            walk(f.get("id"), p)

    walk(None, "")
    return out


def fetch_https(url: str, dest: Path, expected_size: int | None, timeout: float = FETCH_TIMEOUT_SECONDS) -> int:
    """Stream a presigned URL to `dest`. https only and no redirects, because
    the URL comes back from a mint and urllib would happily honour file:// or
    a redirect to one. Size mismatch removes the file and raises."""
    if not url.startswith("https://"):
        raise SeatError("mint returned a non-https url")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, timeout=timeout, follow_redirects=False) as resp:
        resp.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in resp.iter_bytes(1 << 20):
                fh.write(chunk)
    got = dest.stat().st_size
    if expected_size and got != expected_size:
        dest.unlink(missing_ok=True)
        raise SeatError(f"size mismatch: got {got}, expected {expected_size}; re-pull")
    return got


# ---- backend 1: the connector client (on the Machine) ------------------------
class ClientSeat:
    def __init__(self, client: Any | None = None) -> None:
        if client is None:
            from smokeball_connector.client import build_client_from_env

            client = build_client_from_env()
        self.client = client

    def list_files(self, matter_id: str) -> list[dict[str, Any]]:
        docs: list[dict[str, Any]] = []
        off = 0
        while True:
            batch = _items(self.client.get(f"/matters/{matter_id}/documents/files", Limit=PAGE, Offset=off))
            docs.extend(row for row in (normalize_file(d) for d in batch) if row)
            if len(batch) < PAGE:
                return docs
            off += PAGE
            time.sleep(LIST_PAUSE_SECONDS)

    def folder_tree(self, matter_id: str) -> list[dict[str, Any]]:
        def kids(fid: str | None) -> list[dict[str, Any]]:
            path = f"/matters/{matter_id}/documents/folders" + (f"/{fid}" if fid else "")
            r = self.client.get(path)
            v = (r.get("value") or [{}])[0] if isinstance(r, dict) else {}
            return v.get("folders") or []

        return walk_folder_tree(kids)

    def mint(self, matter_id: str, file_ids: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for fid in file_ids:
            try:
                info = self.client.request("GET", f"/matters/{matter_id}/documents/files/{fid}/download")
                out.append(
                    {
                        "id": fid,
                        "url": info.get("downloadUrl"),
                        "expiry": info.get("expiry"),
                        "name": info.get("name"),
                        "size": info.get("sizeBytes"),
                        "ext": info.get("fileExtension"),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - one bad id must not kill the batch
                out.append({"id": fid, "error": str(exc)[:200]})
            time.sleep(MINT_PAUSE_SECONDS)
        return out

    def fetch(self, url: str, dest: Path, expected_size: int | None) -> int:
        return fetch_https(url, dest, expected_size)

    def create_folder(self, matter_id: str, name: str) -> dict[str, Any]:
        r = self.client.create_folder(matter_id, name)
        return r if isinstance(r, dict) else {}

    def add_file(self, matter_id: str, folder_id: str, name: str, data: bytes) -> dict[str, Any]:
        r = self.client.add_file(matter_id, name, data, folder_id=folder_id)
        return r if isinstance(r, dict) else {}


# ---- backend 2: the seat over ssh (the laptop, during the port) ---------------
# The script that runs on the seat. It is the frozen pipeline's seat_list_mint
# and seat_folders, joined, and it prints one JSON document after a sentinel
# so the transport can find it inside whatever the ssh session also prints.
_SEAT_SCRIPT = r"""
import json, sys, time
sys.path.insert(0, "/opt/connectors/smokeball")
from smokeball_connector.client import build_client_from_env
def items(r):
    if isinstance(r, dict):
        for k in ("value", "items", "data", "results"):
            if isinstance(r.get(k), list):
                return r[k]
        return []
    return r if isinstance(r, list) else []
mode, mid = sys.argv[1], sys.argv[2]
c = build_client_from_env()
if mode == "list":
    docs, off = [], 0
    while True:
        batch = items(c.get(f"/matters/{mid}/documents/files", Limit=500, Offset=off))
        docs.extend(batch)
        if len(batch) < 500:
            break
        off += 500
        time.sleep(0.3)
    payload = {"documents": docs}
elif mode == "folders":
    out = []
    def kids(fid):
        p = f"/matters/{mid}/documents/folders" + (f"/{fid}" if fid else "")
        r = c.get(p)
        return ((r.get("value") or [{}])[0]).get("folders") or []
    def walk(fid, prefix):
        for f in kids(fid):
            p = prefix + "/" + (f.get("name") or "?")
            out.append({"id": f.get("id"), "name": f.get("name"), "parentId": fid, "path": p})
            time.sleep(0.25)
            walk(f.get("id"), p)
    walk(None, "")
    payload = {"folders": out}
else:
    out = []
    for fid in [x for x in sys.argv[3].split(",") if x]:
        try:
            info = c.request("GET", f"/matters/{mid}/documents/files/{fid}/download")
            out.append({"id": fid, "url": info.get("downloadUrl"), "expiry": info.get("expiry"),
                        "name": info.get("name"), "size": info.get("sizeBytes"), "ext": info.get("fileExtension")})
        except Exception as exc:
            out.append({"id": fid, "error": str(exc)[:200]})
        time.sleep(0.25)
    payload = {"files": out}
print("@@SEAT@@" + json.dumps(payload))
"""
SENTINEL = "@@SEAT@@"
SEAT_PYTHON = "/opt/connectors/smokeball/.venv/bin/python3"


class SshSeat:
    def __init__(self, customer_slug: str, probe: str | None = None, timeout: int = 300) -> None:
        self.customer_slug = customer_slug
        self.probe = probe or os.environ.get(SEAT_PROBE_ENV) or ""
        if not self.probe or not Path(self.probe).is_file():
            raise SeatError(f"{SEAT_PROBE_ENV} must name operator/bin/seat-probe.sh for the ssh seat backend")
        self.timeout = timeout

    def _call(self, *argv: str) -> dict[str, Any]:
        b64 = base64.b64encode(_SEAT_SCRIPT.encode()).decode()
        argv_json = ",".join(json.dumps(a) for a in argv)
        code = f'import base64,sys;sys.argv=["seat",{argv_json}];exec(base64.b64decode("{b64}").decode())'
        proc = subprocess.run(  # noqa: S603 - argv is the seat-probe script, the slug, and a base64-wrapped module snippet; no shell
            [self.probe, self.customer_slug, SEAT_PYTHON, "-c", code],
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        if SENTINEL not in proc.stdout:
            raise SeatError(f"seat call {argv[0]} returned no payload: {proc.stderr.strip()[-300:]}")
        return json.loads(proc.stdout.split(SENTINEL, 1)[1])

    def list_files(self, matter_id: str) -> list[dict[str, Any]]:
        raw = self._call("list", matter_id)["documents"]
        return [row for row in (normalize_file(d) for d in raw) if row]

    def folder_tree(self, matter_id: str) -> list[dict[str, Any]]:
        return self._call("folders", matter_id)["folders"]

    def mint(self, matter_id: str, file_ids: list[str]) -> list[dict[str, Any]]:
        return self._call("mint", matter_id, ",".join(file_ids))["files"]

    def fetch(self, url: str, dest: Path, expected_size: int | None) -> int:
        return fetch_https(url, dest, expected_size)

    def create_folder(self, matter_id: str, name: str) -> dict[str, Any]:
        raise SeatError("the ssh seat is read-only; the upload stage runs on the Machine (ss#2614)")

    def add_file(self, matter_id: str, folder_id: str, name: str, data: bytes) -> dict[str, Any]:
        raise SeatError("the ssh seat is read-only; the upload stage runs on the Machine (ss#2614)")


def open_seat(customer_slug: str) -> Seat:
    """The backend `MEDCHRON_SEAT` names: `client` (default) or `ssh`."""
    kind = (os.environ.get(SEAT_ENV) or "client").strip().lower()
    if kind == "client":
        return ClientSeat()
    if kind == "ssh":
        return SshSeat(customer_slug)
    raise SeatError(f"{SEAT_ENV}={kind!r} is not a seat backend (client | ssh)")
