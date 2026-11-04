#!/usr/bin/env python3
"""Score any drafted brief against its job's own record, offline.

    score_draft.py <job dir> --draft <draft.md|.txt|.docx> [--key key.json] [--json out.json]

Reads the job's page-marked extracted text (``extracted.jsonl`` rows; a
``text_path`` that does not resolve, as in a copied job dir, is found under the
job dir by file name) and the draft, and reports, with no network and no model:

* **pins**: each ``(FILE: name, p. N)`` resolves to a document and a page;
* **quotes**: each quotation of four words or more in a cited sentence is on
  the cited page (``ok``), a page off (``off_by_one``), elsewhere in the file
  (``wrong_pin``), in another document (``wrong_file``) or nowhere
  (``not_found``);
* **figures**: each dollar figure in a cited sentence is printed on a cited
  page (``ok``), a page off, or not on the cited pages (``not_on_page``);
* **lint**: what the evidence lane's lint would refuse in the draft, by type
  (``hand_quote``, ``hand_money`` ...): how much of a prose draft is hand-typed
  record (when ``evidence.lint`` is in the tree);
* **key**: with ``--key`` (JSON or JSONL), the scorer's fixed / caught /
  disclosed / missed / absent per row (``scorer.py``). A new-lane job (its
  ``evidence-spans.json`` and ``evidence/ledger.jsonl`` present) is scored
  from the render span map, the source check's ``check.json`` and ``--notes``;
  an old-lane draft by the key's ``wrong_pattern`` and its cites.

Deposition cites (``Surname Dep. 45:3``) are counted, not checked: they name
transcript pages, not file pages.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import runner_path
import scorer

QUOTE_WORDS = 4
_QUOTE = re.compile(r"[\"“]([^\"”]{8,3000}?)[\"”]")
_DEP = re.compile(r"\b[A-Z][A-Za-z'-]+\s+Dep\.\s*\d+:\d+")
_WORD = re.compile(r"[a-z0-9]+")


def norm(s: str) -> str:
    """Words only, bare numbers dropped (a transcript's line numbers)."""
    return " ".join(t for t in _WORD.findall(s.casefold()) if not t.isdecimal())


class Corpus:
    def __init__(self, docs: dict[str, dict[int, str]]) -> None:
        self.docs = docs
        self.by_stem: dict[str, str] = {}
        for n in docs:  # a shared name's copies answer to the bare name too (first copy)
            self.by_stem.setdefault(scorer.stem(n), n)
            self.by_stem.setdefault(scorer.stem(re.sub(r" \([^()]*\)(?: #\d+)?$", "", n)), n)
        self.normed = {n: {p: norm(t) for p, t in pages.items()} for n, pages in docs.items()}

    @classmethod
    def from_job(cls, job: Path) -> "Corpus":
        pt = runner_path.page_text()
        docs: dict[str, dict[int, str]] = {}
        rows_file = job / "extracted.jsonl"
        rows = [json.loads(ln) for ln in rows_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
        # One document per ROW, named as the lane names it (two rows sharing a
        # name are two documents, never one overwriting the other).
        for r, key in zip(rows, runner_path.row_names(rows)):
            p = _resolve(job, str(r.get("text_path") or ""))
            if p is None:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            segs = pt.parse_segments(text) if "[p." in text else {1: (text, False)}
            docs[key] = {n: t for n, (t, _m) in segs.items()}
        return cls(docs)

    def doc(self, file_stem: str) -> str | None:
        return self.by_stem.get(file_stem)

    def find(self, quote: str) -> list[tuple[str, int]]:
        q = norm(quote)
        return [(n, p) for n, pages in self.normed.items() for p, t in pages.items() if q and q in t]


def _resolve(job: Path, text_path: str) -> Path | None:
    """The row's text file: as stored, else under the job dir by name. One
    name under two directories (``text/`` and ``email/`` both hold a
    ``d1.txt``) is settled by the stored path's own directory, never dropped."""
    if text_path and Path(text_path).is_file():
        return Path(text_path)
    sp = Path(text_path)
    if not sp.name:
        return None
    hits = [p for p in job.rglob(sp.name) if p.is_file()]
    if len(hits) > 1:
        hits = [p for p in hits if p.parent.name == sp.parent.name] or hits
    return hits[0] if len(hits) == 1 else None


def read_draft(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        import docx  # python-docx, in the jobs venv

        doc = docx.Document(str(path))
        parts = [p.text for p in doc.paragraphs]
        # A brief's charges and damages live in tables: their cells are the brief's text too.
        for t in doc.tables:
            parts += [" | ".join(c.text for c in row.cells) for row in t.rows]
        return "\n\n".join(parts)
    return path.read_text(encoding="utf-8", errors="replace")


def _grade_quote(quote: str, doc: str | None, pages: list[int], corpus: Corpus) -> str:
    hits = corpus.find(quote)
    if not hits:
        return "not_found"
    mine = [p for n, p in hits if n == doc]
    if any(p in pages for p in mine):
        return "ok"
    if any(abs(p - q) == 1 for p in mine for q in pages):
        return "off_by_one"
    return "wrong_pin" if mine else "wrong_file"


def _grade_figure(value: Any, doc: str | None, pages: list[int], corpus: Corpus) -> str:
    if doc is None:
        return "unresolved"
    on = {p for p, t in corpus.docs[doc].items() if value in scorer.money_values(t)}
    if on & set(pages):
        return "ok"
    if any(abs(p - q) == 1 for p in on for q in pages):
        return "off_by_one"
    return "not_on_page"


def check(draft: str, corpus: Corpus) -> dict[str, Any]:
    body, _notes = scorer.split_notes(draft)
    pins: dict[str, int] = {"resolves": 0, "unknown_file": 0, "no_such_page": 0}
    quotes: dict[str, int] = {}
    figures: dict[str, int] = {}
    problems: list[dict[str, Any]] = []
    deps = 0
    for s in scorer.sentences(body):
        deps += len(_DEP.findall(s))
        cs = scorer.cites(s)
        if not cs:
            continue
        by: dict[str | None, list[int]] = {}
        cs = [(st, p) for st, p in cs if not st.startswith("dep ")]  # transcript pages: counted above
        if not cs:
            continue
        for st, p in cs:
            doc = corpus.doc(st)
            if doc is None:
                pins["unknown_file"] += 1
            elif p not in corpus.docs[doc]:
                pins["no_such_page"] += 1
            else:
                pins["resolves"] += 1
            by.setdefault(doc, []).append(p)
        doc, pages = max(by.items(), key=lambda kv: len(kv[1]))
        for q in _QUOTE.findall(s):
            if len(q.split()) < QUOTE_WORDS:
                continue
            g = _grade_quote(q, doc, pages, corpus)
            quotes[g] = quotes.get(g, 0) + 1
            if g != "ok":
                problems.append({"kind": "quote", "grade": g, "sentence": s[:300]})
        for v in scorer.money_values(s):
            g = _grade_figure(v, doc, pages, corpus)
            figures[g] = figures.get(g, 0) + 1
            if g != "ok":
                problems.append({"kind": "figure", "grade": g, "value": str(v), "sentence": s[:300]})
    return {
        "pins": pins,
        "quotes": quotes,
        "figures": figures,
        "deposition_cites_unchecked": deps,
        "problems": problems,
    }


def lint_counts(draft: str) -> dict[str, int] | None:
    lint = runner_path.lint()
    if lint is None:
        return None
    sh = runner_path.shapes()
    body, _notes = scorer.split_notes(draft)
    return lint.counts(lint.lint(body, sh.Ledger(())))


def spans_key(job: Path, rows: list[dict[str, Any]], notes: Path | None) -> dict[str, Any] | None:
    """A new-lane job's key score from its render span map, or None when the
    job has none (an old-lane draft)."""
    sp, lp = job / "evidence-spans.json", job / "evidence" / "ledger.jsonl"
    if not (sp.is_file() and lp.is_file()):
        return None
    sh = runner_path.shapes()
    spans = [sh.Span.from_json(x) for x in json.loads(sp.read_text(encoding="utf-8"))]
    ledger = sh.Ledger.from_jsonl(lp.read_text(encoding="utf-8"))
    cp = job / "check.json"
    chk = json.loads(cp.read_text(encoding="utf-8")) if cp.is_file() else None
    text = notes.read_text(encoding="utf-8") if notes is not None else ""
    if notes is not None and notes.suffix.lower() == ".docx":
        text = read_draft(notes)
    return scorer.score_spans(rows, spans, ledger, chk, text).to_json()


def report(job: Path, draft_path: Path, key: Path | None = None, notes: Path | None = None) -> dict[str, Any]:
    corpus = Corpus.from_job(job)
    draft = read_draft(draft_path)
    out: dict[str, Any] = {"documents": len(corpus.docs), **check(draft, corpus), "lint": lint_counts(draft)}
    if key is not None:
        rows = scorer.load_key(key.read_text(encoding="utf-8"))
        by_spans = spans_key(job, rows, notes)
        out["key_method"] = "spans" if by_spans is not None else "text"
        out["key"] = by_spans if by_spans is not None else scorer.score(rows, draft).to_json()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("job", type=Path)
    ap.add_argument("--draft", required=True, type=Path)
    ap.add_argument("--key", type=Path)
    ap.add_argument("--notes", type=Path, help="the attorney notes (.md or .docx), for a new-lane draft's key")
    ap.add_argument("--json", type=Path, help="write the full report here")
    a = ap.parse_args(argv)
    r = report(a.job, a.draft, a.key, a.notes)
    if a.json:
        a.json.write_text(json.dumps(r, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"documents {r['documents']}  pins {r['pins']}")
    print(f"quotes {r['quotes']}  figures {r['figures']}  deposition cites unchecked {r['deposition_cites_unchecked']}")
    print(f"lint {r['lint'] if r['lint'] is not None else '(evidence.lint not in this tree)'}")
    if "key" in r:
        print(f"key {r['key']['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
