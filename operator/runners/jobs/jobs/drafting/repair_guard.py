"""A repair is kept only where code finds no new fact in it.

A repair round sends the model the sections the audit flagged and merges back
what it returns (``compose.Drafter.repair``). On 2026-11-05 one such repair
rewrote a flagged request on a premise no source and no earlier text holds,
and the rounds after it looped and spent until the set failed. Here every
repair is read by code before it is kept (``newfacts``): per request when a
requests section kept its requests, per section otherwise. A request or a
section whose repair states a fact the job's sources and the text being
repaired do not hold is put back exactly as it was, and FROZEN:

* it is never sent to be repaired again (no retry): a later repair that
  touches it is put back too;
* the findings on it are PARKED: they stop counting toward the set's
  convergence (``converge.served``) and are carried, with the request's own
  text, to the attorney notes as open items, or hold the job for SMD when
  their kind is one a served set must not carry (``repair_budget.decide``).

Everything else the repair did is kept. The record is ``repair-guard.json``
in the unit's data directory, written BEFORE the vetted draft, so a resume
finds the same frozen set; the rejected facts stay there, on the seat, and
never reach an alert.

``python -m jobs.drafting.repair_guard <job data dir>`` replays the check over
a finished or preserved job's own rounds, at no cost: no model is called.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..demand import draft as d
from . import compose, newfacts

if TYPE_CHECKING:
    from .compose import Drafter

Key = tuple[str, int | None]
FindingId = tuple[str, int | None, str]
STATE = "repair-guard.json"
NOTES = "=== ATTORNEY NOTES ==="
_MECH = re.compile(r"## MECHANICAL:.*?(?=\n## |\Z)", re.S)


def _flat(s: str) -> str:
    return " ".join(s.split())


def rows(audit_md: str) -> list[tuple[FindingId, str]]:
    """Every blocking finding row of an audit, with its identity
    (``converge.finding_ids`` reads the same identities)."""
    out: list[tuple[FindingId, str]] = []
    for head, body in d.audit_sections(audit_md).items():
        for ln in body.splitlines():
            m = compose.FINDING.search(ln)
            if m:
                out.append(((head, compose.row_item(ln), m.group(1).upper()), ln))
    mech = _MECH.search(audit_md)
    for ln in (mech.group(0) if mech else "").splitlines():
        if ln.startswith("- "):
            out.append((("MECHANICAL", None, " ".join(ln[2:].split())), ln))
    return out


def kind_of(fid: FindingId) -> str:
    return "MECHANICAL" if fid[0] == "MECHANICAL" else fid[2]


def unit_text(doc: str, key: Key) -> str:
    """The request's (or the section's) own text in ``doc``, or ""."""
    head, n = key
    for h, body in d.sections(doc.partition(NOTES)[0]):
        if h != head:
            continue
        if n is None:
            return body
        return compose.split_items(body)[1].get(n, "")
    return ""


def pieces(doc: str) -> list[str]:
    """``doc`` as the pieces a repair changes: each request of a requests
    section (and the text before its first), each other section whole."""
    out: list[str] = []
    for _h, body in d.sections(doc):
        pre, items = compose.split_items(body)
        out += [pre, *items.values()] if items else [body]
    return out


@dataclass
class Rejection:
    key: Key
    text: str  # the request or section as it was, and as it stays
    facts: list[tuple[str, str]]  # (kind, as printed): kept on the seat, never in an alert


def vet(before: str, after: str, base: newfacts.Known | newfacts.Both, frozen: set[Key]) -> tuple[str, list[Rejection]]:
    """``after`` with every request or section whose change adds a fact put
    back as it was in ``before``, and every change to a frozen one undone."""
    bdoc, after_doc = before.partition(NOTES)[0], after.partition(NOTES)
    adoc, sep, tail = after_doc
    known = newfacts.Both(base, newfacts.Doc(pieces(bdoc), bdoc))
    old = dict(reversed(d.sections(bdoc)))  # the first section of a heading wins
    out: list[str] = []
    rejected: list[Rejection] = []
    changed = False
    for head, body in d.sections(adoc):
        prev = old.get(head)
        if prev is not None and _flat(prev) == _flat(body):
            out.append(body)
            continue
        if prev is None:
            new = newfacts.new_facts(body, known)
            if new:
                rejected.append(Rejection((head, None), "", new))
                changed = True
                continue  # a section the repair invented, dropped
            out.append(body)
            continue
        pre, items = compose.split_items(body)
        ppre, pitems = compose.split_items(prev)
        whole = (head, None) in frozen or any(k[0] == head and k[1] not in items for k in frozen)
        if items and pitems and _flat(pre) == _flat(ppre) and not whole:
            for n, text in items.items():
                was = pitems.get(n)
                if was is not None and _flat(was) == _flat(text):
                    continue
                if (head, n) in frozen:
                    body, changed = body.replace(text, was or "", 1), True
                    continue
                new = newfacts.new_facts(text, known)
                if new:
                    rejected.append(Rejection((head, n), was or "", new))
                    body, changed = body.replace(text, was or "", 1), True
            out.append(body)
            continue
        if whole or any(k[0] == head for k in frozen):
            out.append(prev)
            changed = True
            continue
        new = newfacts.new_facts(body, known)
        if new:
            rejected.append(Rejection((head, None), prev, new))
            out.append(prev)
            changed = True
            continue
        out.append(body)
    if not changed:
        return after, []
    doc = "\n".join(s.rstrip() + "\n" for s in out)
    return doc + (sep + tail if sep else ""), rejected


class Guard:
    """One unit's frozen requests and the findings parked on them."""

    def __init__(self, data: Path, sources: str | newfacts.Known) -> None:
        """``sources``: the job's index, or the text to index (the digest and
        the record text, read once per job and cached: ``newfacts.index``)."""
        if isinstance(sources, str):
            text = sources
            sources = newfacts.index(lambda: (text,), job_root(data))
        self.data, self.sources = data, sources
        p = data / STATE
        st = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        self.rejected: list[dict[str, Any]] = list(st.get("rejected") or [])

    @property
    def frozen(self) -> set[Key]:
        return {(str(r["key"][0]), r["key"][1]) for r in self.rejected}

    def _save(self) -> None:
        tmp = self.data / f".{STATE}.tmp"
        tmp.write_text(json.dumps({"rejected": self.rejected}, indent=1), encoding="utf-8")
        tmp.replace(self.data / STATE)

    def check(self, before: str, after: str, audit_md: str, where: str) -> tuple[str, list[Rejection]]:
        """The repair ``after`` of ``before``, vetted; rejections recorded
        (with the findings ``audit_md`` had on each) BEFORE the caller commits."""
        vetted, rejected = vet(before, after, self.sources, self.frozen)
        if rejected:
            found = rows(audit_md)
            for r in rejected:
                on = [{"id": list(f), "line": ln} for f, ln in found if self._on(f, [r.key], {r.key: r.text})]
                self.rejected.append(
                    {
                        "key": list(r.key),
                        "text": r.text,
                        "facts": [list(f) for f in r.facts],
                        "findings": on,
                        "at": where,
                    }
                )
            self._save()
        return vetted, rejected

    def _on(self, fid: FindingId, keys: list[Key], texts: dict[Key, str]) -> bool:
        head, n, kind = fid
        if head == "MECHANICAL":  # a quotation: on the request that carries it
            q = _flat(kind)[:60]
            return any(q and q in _flat(texts.get(k, "")) for k in keys)
        return any(k[0] == head and (k[1] is None or k[1] == n) for k in keys)

    def parked(self, fid: FindingId) -> bool:
        return self._on(fid, list(self.frozen), {(str(r["key"][0]), r["key"][1]): r["text"] for r in self.rejected})

    def open_rows(self, audit_md: str) -> list[tuple[FindingId, str]]:
        return [(f, ln) for f, ln in rows(audit_md) if not self.parked(f)]

    def open_ids(self, audit_md: str) -> set[FindingId]:
        return {f for f, _ in self.open_rows(audit_md)}

    def parked_rows(self, audit_md: str) -> list[tuple[FindingId, str]]:
        """This audit's rows on frozen requests, and the rows recorded at a
        rejection that this audit no longer carries (the finding stands: the
        request was put back as it was)."""
        now = [(f, ln) for f, ln in rows(audit_md) if self.parked(f)]
        seen = {f for f, _ in now}
        for r in self.rejected:
            for x in r.get("findings") or []:
                f = (str(x["id"][0]), x["id"][1], str(x["id"][2]))
                if f not in seen:
                    seen.add(f)
                    now.append((f, str(x["line"])))
        return now


def repaired(dr: Drafter, digest: str, version: int, guard: Guard) -> tuple[str, list[Rejection]]:
    """``dr.repair`` of ``draft-v<version>``, kept only where it adds no fact:
    the vetted ``draft-v<version + 1>.md`` and what was put back."""
    before = dr._read(f"draft-v{version}.md")
    after = dr.repair(digest, version)
    vetted, rejected = guard.check(before, after, dr._read(f"audit-v{version}.md"), f"v{version}")
    if vetted != after:
        dr._commit(f"draft-v{version + 1}.md", vetted)
    return vetted, rejected


def job_root(data: Path) -> Path:
    """A unit's job data directory (``data/set-<k>`` belongs to ``data``)."""
    return data.parent if data.name.startswith("set-") else data


def vet_format(data: Path, before: str, after: str, sources: str | newfacts.Known) -> str:
    """The format check's one repair, kept only where it adds no fact."""
    vetted, _ = Guard(data, sources).check(before, after, "", "format")
    return vetted


# ---- the $0 replay over a job's own rounds ---------------------------------------------------
def _record_files(job_data: Path) -> tuple[list[Path], int]:
    """The record text files the job read, and how many it listed. A copied
    job's ``text_path`` names the seat's path: the copy is listed ONCE, and a
    path not here is found by its file name in that listing."""
    listed = job_data / "extracted.jsonl"
    paths = []
    with listed.open(encoding="utf-8") if listed.is_file() else open(os.devnull, encoding="utf-8") as fh:
        for ln in fh:
            if ln.strip():
                tp = str(json.loads(ln).get("text_path") or "")
                if tp:
                    paths.append(Path(tp))
    by_name: dict[str, Path] = {}
    if any(not p.is_file() for p in paths):
        for root, _dirs, names in os.walk(job_data.parent):
            for n in names:
                by_name.setdefault(n, Path(root) / n)
    found = [p if p.is_file() else by_name.get(p.name) for p in paths]
    return [p for p in found if p is not None and p.is_file()], len(paths)


def replay(job_data: Path, show: bool = False) -> list[str]:
    """Each repair round of each unit of a job, as this guard would have
    judged it: the request or section and the new facts by kind; their words
    only with ``show``. Reads files one at a time; calls no model."""
    digest = (job_data / "digest.md").read_text(encoding="utf-8") if (job_data / "digest.md").is_file() else ""
    files, listed = _record_files(job_data)

    def chunks():
        yield digest
        for f in files:
            yield f.read_text(encoding="utf-8", errors="replace")

    base = newfacts.index(chunks, job_data)
    size = sum(f.stat().st_size for f in files)
    out = [f"sources: digest {len(digest):,} chars; record text {len(files)} of {listed} files, {size:,} bytes"]
    units = sorted(p for p in job_data.glob("set-*") if p.is_dir()) or [job_data]
    for u in units:
        frozen: set[Key] = set()
        v = 1
        while (u / f"draft-v{v + 1}.md").is_file():
            before = (u / f"draft-v{v}.md").read_text(encoding="utf-8")
            after = (u / f"draft-v{v + 1}.md").read_text(encoding="utf-8")
            _vetted, rejected = vet(before, after, base, frozen)
            frozen |= {r.key for r in rejected}
            line = f"{u.name} repair v{v}->v{v + 1}: kept"
            if rejected:
                kinds: dict[str, int] = {}
                for r in rejected:
                    for k, _raw in r.facts:
                        kinds[k] = kinds.get(k, 0) + 1
                said = ", ".join(r.key[0] + ("" if r.key[1] is None else f" No. {r.key[1]}") for r in rejected)
                line = f"{u.name} repair v{v}->v{v + 1}: REFUSED {len(rejected)} ({said}); new facts by kind {kinds}"
                if show:
                    line += "; " + "; ".join(f"{k}: {raw}" for r in rejected for k, raw in r.facts)
            out.append(line)
            v += 1
    return out


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    show = "--show" in args
    rest = [a for a in args if a != "--show"]
    if len(rest) != 1 or not Path(rest[0]).is_dir():
        print("usage: python -m jobs.drafting.repair_guard <job data dir> [--show]", file=sys.stderr)
        return 2
    for line in replay(Path(rest[0]), show=show):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
