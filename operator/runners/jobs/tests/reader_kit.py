"""The ``reader`` goldens' recorders, at the process edges only.

A refactor of how a firm's file is read (wave 5: one reader, one downloader,
one store of paid page text) moves code between modules; the edges it talks to
do not move. So every recorder here sits on one of four:

* the MODEL CLIENT (``Model``): the SDK object a lane's doorway calls
  ``messages.create`` on, and the connector's HTTP transport (``vision_http``).
  Each request is recorded as its shape (model, max tokens, a sha256 of its
  system prompt and of its text blocks, its keys) and the page it carried,
  decoded from the page's colour (``reader_fixtures.identity``) with the render
  size; never a pixel. The reply names the page it was shown.
* the SEAT (``Seat``): ``list_files``, ``folder_tree``, ``mint``, ``fetch``,
  ``file_meta``, in call order, with a scripted outcome per call (a mint that
  raises, a 404, 403 or 429, a size the listing no longer has, a storage error
  body).
* SLEEP (``Sleeps``): ``time.sleep`` as called, in seconds.
* the FILE TREE (``tree``): every file a run left under a root, its path and its
  content (JSON parsed, timestamps and job ids out).

``scrub`` writes the test's temp dir as ``<TMP>`` and each fixture's sha256 as
``<sha:<key>>``, so no golden carries a hex digest.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import goldens_kit as gk
from reader_fixtures import FIXTURES, INDEX

# ---- scrubbing ---------------------------------------------------------------------------
_HEX64 = re.compile(r"[0-9a-f]{64}")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?")


def _shas() -> dict[str, str]:
    out = {}
    for key, f in FIXTURES.items():
        out[hashlib.sha256(f.data).hexdigest()] = key
    return out


SHAS = _shas()


#: Keys whose value is a clock reading.
TIME_KEYS = frozenset({"created_at", "at", "ts", "time", "mtime", "seconds", "touched"})


def _tmp_out(s: str, tmp: str) -> str:
    """The temp dir written ``<TMP>``, a path cut short by a reader's own
    message limit included."""
    s = s.replace(tmp, "<TMP>")
    head = tmp[:24]
    i = s.find(head)
    while i >= 0:
        k = 0
        while k < len(tmp) and i + k < len(s) and s[i + k] == tmp[k]:
            k += 1
        s = s[:i] + "<TMP>" + s[i + k :]
        i = s.find(head)
    return s


def scrub(x: Any, tmp: Path | str) -> Any:
    """``x`` with the temp dir, every fixture sha256 (whole or its 12-character
    prefix), any other sha256 and every timestamp written as placeholders."""
    if isinstance(x, str):
        s = _tmp_out(x, str(tmp))
        s = _HEX64.sub(lambda m: f"<sha:{SHAS[m.group()]}>" if m.group() in SHAS else f"<sha256:{m.group()[:12]}>", s)
        for full, key in SHAS.items():
            s = s.replace(full[:12], f"<sha12:{key}>")
        return _STAMP.sub("<AT>", s)
    if isinstance(x, dict):
        return {scrub(k, tmp): "<AT>" if k in TIME_KEYS and x[k] is not None else scrub(v, tmp) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [scrub(v, tmp) for v in x]
    return x


#: A page marker as a lane writes one: litigation and negotiation's
#: ``===== page N =====``, the chronology and demand's ``[p.N]`` (a machine
#: transcription's note after it).
_MARK = re.compile(r"(?m)^(?====== page \d+ =====$|\[p\.\d+\])")
#: A piece longer than this is recorded as its length, a short sha256 and its ends.
LONG = 6000


def paged(text: str | None) -> Any:
    """``text`` as its pages, each piece opening with its marker, so a change
    to one page names that page. A piece over ``LONG`` characters (an email's
    encoded attachment read as plain text) is its length, sha and ends."""
    if text is None:
        return None
    pieces = [p for p in _MARK.split(text) if p] or [""]
    return [
        p if len(p) <= LONG else {"chars": len(p), "sha12": gk.sha(p)[:12], "head": p[:300], "tail": p[-300:]}
        for p in pieces
    ]


def tree(root: Path, tmp: Path) -> dict[str, Any]:
    """Every file under ``root``: its path, and its content (JSON parsed,
    text as text, other bytes as their length)."""
    out: dict[str, Any] = {}
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = scrub(p.relative_to(root).as_posix(), tmp)
        raw = p.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            out[rel] = {"bytes": len(raw)}
            continue
        if p.suffix in (".json", ".jsonl"):
            try:
                doc: Any = json.loads(text) if p.suffix == ".json" else [json.loads(x) for x in text.splitlines() if x]
                out[rel] = scrub(doc, tmp)
                continue
            except ValueError:
                pass
        out[rel] = (
            scrub(text, tmp) if len(text) <= 200 else {"chars": len(text), "sha12": gk.sha(scrub(text, tmp))[:12]}
        )
    return out


# ---- the model client --------------------------------------------------------------------
def _colour_page(pix: Any) -> dict[str, Any] | None:
    r, g, b = pix.pixel(pix.width // 2, int(pix.height * 0.9))[:3]
    if abs(b - 200) > 12:
        return None
    key = INDEX.get(round(r / 8))
    return {"fixture": key, "page": round(g / 8)} if key else None


def page_of(source: dict[str, Any]) -> dict[str, Any]:
    """The page a base64 image or one-page PDF carries, and its size."""
    import pymupdf

    data = base64.b64decode(source.get("data") or "")
    media = source.get("media_type")
    if media == "application/pdf":
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            pg = doc[0]
            size = [round(pg.rect.width), round(pg.rect.height)]
            pix = pg.get_pixmap(dpi=24)
            return {"media": media, "size": size, "pages": len(doc), "shows": _colour_page(pix)}
    pix = pymupdf.Pixmap(data)
    return {"media": media, "size": [pix.width, pix.height], "shows": _colour_page(pix)}


def _blocks(messages: Any) -> list[dict[str, Any]]:
    out = []
    for m in messages or []:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, str):
            out.append({"type": "text", "text": c})
        elif isinstance(c, list):
            out += [b for b in c if isinstance(b, dict)]
    return out


def _system_text(system: Any) -> str:
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return "".join(str(b.get("text") or "") for b in system if isinstance(b, dict))
    return ""


def shape(params: dict[str, Any]) -> dict[str, Any]:
    """A request without its pages: what a refactor must keep byte for byte."""
    texts = [str(b.get("text") or "") for b in _blocks(params.get("messages")) if b.get("type") == "text"]
    oc = params.get("output_config") or {}
    return {
        "model": params.get("model"),
        "max_tokens": params.get("max_tokens"),
        "system_sha256": gk.sha(_system_text(params.get("system"))) if params.get("system") else None,
        "prompt_sha256": gk.sha("\n".join(texts)),
        "effort": oc.get("effort") if isinstance(oc, dict) else None,
        "keys": sorted(params),
        "blocks": [b.get("type") for b in _blocks(params.get("messages"))],
    }


def reply_for(shown: dict[str, Any] | None, params: dict[str, Any]) -> str:
    body = f"TRANSCRIPT OF {shown['fixture']} PAGE {shown['page']}" if shown else "TRANSCRIPT OF AN UNMARKED PAGE"
    if "HANDWRITING" in _system_text(params.get("system")):
        return "HANDWRITING: no\n" + body
    return body


class Model:
    """The SDK client a doorway is given. Thread-safe; every request is kept."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **params: Any) -> Any:
        pages = [
            page_of(b["source"]) for b in _blocks(params.get("messages")) if b.get("type") in ("image", "document")
        ]
        shown = next((p["shows"] for p in pages if p.get("shows")), None)
        with self.lock:
            self.calls.append({"shape": shape(params), "pages": pages})
        usage = SimpleNamespace(
            input_tokens=1200, output_tokens=40, cache_read_input_tokens=0, cache_creation_input_tokens=0
        )
        return SimpleNamespace(
            id="msg-reader", model=params.get("model"), content=[{"type": "text", "text": reply_for(shown, params)}],
            stop_reason="end_turn", usage=usage,
        )  # fmt: skip

    def record(self) -> dict[str, Any]:
        """The requests as an ordered list of pages, each naming its shape by
        index into ``shapes`` (one entry per distinct request shape)."""
        shapes: list[dict[str, Any]] = []
        sent = []
        for c in self.calls:
            if c["shape"] not in shapes:
                shapes.append(c["shape"])
            sent.append({"shape": shapes.index(c["shape"]), "pages": c["pages"]})
        return {"shapes": shapes, "sent": sent}


# ---- the seat ----------------------------------------------------------------------------
class Seat:
    """The seat interface over the fixture files, with a scripted outcome per call.

    ``mint_raises``: how many mint calls raise before one succeeds.
    ``script[file id]``: one outcome per fetch of that file, used in turn, the
    last repeated: ``ok``; ``("bytes", data)`` serves other bytes (the file
    changed since it was listed); ``("status", n)`` an HTTP error; ``nosuchkey``
    the storage service's error body with a 200."""

    def __init__(
        self, docs: list[dict[str, Any]], blobs: dict[str, bytes], folders: list[dict[str, Any]] | None = None
    ) -> None:
        self.docs, self.blobs, self.folders = docs, blobs, folders or []
        self.mint_raises = 0
        self.script: dict[str, list[Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.client = SimpleNamespace(get=self._get)

    def _get(self, path: str, **params: Any) -> Any:
        self.events.append({"call": "client.get", "path": path})
        return {}

    def list_files(self, matter_id: str) -> list[dict[str, Any]]:
        self.events.append({"call": "list_files"})
        return [dict(d) for d in self.docs]

    def folder_tree(self, matter_id: str) -> list[dict[str, Any]]:
        self.events.append({"call": "folder_tree"})
        return [dict(f) for f in self.folders]

    def file_meta(self, matter_id: str, file_id: str) -> dict[str, Any]:
        self.events.append({"call": "file_meta", "id": file_id})
        d = next((x for x in self.docs if str(x["id"]) == str(file_id)), {})
        return {"id": file_id, "name": d.get("name"), "fileExtension": d.get("ext"), "isDeleted": False}

    def mint(self, matter_id: str, file_ids: list[str]) -> list[dict[str, Any]]:
        self.events.append({"call": "mint", "ids": list(file_ids)})
        if self.mint_raises:
            self.mint_raises -= 1
            raise RuntimeError("mint: the vendor answered 503")
        out = []
        for fid in file_ids:
            if fid in self.blobs:
                out.append({"id": fid, "url": f"https://fake.invalid/{fid}", "size": len(self.blobs[fid])})
            else:
                out.append({"id": fid, "error": "not found"})
        return out

    def _outcome(self, fid: str) -> Any:
        steps = self.script.get(fid) or ["ok"]
        return steps.pop(0) if len(steps) > 1 else steps[0]

    def fetch(self, url: str, dest: Path, expected_size: int | None) -> int:
        """``jobs.seat.fetch_https``'s contract: the bytes at ``dest``, and a
        size other than ``expected_size`` removes them and raises."""
        import httpx

        from jobs.seat import SeatError

        fid = url.rsplit("/", 1)[1]
        out = self._outcome(fid)
        ev: dict[str, Any] = {"call": "fetch", "id": fid, "expected": expected_size}
        self.events.append(ev)
        if isinstance(out, tuple) and out[0] == "status":
            ev["answer"] = out[1]
            req = httpx.Request("GET", url)
            phrase = {403: "Forbidden", 404: "Not Found", 429: "Too Many Requests"}.get(out[1], "Error")
            raise httpx.HTTPStatusError(
                f"Client error '{out[1]} {phrase}' for url '{url}'",
                request=req,
                response=httpx.Response(out[1], request=req),
            )
        if out == "nosuchkey":
            data = b'<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>NoSuchKey</Code><Message>The specified key does not exist.</Message></Error>'
        elif isinstance(out, tuple) and out[0] == "bytes":
            data = out[1]
        else:
            data = self.blobs[fid]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        ev["served"] = len(data)
        if expected_size and len(data) != expected_size:
            dest.unlink()
            raise SeatError(f"size mismatch: got {len(data)}, expected {expected_size}; re-pull")
        return len(data)


def listing(keys: list[str], folder: str | None = "fold-med") -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    """The seat's rows for these fixtures (name and extension apart) and their bytes."""
    docs, blobs = [], {}
    for k in keys:
        f = FIXTURES[k]
        docs.append({"id": f.id, "name": f.name, "ext": f.ext, "size": len(f.data), "folderId": folder,
                     "created": "2026-01-20T10:00:00", "modified": "2026-01-20T10:00:00", "deleted": False})  # fmt: skip
        blobs[f.id] = f.data
    return docs, blobs


class Sleeps:
    """``time.sleep``, recorded and never slept."""

    def __init__(self, mp: Any) -> None:
        self.calls: list[float] = []
        mp.setattr(time, "sleep", lambda s: self.calls.append(round(float(s), 3)))


def by_id() -> dict[str, str]:
    return {f.id: k for k, f in FIXTURES.items()}


__all__ = ["Model", "Seat", "Sleeps", "by_id", "listing", "page_of", "reply_for", "scrub", "shape", "tree"]
