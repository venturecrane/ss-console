"""The model reads the whole corpus, ten pages at a time, and proposes facts.

One call per window of ``WINDOW_PAGES`` pages of one document, through the
lane's doorway like every other drafting stage. The answer is a JSON array of
``Candidate`` objects; nothing in it is trusted until ``verify`` has checked
it against the page.

Each window's answer is cached under the job's data directory by the
window's sha (its document name, pages, text, the prompt and the model), so a
resume pays only for windows not yet read, and a changed page or prompt is
read again rather than collecting a stale answer. An answer that is not a
JSON array gets one corrective retry; an answer that reaches the output
ceiling is split by pages and read again; a single page that still cannot be
read fails the stage (``ExtractError``) with nothing cached for it.
"""

from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from ...medchron.audit.page_text import parse_segments
from . import prompts
from .shapes import Candidate, DocPages

WINDOW_PAGES = 10
EXTRACT_MAX_TOKENS = 32_000
STAGE = "evidence_extract"
CACHE_DIR = ("evidence", "extract")


class ExtractError(RuntimeError):
    """A window could not be read: ``failed``, resumable."""


@dataclass(frozen=True)
class Window:
    name: str
    pages: tuple[int, ...]
    text: str
    sha: str


def load_corpus(data: Path) -> dict[str, DocPages]:
    """Every extracted document as page text, from ``extracted.jsonl``: the
    same files the digest and the gate read. A page marked
    ``(machine transcription)`` (or a document named in ``transcribed.json``)
    is a machine page. A file with no ``[p.N]`` markers is one page."""
    rows = [json.loads(ln) for ln in (data / "extracted.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    machine_names: set[str] = set()
    tp = data / "transcribed.json"
    if tp.is_file():
        machine_names = {str(x) for x in json.loads(tp.read_text(encoding="utf-8"))}
    out: dict[str, DocPages] = {}
    for r in rows:
        p = r.get("text_path")
        if not p or not Path(p).is_file():
            continue
        text = Path(p).read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            continue
        name = str(r.get("name"))
        if "[p." in text:
            segs = parse_segments(text)
            pages = {n: t for n, (t, _m) in segs.items()}
            mach = {n for n, (_t, m) in segs.items() if m}
        else:
            pages, mach = {1: text}, set()
        if name in machine_names:
            mach = set(pages)
        out[name] = DocPages(name, pages, frozenset(mach), r.get("source_path"))
    return out


def _sha(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def windows(
    corpus: Mapping[str, DocPages], model: str, size: int = WINDOW_PAGES, prompt: str = prompts.EXTRACT_PROMPT
) -> list[Window]:
    """Each document in windows of ``size`` pages, in page order; a window
    never spans two documents."""
    out = []
    for name, doc in corpus.items():
        pages = sorted(doc.pages)
        for i in range(0, len(pages), size):
            out.append(_window(name, doc, tuple(pages[i : i + size]), model, prompt))
    return out


def _window(name: str, doc: DocPages, pages: tuple[int, ...], model: str, prompt: str) -> Window:
    text = prompts.extract_user(name, {p: doc.pages[p] for p in pages})
    return Window(name, pages, text, _sha(prompts.EXTRACT_VERSION, model, prompt, text))


def parse_array(text: str) -> list[dict[str, Any]] | None:
    """The first well-formed JSON array of objects in the answer, else None."""
    dec = json.JSONDecoder()
    for m in re.finditer(r"\[", text or ""):
        try:
            got, _ = dec.raw_decode(text, m.start())
        except ValueError:
            if re.match(r"\[\s*\{", text[m.start() :]):
                return None
            continue
        if isinstance(got, list) and all(isinstance(x, dict) for x in got):
            return got
    return None


class Extractor:
    def __init__(
        self,
        data: Path,
        doorway: Any,
        model: str,
        corpus: Mapping[str, DocPages],
        *,
        prompt: str = prompts.EXTRACT_PROMPT,
        max_tokens: int = EXTRACT_MAX_TOKENS,
        workers: int = 4,
        log: Callable[[str], None] = print,
    ) -> None:
        self.data, self.doorway, self.model, self.corpus = data, doorway, model, corpus
        self.prompt, self.max_tokens, self.workers, self.log = prompt, max_tokens, workers, log
        self.cache = data.joinpath(*CACHE_DIR)

    def _cached(self, w: Window) -> list[Candidate] | None:
        p = self.cache / f"{w.sha}.json"
        if not p.is_file():
            return None
        got = json.loads(p.read_text(encoding="utf-8"))
        return [Candidate.from_json(d, w.sha) for d in got["candidates"]]

    def _store(self, w: Window, raw: list[dict[str, Any]]) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        tmp = self.cache / f".{w.sha}.json.tmp"
        tmp.write_text(
            json.dumps({"file": w.name, "pages": list(w.pages), "candidates": raw}, indent=1), encoding="utf-8"
        )
        tmp.replace(self.cache / f"{w.sha}.json")

    def read(self, w: Window) -> list[Candidate]:
        got = self._cached(w)
        if got is not None:
            return got
        messages: list[dict[str, Any]] = [{"role": "user", "content": w.text}]
        raw = None
        for attempt in (0, 1):
            r = self.doorway.call(
                STAGE,
                model=self.model,
                system=self.prompt,
                messages=messages,
                max_tokens=self.max_tokens,
                stream=True,
                custom_id=f"evx-{w.sha[:16]}-{attempt}",
            )
            if r.stop_reason == "max_tokens":
                return self._split(w)
            raw = parse_array(r.text)
            if raw is not None:
                break
            messages = [
                *messages,
                {"role": "assistant", "content": r.text or "(no answer)"},
                {"role": "user", "content": prompts.RETRY},
            ]
        if raw is None:
            raise ExtractError(
                f"the evidence read of {w.name} pp. {w.pages[0]}-{w.pages[-1]} did not answer with a JSON array"
            )
        for d in raw:
            if not str(d.get("file") or "").strip():
                d["file"] = w.name
        self._store(w, raw)
        return [Candidate.from_json(d, w.sha) for d in raw]

    def _split(self, w: Window) -> list[Candidate]:
        if len(w.pages) <= 1:
            where = f"p. {w.pages[0]}" if w.pages else "(no pages)"
            raise ExtractError(f"the evidence read of {w.name} {where} stopped at its output ceiling")
        mid = len(w.pages) // 2
        doc = self.corpus[w.name]
        self.log(f"  evidence: {w.name} pp. {w.pages[0]}-{w.pages[-1]} reached the output ceiling; read in halves")
        out: list[Candidate] = []
        for half in (w.pages[:mid], w.pages[mid:]):
            out += self.read(_window(w.name, doc, half, self.model, self.prompt))
        return out

    def run(self) -> list[Candidate]:
        ws = windows(self.corpus, self.model, prompt=self.prompt)
        fresh = sum(1 for w in ws if not (self.cache / f"{w.sha}.json").is_file())
        self.log(
            f"  evidence: {len(ws)} window(s) over {len(self.corpus)} document(s); {fresh} to read, {len(ws) - fresh} cached"
        )
        with ThreadPoolExecutor(max_workers=max(1, self.workers)) as ex:
            results = list(ex.map(self.read, ws))
        return [c for rs in results for c in rs]


def extract(
    data: Path,
    doorway: Any,
    model: str,
    corpus: Mapping[str, DocPages] | None = None,
    *,
    workers: int = 4,
    log: Callable[[str], None] = print,
    prompt: str = prompts.EXTRACT_PROMPT,
) -> list[Candidate]:
    """Every window of the corpus read into candidates (cached per window)."""
    corpus = corpus if corpus is not None else load_corpus(data)
    return Extractor(data, doorway, model, corpus, prompt=prompt, workers=workers, log=log).run()


def estimate(corpus: Mapping[str, DocPages], model: str, prompt: str = prompts.EXTRACT_PROMPT) -> dict[str, int]:
    """What a full-corpus read sends, before anything is paid: windows,
    pages, and input characters (window text plus the system prompt each
    call carries). The preflight prices it from these counts."""
    ws = windows(corpus, model, prompt=prompt)
    return {
        "windows": len(ws),
        "pages": sum(len(w.pages) for w in ws),
        "input_chars": sum(len(w.text) + len(prompt) for w in ws),
    }
