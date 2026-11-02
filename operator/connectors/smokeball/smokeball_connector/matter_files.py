"""Find a matter's files by name or folder, and stage named files for a staff reply.

WHY THIS EXISTS (2026-11-01, the ashton-price miss). An attorney emailed the
Operator for five named records on one matter, attached to a reply. The Operator
answered that it could not search the matter's documents by name (the listing
tool returns 500 files a page with no filter, on a matter with thousands) and
that its email could not carry a file. Both were true. This module closes the
connector half of both:

* ``find_files_on_matter`` (READ): every file on the matter whose name, folder
  or extension matches, paged SERVER-SIDE through the whole listing. The vendor
  does offer a ``Search`` parameter on the file listing, but it is answered from
  "the document search index", a separate index whose matching rules, lag behind
  an upload, and coverage we have not observed on a live tenant. The plain
  listing is the record itself, so the filter is applied here, over every page,
  and the result says whether every page was read (``searchComplete``).
* ``stage_matter_files`` (READ): the model names file ids, and for a file it
  wants only part of, a page range. This code fetches the bytes from Smokeball,
  checks each file is on the matter the call names, cuts the pages out itself,
  and writes each result to the seat's outbound file spool. The model never
  handles a byte and never names a path. The reply plugin attaches the staged
  files to the reply to the colleague who asked, after its own fences
  (overlay ``shared/matter_file_reply.py``, ADR 0102).

Nothing here sends anything. Staging only writes this seat's own disk, which is
why the tool is READ-class; the external effect is the reply, and the reply is
fenced where it is sent.

FAIL CLOSED. Unauthored ``matter_file_delivery`` stages nothing. A file whose
own record does not name this matter, that is not a PDF, that cannot be fetched,
or that would take the reply past the size an email carries is refused BY NAME
in the result, never silently dropped; the files that passed are still staged,
so the person gets what can go and is told what cannot.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .library import CUSTOMER_YAML_ENV, DEFAULT_CUSTOMER_YAML, _listing, _walk_folder_paths

CONFIG_BLOCK = "matter_file_delivery"

#: The spool the reply plugin reads. Mirrors overlay ``shared/matter_file_reply``
#: (same names, same values): the layout is the contract between two processes.
SPOOL_DIR_ENV = "SMD_OUTBOUND_FILE_SPOOL_DIR"
DEFAULT_SPOOL_DIR = "/opt/data/outbound-file-spool"
SPOOL_TTL_SECONDS = 2 * 60 * 60
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")

#: Files one request may stage, and the bytes per file and in all. The fleet's
#: per-MESSAGE cap is 3 files and 25 MiB (Exchange Online's default message
#: limit is 35 MB, and base64 inflates a file by a third); the reply plugin
#: splits a larger set into up to three replies in the same thread.
MAX_FILES = 9
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 75 * 1024 * 1024
#: The largest source this connector will fetch to cut pages from (a 2,310-page
#: discovery set ran 413 MB on the miss). Streamed to disk, never held in memory.
MAX_SOURCE_BYTES = 600 * 1024 * 1024
#: Pages one extract may carry. A record request names a few pages of a set.
MAX_EXTRACT_PAGES = 200

_PAGE = 500
#: 60 pages of 500: 30,000 files. Past that the result says the search is partial.
_MAX_PAGES = 60
_MAX_MATCHES = 100
_ID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_RANGE_RE = re.compile(r"^\s*(\d{1,5})\s*(?:-\s*(\d{1,5}))?\s*$")
_NAME_CHARS = re.compile(r"[^\w .()-]+")


class StageRefused(ValueError):
    """One file (or the whole call) cannot be staged. The message is the reason."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def load_recipients(path: str | None = None) -> tuple[str, ...]:
    """``matter_file_delivery.recipients`` off the seat's own customer.yaml.

    Empty means unauthored, which stages nothing. Read on every call so an
    authoring change applies without a restart (the vendor_invoice_intake
    shape). Shape-checked loosely here: an entry is an address or an
    ``@domain``; the overlay and the broker apply the same list as the fence."""
    path = path or os.environ.get(CUSTOMER_YAML_ENV) or DEFAULT_CUSTOMER_YAML
    try:
        import yaml

        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001 - an unreadable config authors nothing
        return ()
    block = data.get(CONFIG_BLOCK) if isinstance(data, dict) else None
    raw = block.get("recipients") if isinstance(block, dict) else None
    if not isinstance(raw, list):
        return ()
    out = [str(v).strip().lower() for v in raw if isinstance(v, str) and "@" in v and v.strip()]
    return tuple(dict.fromkeys(out))


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def _norm_words(text: Any) -> str:
    return " ".join(re.sub(r"[^0-9a-z]+", " ", str(text or "").casefold()).split())


def _ext(row: dict[str, Any]) -> str:
    return str(row.get("fileExtension") or "").strip().lower().lstrip(".")


def _folder_id(row: dict[str, Any]) -> str | None:
    folder = row.get("folder")
    if isinstance(folder, dict):
        if folder.get("id"):
            return str(folder["id"])
        href = str(folder.get("href") or "")
        if "/folders/" in href:
            return href.rstrip("/").rsplit("/", 1)[-1]
    return None


def _folder_paths(client: Any, matter_id: str) -> dict[str, str]:
    """Folder id -> its path ("Medical/Records"). Raises when unreadable."""
    resp = client.get(f"/matters/{matter_id}/documents/folders", Limit=500, Offset=0)
    return {str(node["id"]): "/".join(path) for node, path in _walk_folder_paths(_listing(resp))}


def _all_files(client: Any, matter_id: str) -> tuple[list[dict[str, Any]], bool]:
    """Every file row and whether the listing was read to its end."""
    rows: list[dict[str, Any]] = []
    for page in range(_MAX_PAGES):
        batch = _listing(client.get(f"/matters/{matter_id}/documents/files", Limit=_PAGE, Offset=page * _PAGE))
        rows.extend(batch)
        if len(batch) < _PAGE:
            return rows, True
    return rows, False


def _folder_matches(path: str, wanted: str) -> bool:
    """A folder query matches a folder name at any depth, or a path suffix."""
    want = [_norm_words(p) for p in wanted.split("/") if p.strip()]
    have = [_norm_words(p) for p in path.split("/") if p.strip()]
    if not want or len(want) > len(have):
        return False
    if len(want) == 1:
        return want[0] in have
    return have[-len(want) :] == want


def _row_view(row: dict[str, Any], folders: dict[str, str]) -> dict[str, Any]:
    folder_id = _folder_id(row)
    return {
        "fileId": row.get("id"),
        "name": row.get("name"),
        "fileExtension": row.get("fileExtension"),
        "folderId": folder_id,
        "folder": folders.get(folder_id, "") if folder_id else "(matter root)",
        "sizeBytes": row.get("sizeBytes"),
        "dateCreated": row.get("dateCreated"),
        "dateModified": row.get("dateModified"),
    }


def find_files(
    client: Any,
    matter_id: str,
    *,
    name: str | None = None,
    folder: str | None = None,
    extension: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """The search behind ``find_files_on_matter``. Never returns a partial
    listing as if it were whole: a failed page is an ``error``, and a listing
    past the page bound says ``searchComplete: false``."""
    if not isinstance(matter_id, str) or not _ID_RE.match(matter_id.strip()):
        return {"error": f"{matter_id!r} is not a matter id (a UUID). Resolve the matter first with list_matters."}
    matter_id = matter_id.strip()
    words = _norm_words(name).split() if name else []
    ext = str(extension or "").strip().lower().lstrip(".")
    if not (words or (folder and folder.strip()) or ext):
        return {"error": "name, folder or extension is required: say what to look for."}
    limit = max(1, min(int(limit or 25), _MAX_MATCHES))
    try:
        rows, complete = _all_files(client, matter_id)
    except Exception as exc:  # noqa: BLE001 - could not look is never "none found"
        return {
            "error": f"the matter's file listing could not be read ({exc.__class__.__name__}); nothing was searched"
        }
    try:
        folders = _folder_paths(client, matter_id)
    except Exception as exc:  # noqa: BLE001 - unreadable folders: a folder filter cannot run, so it says so
        if folder:
            return {"error": f"the matter's folders could not be read ({exc.__class__.__name__}); nothing was searched"}
        folders = {}
    hits = []
    for row in rows:
        haystack = _norm_words(f"{row.get('name') or ''} {row.get('fileExtension') or ''}")
        if words and not all(w in haystack for w in words):
            continue
        if ext and _ext(row) != ext:
            continue
        if folder and not _folder_matches(folders.get(_folder_id(row) or "", ""), folder):
            continue
        hits.append(_row_view(row, folders))
    hits.sort(key=lambda r: str(r.get("dateModified") or ""), reverse=True)
    out: dict[str, Any] = {
        "matterId": matter_id,
        "query": {"name": name, "folder": folder, "extension": extension},
        "files": hits[:limit],
        "matchCount": len(hits),
        "returned": min(len(hits), limit),
        "searchedFiles": len(rows),
        "searchComplete": complete,
    }
    if not complete:
        out["note"] = (
            f"The matter holds more than {_PAGE * _MAX_PAGES} files and only that many were searched; "
            "a file not found here may still be on the matter."
        )
    elif len(hits) > limit:
        out["note"] = f"{len(hits)} files matched; the {limit} most recently changed are shown. Narrow the search."
    return out


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Request:
    file_id: str
    pages: tuple[int, int] | None


def parse_requests(files: Any) -> list[Request]:
    """``[{"file_id": ..., "pages": "3-11"}]`` -> requests, or :class:`StageRefused`."""
    if not isinstance(files, list) or not files:
        raise StageRefused("files must be a non-empty list of {file_id, pages?}")
    if len(files) > MAX_FILES:
        raise StageRefused(f"at most {MAX_FILES} files go out for one request")
    out: list[Request] = []
    for item in files:
        item = {"file_id": item} if isinstance(item, str) else item
        if not isinstance(item, dict):
            raise StageRefused("each file is {file_id, pages?}")
        fid = str(item.get("file_id") or item.get("fileId") or "").strip()
        if not _ID_RE.match(fid):
            raise StageRefused(f"{fid!r} is not a file id (a UUID from find_files_on_matter)")
        out.append(Request(fid, parse_pages(item.get("pages"))))
    keys = [(r.file_id.lower(), r.pages) for r in out]
    if len(set(keys)) != len(keys):
        raise StageRefused("the same file and pages are named twice")
    return out


def parse_pages(value: Any) -> tuple[int, int] | None:
    """``"3-11"`` / ``"4"`` / ``None`` -> an inclusive 1-based range."""
    if value in (None, ""):
        return None
    m = _RANGE_RE.match(str(value))
    if not m:
        raise StageRefused(f"pages {value!r} is not a range like 3-11")
    first = int(m.group(1))
    last = int(m.group(2) or first)
    if first < 1 or last < first:
        raise StageRefused(f"pages {value!r} is not a range like 3-11")
    if last - first + 1 > MAX_EXTRACT_PAGES:
        raise StageRefused(f"pages {value!r} is more than {MAX_EXTRACT_PAGES} pages; ask for fewer")
    return first, last


def attachment_name(name: Any, pages: tuple[int, int] | None, taken: set[str]) -> str:
    """A plain ``.pdf`` name the attachment validators accept, unique in this reply."""
    base = _NAME_CHARS.sub(" ", str(name or "").strip())
    base = " ".join(base.split()).strip(" .") or "document"
    if base.lower().endswith(".pdf"):
        base = base[:-4].rstrip(" .") or "document"
    if pages:
        base += f" pp. {pages[0]}" + (f"-{pages[1]}" if pages[1] != pages[0] else "")
    base = base[:110].rstrip(" .")
    candidate, n = f"{base}.pdf", 2
    while candidate.lower() in taken:
        candidate, n = f"{base} ({n}).pdf", n + 1
    taken.add(candidate.lower())
    return candidate


def _on_matter(meta: Any, matter_id: str) -> bool:
    """Does the file's OWN record name this matter? Absent is not proof."""
    if not isinstance(meta, dict):
        return False
    matter = meta.get("matter")
    named = ""
    if isinstance(matter, dict):
        named = str(matter.get("id") or "") or str(matter.get("href") or "").rstrip("/").rsplit("/", 1)[-1]
    named = named or str(meta.get("matterId") or "")
    return bool(named) and named.lower() == matter_id.lower()


def _download_to(client: Any, matter_id: str, file_id: str, dest: Path, cap: int) -> int:
    """Stream a file to ``dest``; refuse past ``cap`` bytes. Returns the size."""
    info = client.request("GET", f"/matters/{matter_id}/documents/files/{file_id}/download")
    if not isinstance(info, dict) or not info.get("downloadUrl"):
        raise StageRefused("Smokeball gave no download for this file")
    size = 0
    with client._http.stream("GET", info["downloadUrl"]) as resp:
        if resp.status_code >= 400:
            raise StageRefused(f"the download was refused (HTTP {resp.status_code})")
        with open(dest, "wb") as fh:
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > cap:
                    raise StageRefused(f"the file is over {cap // (1024 * 1024)} MB, too large to fetch here")
                fh.write(chunk)
    return size


def _cut_pages(source: Path, pages: tuple[int, int]) -> bytes:
    """The named pages of the PDF at ``source``, as a new PDF.

    Cut in a memory-capped child process from a file handle (page_cut.py): a
    413 MB set must never be read whole into the connector, a child of the
    gateway."""
    from .page_cut import CutRefused, cut

    try:
        return cut(source, pages[0], pages[1], source.with_suffix(".cut.pdf"))
    except CutRefused as exc:
        raise StageRefused(str(exc)) from exc
    finally:
        source.with_suffix(".cut.pdf").unlink(missing_ok=True)


def spool_dir() -> Path:
    return Path(os.environ.get(SPOOL_DIR_ENV, "").strip() or DEFAULT_SPOOL_DIR)


def _prune(base: Path) -> None:
    cutoff = time.time() - SPOOL_TTL_SECONDS
    for path in base.iterdir():
        try:
            if not path.is_symlink() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def spool_write(blob: bytes, meta: dict[str, Any]) -> str:
    """Write ``<token>.bin`` + ``<token>.json`` 0600, create-exclusive. The token."""
    base = spool_dir()
    base.mkdir(parents=True, exist_ok=True)
    # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions - owner-only on purpose; client documents
    os.chmod(base, 0o700)
    _prune(base)
    token = secrets.token_hex(16)
    for suffix, data in ((".bin", blob), (".json", json.dumps(meta, sort_keys=True).encode())):
        fd = os.open(base / f"{token}{suffix}", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    return token


def _fetch_one(client: Any, matter_id: str, req: Request, workdir: Path) -> tuple[dict[str, Any], bytes]:
    """(file metadata, the bytes to attach) for one request, or StageRefused."""
    meta = client.get(f"/matters/{matter_id}/documents/files/{req.file_id}")
    if not _on_matter(meta, matter_id):
        raise StageRefused("the file's own record does not name this matter, so it is not sent")
    if str(meta.get("fileExtension") or "").strip().lower().lstrip(".") != "pdf":
        raise StageRefused(f"only PDF files can be attached; this one is {meta.get('fileExtension') or 'not a PDF'}")
    source = workdir / f"{req.file_id}.pdf"
    _download_to(client, matter_id, req.file_id, source, MAX_SOURCE_BYTES if req.pages else MAX_FILE_BYTES)
    try:
        with open(source, "rb") as fh:
            if fh.read(5) != b"%PDF-":
                raise StageRefused("the file is not a PDF")
        blob = _cut_pages(source, req.pages) if req.pages else source.read_bytes()
    finally:
        source.unlink(missing_ok=True)
    if len(blob) > MAX_FILE_BYTES:
        raise StageRefused(
            f"it is {len(blob) / 1048576:.1f} MB, over the {MAX_FILE_BYTES // 1048576} MB one email can carry; "
            "ask for a page range"
        )
    return meta, blob


def stage(client: Any, matter_id: str, files: Any, *, recipients: tuple[str, ...] | None = None) -> dict[str, Any]:
    """The work behind ``stage_matter_files``."""
    recipients = load_recipients() if recipients is None else recipients
    if not recipients:
        return {
            "status": "refused",
            "reason": "this seat does not send matter files by email (matter_file_delivery is not authored)",
        }
    if not isinstance(matter_id, str) or not _ID_RE.match(matter_id.strip()):
        return {"status": "refused", "reason": f"{matter_id!r} is not a matter id (a UUID)"}
    matter_id = matter_id.strip()
    try:
        requests = parse_requests(files)
    except StageRefused as exc:
        return {"status": "refused", "reason": str(exc)}
    staged: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    taken: set[str] = set()
    total = 0
    # On the seat volume beside the spool, not /tmp: a source for a page cut can
    # be hundreds of MB, and the root filesystem is the image's.
    work = spool_dir()
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="smd-matter-files-", dir=work) as tmp:
        for req in requests:
            label = {"fileId": req.file_id, **({"pages": f"{req.pages[0]}-{req.pages[1]}"} if req.pages else {})}
            try:
                meta, blob = _fetch_one(client, matter_id, req, Path(tmp))
                if total + len(blob) > MAX_TOTAL_BYTES:
                    raise StageRefused(
                        f"adding it would take this request over the {MAX_TOTAL_BYTES // 1048576} MB three replies carry"
                    )
            except StageRefused as exc:
                refused.append({**label, "reason": str(exc)})
                continue
            except Exception as exc:  # noqa: BLE001 - one file's failure is named, never fatal to the rest
                refused.append({**label, "reason": f"it could not be fetched ({exc.__class__.__name__})"})
                continue
            total += len(blob)
            name = attachment_name(meta.get("name"), req.pages, taken)
            sha = hashlib.sha256(blob).hexdigest()
            token = spool_write(
                blob,
                {
                    "matter_id": matter_id,
                    "file_id": req.file_id,
                    "attachment_name": name,
                    "sha256": sha,
                    "size": len(blob),
                    "spooled_at": time.time(),
                },
            )
            staged.append(
                {
                    **label,
                    "name": meta.get("name"),
                    "attachmentName": name,
                    "sizeBytes": len(blob),
                    "sha256": sha,
                    "spoolToken": token,
                }
            )
    result: dict[str, Any] = {
        "status": "staged" if staged else "refused",
        "matterId": matter_id,
        "staged": staged,
        "refused": refused,
    }
    result["note"] = (
        "Staged files go ONLY on your reply to the colleague whose email asked for them, in this turn. "
        "Name in the reply each file attached and each one that could not be, with its reason."
        if staged
        else "Nothing was staged; tell the person which files could not be sent and why."
    )
    return result


def register(server: Any) -> None:
    """The two tools, registered through attachment_tools (server.py is size-ratcheted)."""

    def _client() -> Any:
        from . import server as _server

        return _server._get_client()

    def _enrich(resp: dict[str, Any]) -> dict[str, Any]:
        from . import server as _server

        if resp.get("matterId"):
            _server._attach_matter_ref(_client(), resp, matter_id=resp["matterId"])
        return resp

    @server.tool()
    def find_files_on_matter(
        matter_id: str,
        name: str | None = None,
        folder: str | None = None,
        extension: str | None = None,
        limit: int = 25,
    ) -> Any:
        """Find a matter's files by NAME words, FOLDER and/or EXTENSION.

        Use this, not get_files_on_matter, when looking for named documents:
        it reads every page of the matter's file listing and filters here.
        ``name`` matches when every word appears in the file name (any order,
        case and punctuation ignored); ``folder`` matches a folder name at any
        depth or a path like "Medical/Records"; ``extension`` like "pdf".
        Returns ``fileId``, ``name``, ``fileExtension``, ``folder``,
        ``sizeBytes``, ``dateCreated``, ``dateModified``, newest first, plus
        ``matchCount`` and ``searchComplete`` (false means a match could exist
        past what was searched; say so rather than "not on the matter")."""
        return _enrich(find_files(_client(), matter_id, name=name, folder=folder, extension=extension, limit=limit))

    @server.tool()
    def stage_matter_files(matter_id: str, files: list[dict[str, Any]]) -> Any:
        """Stage matter PDFs to go ATTACHED on your reply to the firm colleague
        who emailed asking for them. ``files``: up to 9 of
        ``{"file_id": "<from find_files_on_matter>", "pages": "3-11"}``;
        ``pages`` is optional and cuts just those pages out of a large file.

        The seat fetches the files itself and attaches them to the reply you
        then draft, only to that colleague, only in this turn. Each file that
        cannot go is listed under ``refused`` with its reason; tell the person.
        Only on a seat that authors matter_file_delivery."""
        return _enrich(stage(_client(), matter_id, files))


__all__ = [
    "CONFIG_BLOCK",
    "DEFAULT_SPOOL_DIR",
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "MAX_TOTAL_BYTES",
    "SPOOL_DIR_ENV",
    "StageRefused",
    "attachment_name",
    "find_files",
    "load_recipients",
    "parse_pages",
    "parse_requests",
    "register",
    "spool_write",
    "stage",
]
