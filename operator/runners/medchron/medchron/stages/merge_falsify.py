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


# ---- THE ONE RULE for what code may collapse -------------------------------------
# Held here, in the falsifier, because the falsifier is the reader that must agree
# with it. `merge.merge_cluster` imports `yields_to` for its code merge, and
# `containment_collapses` below credits exactly what `yields_to` drops, so the
# floor the falsifier enforces and the collapse code performs cannot drift.
#
# 2026-09-15: they had drifted. The router sent a cluster to the model BECAUSE
# it held a same-citation reworded pair, the prompt licensed the model to
# collapse it, and this function credited only substring containment -- so a
# compliant model was refused for "losing" a paragraph, every retry could only
# repeat that, and a client's chronology failed at $62. Twenty-nine of fifty-five
# routed clusters on that matter were routed for that reason alone. The five
# inspected were the same boilerplate sentence with its commas moved.
NUMBER = re.compile(r"\d+(?:[./:\-]\d+)*")
WORD = re.compile(r"[a-z0-9]+")
#: The only words two sentences may differ by and still be ONE sentence with
#: its punctuation and connectives moved. Closed and deliberately small: a word
#: not listed here is a content word, and a content-word difference keeps both
#: sentences. Negations, laterality and qualifiers are content words on purpose
#: ("no", "not", "left", "right", "active", "passive" are never in this set).
FUNCTION_WORDS = frozenset(
    "a an the of and with in on at to for or by as is was were are be from that this these those it its".split()
)


def tokens(prose: str) -> set[str]:
    return set(WORD.findall(prose.lower()))


def jaccard(a: set, b: set) -> float:
    return 1.0 if not a and not b else len(a & b) / len(a | b)


def same_fact(a: str, b: str) -> bool:
    """Two same-citation sentences that are ONE sentence reworded: identical
    number set and identical CONTENT-WORD set; only function words may differ.

    This is the rule code can state truthfully. The first draft guarded on
    numbers alone (plus token Jaccard >= 0.8) and was measured against the 47
    same-citation pairs on a real matter before it shipped: only 3 had identical
    content words. The other 44 differed by "active" vs "passive" range of
    motion, "positive", "bilateral", "spinal" vs "spines" -- clinically distinct
    findings that a number-only guard would have deleted from a legal record,
    silently, 44 times. Laterality and negation are the same class: "left" vs
    "right", "no" vs a bare finding. A word not in FUNCTION_WORDS keeps both
    sentences; that is the safe direction, and it is what "fidelity over
    reconciliation" means. Exact repeats are not this function's business: the
    caller's set already absorbed them.
    """
    if a == b:
        return False
    if set(NUMBER.findall(a)) != set(NUMBER.findall(b)):
        return False
    return (tokens(a) ^ tokens(b)) <= FUNCTION_WORDS


def yields_to(t: str, o: str) -> bool:
    """`t` is dropped in favour of `o` (both normalised, same citation).

    Either `t` is a strict substring of `o`, or the two are `same_fact` and
    `t` is the shorter (ties broken by text, so a chain of three collapses to
    exactly one and both callers agree which one).
    """
    if t == o:
        return False
    if t in o:
        return True
    return same_fact(t, o) and (len(t), t) < (len(o), o)


def containment_collapses(pairs: set[tuple[str, str]]) -> int:
    """How many paragraphs the code merge is CREDITED for dropping.

    Pools by citation only, while `merge_cluster` collapses per heading. That
    asymmetry is deliberate and safe-direction: the floor here can only be at
    or below what the code merge actually emits, never above it, so a
    cross-heading pair the code keeps can never be reported as lost. It is
    named so nobody "fixes" it into a fourth private definition.
    """
    n = 0
    by_cite: dict[str, list[str]] = {}
    for text, cite in pairs:
        by_cite.setdefault(cite, []).append(text)
    for texts in by_cite.values():
        n += sum(1 for t in texts if any(yields_to(t, o) for o in texts))
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
