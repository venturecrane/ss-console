"""The connector's reader, ``extract.extract_text_ex``, over every fixture.

The connector reads a firm's file for the agent (``read_document``, the
record check, the corpus tools). Its edge is the Messages API over HTTP:
``vision._http_client`` is given a mock transport here, the counting vision
stub. Every request is recorded as its shape and the one page it carried
(``reader_kit.page_of``); the stream's answer names that page.

Three modes, each a cell:

* ``mechanical``: ``allow_vision=False``, an empty transcription cache: what the
  record check sees (a scan is ``none_scanned``, nothing is sent).
* ``vision``: ``allow_vision=True`` (``read_document``): the scans are sent.
* ``cached``: the same files again after ``vision``: what the cache serves,
  and what is sent again (nothing, today).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import reader_kit as rk
from reader_fixtures import FIXTURES

KNOBS = (
    "SMOKEBALL_VISION_DISABLED",
    "SMOKEBALL_VISION_MODEL",
    "SMOKEBALL_VISION_PAGE_CAP",
    "SMOKEBALL_VISION_MAX_BYTES",
    "SMOKEBALL_VISION_LOCK_TIMEOUT_SECONDS",
    "SMOKEBALL_EXTRACT_CACHE_MAX_BYTES",
)


def _sse(text: str) -> bytes:
    events = [
        {"type": "message_start", "message": {"id": "msg_reader"}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
        {"type": "message_stop"},
    ]
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


class VisionHttp:
    """The Messages API at the connector's HTTP seam: one answer per page."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def handler(self, request: Any) -> Any:
        import httpx

        params = json.loads(request.content)
        pages = [
            rk.page_of(b["source"])
            for b in rk._blocks(params.get("messages"))
            if b.get("type") in ("image", "document")
        ]
        shown = next((p["shows"] for p in pages if p.get("shows")), None)
        self.calls.append({"shape": rk.shape(params), "pages": pages, "url": str(request.url)})
        return httpx.Response(200, content=_sse(rk.reply_for(shown, params)))

    def record(self) -> dict[str, Any]:
        m = rk.Model()
        m.calls = [{"shape": {**c["shape"], "url": c["url"]}, "pages": c["pages"]} for c in self.calls]
        return m.record()


def _env(tmp: Path, mp: Any) -> None:
    from smokeball_connector import extract_cache

    mp.setenv(extract_cache.CACHE_DIR_ENV, str(tmp / "vision-cache"))
    mp.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    mp.setenv("SMOKEBALL_VISION_WORKERS", "1")
    for k in KNOBS:
        mp.delenv(k, raising=False)


def _read_all(allow_vision: bool) -> dict[str, Any]:
    from smokeball_connector.extract import UnsupportedDocumentError, extract_text_ex

    out: dict[str, Any] = {}
    for key, f in FIXTURES.items():
        try:
            r = extract_text_ex(f.data, file_name=f.full, file_extension=f.ext, allow_vision=allow_vision)
            out[key] = {"text": rk.paged(r.text), "method": r.method, "reason": r.reason, "pages": r.pages}
        except UnsupportedDocumentError as exc:
            out[key] = {"refused": str(exc)[:300]}
    return out


def _mode(name: str) -> Any:
    def run(tmp: Path, mp: Any) -> dict[str, Any]:
        import httpx

        from smokeball_connector import vision

        _env(tmp, mp)
        http = VisionHttp()
        mp.setattr(vision, "_http_client", lambda _t: httpx.Client(transport=httpx.MockTransport(http.handler)))
        doc: dict[str, Any] = {}
        if name == "cached":
            _read_all(True)
            http.calls.clear()
        doc["files"] = _read_all(name != "mechanical")
        doc["sent"] = http.record()
        doc["cache"] = rk.tree(tmp / "vision-cache", tmp)
        return rk.scrub(doc, tmp)

    return run


MODES = {"mechanical": _mode("mechanical"), "vision": _mode("vision"), "cached": _mode("cached")}

__all__ = ["MODES", "VisionHttp"]
