"""Falsify a merge: prove the merged entries carry everything the clusters did.

The merge (model or code) is union-only by contract: every citation survives,
every paragraph survives unless it is a same-file duplicate, every cluster
becomes exactly one entry. The only check was once a WARNING that counted
date lines, and a merge that lost 34 clusters printed a line nobody read and
the document shipped without them. This is the hard version, arithmetic over
the two texts, so no step can satisfy it by claiming success.

Three checks, three exit codes, all reported before exiting:
  3  a citation present in the clusters is absent from the merged output
  4  distinct (normalized text, citation) paragraphs out < in minus the
     same-citation containment collapses the contract allows
  5  merged entries != clusters in

The heading menu comes from the firm config (`format.subsections`), the same
list the map prompt is filled from, so the falsifier and the prompt agree on
what a heading line is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

CLUSTER_HEAD = re.compile(r"^##### CLUSTER (\d{4}-\d{2}-\d{2}) \| (.*?) \((\d+) fragments?\)\s*$")
CLUSTER_SPLIT = re.compile(r"(?m)^(?=##### CLUSTER )")
ENTRY_SPLIT = re.compile(r"(?m)^(?=\d{2}/\d{2}/\d{4}\s*(?:\(|$))")
DATE_LINE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})\s*(\(.*\))?\s*$")
# Every citation shape assemble can leave behind: exhibit with pages, exhibit
# without pages, and an unresolved (FILE: ...) no exhibit number claimed.
CITE = re.compile(r"\((?:Exhibit \d+(?: - p\. [0-9,\s\-]+)?|FILE:[^()]*)\)")
CONTINUE = re.compile(r"(?m)^\[entry may continue in next chunk\]\s*$")
CITE_END = re.compile(CITE.pattern + r"\.?\s*$")


@dataclass(frozen=True)
class Headings:
    canon: tuple[str, ...]

    @classmethod
    def from_config(cls, cfg: Any) -> "Headings":
        return cls(tuple(str(h) for h in (cfg.get("format", "subsections") or [])))

    def index(self, h: str) -> int:
        return self.canon.index(h)

    def canon_heading(self, line: str) -> str | None:
        s = re.sub(r"\s+", " ", line.strip().rstrip(":")).lower().replace(" and ", " & ")
        for h in self.canon:
            if re.sub(r"\s+", " ", h.lower()) == s:
                return h
        return None

    def is_structural(self, line: str) -> bool:
        if DATE_LINE.match(line) or self.canon_heading(line):
            return True
        return "|" in line and not CITE.search(line) and self.canon_heading(line.rpartition("|")[2]) is not None


def norm_cite(c: str) -> str:
    return re.sub(r"\s+", " ", c.strip())


def norm_text(t: str) -> str:
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", t).strip().lower().rstrip(" .")


def split_cited(joined: str) -> tuple[str, str]:
    cites = [norm_cite(c) for c in CITE.findall(joined)]
    prose = CITE.sub("", joined)
    prose = re.sub(r"\s+([.,;:])", r"\1", prose)
    prose = re.sub(r"\s+", " ", prose).strip()
    return prose, " ".join(cites)


def paragraphs(block: str, hd: Headings) -> list[tuple[str, str]]:
    """(prose, citation) pairs: a paragraph is a run of non-blank lines ending
    with a citation; uncited prose that never meets one comes back with
    citation "" so a caller can count it as a parse failure."""
    out: list[tuple[str, str]] = []
    buf: list[str] = []
    for raw in block.splitlines():
        line = raw.strip()
        if not line:
            if buf:
                out.append((" ".join(buf), ""))
                buf = []
            continue
        if not buf and hd.is_structural(line):
            continue
        buf.append(line)
        if CITE_END.search(line):
            out.append(split_cited(" ".join(buf)))
            buf = []
    if buf:
        out.append((" ".join(buf), ""))
    return out


def parse_clusters(text: str) -> list[dict[str, Any]]:
    clusters = []
    for block in CLUSTER_SPLIT.split(text.strip()):
        if not block.strip():
            continue
        head, _, body = block.partition("\n")
        m = CLUSTER_HEAD.match(head.strip())
        if not m:
            clusters.append(
                {"date": None, "key": None, "n": 0, "head": head.strip(), "body": body, "fragments": [body.strip()]}
            )
            continue
        body = CONTINUE.sub("", body)
        frags = [f.strip() for f in body.split("---FRAGMENT-BREAK---")]
        clusters.append(
            {
                "date": m.group(1),
                "key": m.group(2),
                "n": int(m.group(3)),
                "head": head.strip(),
                "body": body,
                "fragments": [f for f in frags if f],
            }
        )
    return clusters


def parse_entries(text: str) -> list[dict[str, str]]:
    entries = []
    for e in ENTRY_SPLIT.split(text.strip()):
        e = e.strip()
        if not e or not DATE_LINE.match(e.splitlines()[0]):
            continue
        lines = e.splitlines()
        entries.append(
            {"date_line": lines[0].strip(), "provider_line": lines[1].strip() if len(lines) > 1 else "", "body": e}
        )
    return entries


def distinct_paragraphs(block: str, hd: Headings) -> set[tuple[str, str]]:
    return {(norm_text(p), c) for p, c in paragraphs(block, hd) if c}


def containment_collapses(pairs: set[tuple[str, str]]) -> int:
    n = 0
    by_cite: dict[str, list[str]] = {}
    for text, cite in pairs:
        by_cite.setdefault(cite, []).append(text)
    for texts in by_cite.values():
        n += sum(1 for t in texts if any(o != t and t in o for o in texts))
    return n


def check(clusters_text: str, merged_text: str, hd: Headings) -> tuple[int, list[str]]:
    """(rc, report). rc 0 = the merge is not falsified."""
    clusters = parse_clusters(clusters_text)
    entries = parse_entries(merged_text)
    rep: list[str] = []
    rc = 0
    cites_in = {norm_cite(x) for c in clusters for x in CITE.findall(c["body"])}
    cites_out = {norm_cite(x) for e in entries for x in CITE.findall(e["body"])}
    lost = sorted(cites_in - cites_out)
    rep.append(f"citations: {len(cites_in)} in, {len(cites_out)} out, {len(lost)} lost")
    rep.extend(f"   LOST {x}" for x in lost[:20])
    if lost:
        rc = rc or 3
    n_in = n_collapse = 0
    for c in clusters:
        pairs = distinct_paragraphs(c["body"], hd)
        n_in += len(pairs)
        n_collapse += containment_collapses(pairs)
    n_out = sum(len(distinct_paragraphs(e["body"], hd)) for e in entries)
    floor = n_in - n_collapse
    rep.append(
        f"paragraphs: {n_in} distinct in, {n_collapse} same-cite containment collapse(s) allowed, "
        f"floor {floor}, {n_out} out"
    )
    if n_out < floor:
        rep.append(f"   LOST {floor - n_out} paragraph(s)")
        rc = rc or 4
    rep.append(f"entries: {len(clusters)} clusters in, {len(entries)} out")
    if len(entries) != len(clusters):
        rc = rc or 5
    rep.append("merge falsifier: " + ("PASS" if rc == 0 else f"FAIL (exit {rc})"))
    return rc, rep
