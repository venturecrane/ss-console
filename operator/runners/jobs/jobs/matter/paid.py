"""A page read by a model: how it is asked for, and the store in front of it.

* :class:`Method`: what makes two reads of a page the same read (the system
  prompt, the user text, the model, the max tokens and how the page was
  rendered), and its :attr:`Method.version`, the store's key for it.
* :func:`ocr_plain`: the court lanes' one-page request (litigation and the
  negotiation watch: the image, then the prompt; no system prompt).
* :func:`stored`: a lane's paid read with the page store (``store``) in front.
  The page being read is named by the PDF reader around each call
  (:func:`page`), and the matter by the lane (:func:`for_matter`), on this
  thread, so a lane's page reader keeps its one-argument shape (``ocr(png)``)
  and a test's plain function still reads a page with no store at all.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from .policy import Policy

_HERE = threading.local()


@dataclass(frozen=True)
class Method:
    system: str | None
    prompt: str
    model: str
    max_tokens: int
    render: str

    @property
    def version(self) -> str:
        parts = [self.system, self.prompt, self.model, self.max_tokens, self.render]
        return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:12]


def method(policy: Policy, model: str, render: str | None = None) -> Method:
    """The lane's read of a page by ``model`` (its row's own model when it fixes one)."""
    return Method(policy.system, policy.prompt, policy.model or model, policy.max_tokens, render or policy.render)


def ocr_plain(doorway: Any, stage: str, *, model: str, png: bytes, prompt: str, max_tokens: int) -> tuple[str, str]:
    """One page image, then the prompt: the court lanes' transcription request.
    (its text, its stop reason)."""
    img = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": base64.standard_b64encode(png).decode()},
    }
    r = doorway.call(
        stage,
        model=model,
        messages=[{"role": "user", "content": [img, {"type": "text", "text": prompt}]}],
        max_tokens=max_tokens,
        cache_blocks=(),
    )
    return r.text, str(getattr(r, "stop_reason", "") or "")


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def content_sha(src: bytes | Path) -> Callable[[], str]:
    """The file's sha256, computed once and only when a page of it is read."""
    got: list[str] = []

    def sha() -> str:
        if not got:
            got.append(sha_file(src) if isinstance(src, Path) else sha_bytes(src))
        return got[0]

    return sha


@contextmanager
def page(sha: Callable[[], str] | str | None, n: int, render: str) -> Iterator[None]:
    """Name the page (its file's sha, 1-based number and render) a page reader
    is about to read on this thread."""
    prev = getattr(_HERE, "page", None)
    _HERE.page = (sha, int(n), render)
    try:
        yield
    finally:
        _HERE.page = prev


@contextmanager
def for_matter(matter_id: str) -> Iterator[None]:
    """Name the matter this thread is reading for (a stored page records it)."""
    prev = getattr(_HERE, "matter", "")
    _HERE.matter = str(matter_id or "")
    try:
        yield
    finally:
        _HERE.matter = prev


def stored(store: Any, method_of: Callable[[str], Method], read: Callable[[], tuple[str, str]]) -> str:
    """``read()`` (the paid call: its text and stop reason) unless the store
    holds this page read this way; a fresh read is then kept when it finished
    (``store.COMPLETE_STOPS``). With no store, or no page named, it is the paid
    call alone (counted as paid when a store is there to count it)."""
    here = getattr(_HERE, "page", None)
    if store is None:
        return read()[0]
    if here is None:
        store.missed()
        return read()[0]
    src, n, render = here
    sha = src() if callable(src) else src
    m = method_of(render).version
    matter = getattr(_HERE, "matter", "")
    if not sha:
        store.missed()
        return read()[0]
    hit = store.get(sha, n, m, matter)
    if hit is not None:
        return hit
    text, stop = read()
    store.put(sha, n, m, text, matter, stop_reason=stop)
    return text


__all__ = ["Method", "content_sha", "for_matter", "method", "ocr_plain", "page", "sha_bytes", "sha_file", "stored"]
