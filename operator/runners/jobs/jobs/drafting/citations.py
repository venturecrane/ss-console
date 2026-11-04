"""The firm's citation policy, enforced in code on the finished document.

WHY. The attorney's standing instruction for a mediation brief (A&P,
2026-11-04): cite only direct quotes from a deposition transcript or from case
law; never cite the demand or the medical records; mention the CHP report
without citing it; cite discovery responses only when quoting them directly. A
prompt can ask for that and a model can drift from it, so the rule is code:
every cite the document carries is classified by the KIND of the file it
names (read from the file itself, never from the model) and by whether it
sits on a verbatim quotation of that file, and a cite the policy does not
allow is dropped while the sentence stays.

``drafting-firm.yaml``::

    citations:
      mediation_brief:
        allow:  [deposition-quote, legal-authority, discovery-quote]
        forbid: [demand, medical, police-report, position-paper, offer, email]

A kind with ``-quote`` is allowed only on a verbatim quotation of the cited
file. Every kind not allowed is forbidden, listed or not (``other`` included):
a file the classifier cannot name is never citable by default. Legal authority
(cases, codes, CACI) is never a FILE cite, so the policy leaves it in place.

What a cite is: the two forms both drafting paths write, inside parentheses
and separated by ``;``: ``FILE: <name>, p. N`` and ``<Surname> Dep. 45:3-7``.
``apply`` rewrites the markdown before render (each forbidden part removed;
a parenthetical left empty removed whole) and says what it dropped;
``check`` reads the rendered text back and lists any forbidden cite still
there, which fails the job (``format_check``: ours, never a repair).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping

KEY = "citations"
#: The closed vocabulary of a cited file's kind.
KINDS = (
    "deposition",
    "discovery",
    "legal-authority",
    "demand",
    "medical",
    "police-report",
    "position-paper",
    "offer",
    "email",
    "other",
)
QUOTE = "-quote"
_ALLOWABLE = set(KINDS) | {k + QUOTE for k in KINDS}

_RECORD = re.compile(r"FILE:|\bDep\.\s*\d")
_FILE = re.compile(r"^FILE:\s*(?P<name>.+?),\s*pp?\.\s*\d")
_DEP = re.compile(r"^(?P<who>[A-Z][A-Za-z'.-]*(?:\s+[A-Z][A-Za-z'.-]*){0,2})\s+Dep\.\s*\d{1,4}:\d")
_QUOTED = re.compile(r"[\"“]([^\"“”]{8,}?)[\"”]\s*$")
_WORDS = re.compile(r"[a-z0-9]+")

#: In order; the first that matches names the kind. Discovery is read before
#: demand ("responses to demand for production" is discovery).
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"(?i)\.(eml|msg)$|^from:\s.*\n(?:.*\n){0,4}?(?:to|sent|subject):\s", re.M)),
    ("deposition", re.compile(r"(?i)\bdepo(sition)?\b|\breporter'?s transcript\b|\btranscript of\b")),
    (
        "discovery",
        re.compile(
            r"(?i)\binterrogator(y|ies)\b|\brequests?\s+for\s+(admission|production)\b|"
            r"\bresponses?\s+to\b.{0,60}\b(demand|request|interrogator)|\bdemand\s+for\s+(production|inspection)\b"
        ),
    ),
    ("police-report", re.compile(r"(?i)\bCHP\b|traffic collision report|\bpolice report\b|\bcollision report\b")),
    ("position-paper", re.compile(r"(?i)\bposition (paper|statement)\b|\bmediation brief\b")),
    ("offer", re.compile(r"(?i)\boffer\b|\bcounter[- ]?offer\b|\bsettlement proposal\b")),
    ("demand", re.compile(r"(?i)\bdemand\b|\bpolicy limits? (letter|demand)\b")),
    (
        "medical",
        re.compile(
            r"(?i)\bmedical\b|\brecords?\b|\bhospital\b|\bclinic\b|\bimaging\b|\bMRI\b|\bradiology\b|"
            r"\bphysical therapy\b|\bchiropractic\b|\bbill(s|ing)?\b|\bhealth\b|\bM\.?D\.?\b|\bdischarge\b|"
            r"\bemergency\b|\bortho\w*\b|\bpatient\b|\bdiagnos\w+\b"
        ),
    ),
)


def kind_of(name: str, text: str = "") -> str:
    """The file's kind, from its name first and then the head of its text."""
    head = (text or "")[:3000]
    for kind, pat in _RULES:
        if pat.search(name):
            return kind
    for kind, pat in _RULES:
        if pat.search(head):
            return kind
    return "other"


def validate(body: Any, classes: tuple[str, ...]) -> list[str]:
    if not isinstance(body, dict):
        return [f"{KEY}: expected a map of class to policy"]
    out: list[str] = []
    for cls, pol in body.items():
        where = f"{KEY}.{cls}"
        if cls not in classes:
            out.append(f"{where}: unknown class (expected {list(classes)})")
            continue
        if not isinstance(pol, dict) or set(pol) - {"allow", "forbid"} or "allow" not in pol:
            out.append(f"{where}: expected {{allow, forbid?}}")
            continue
        for key in ("allow", "forbid"):
            vals = pol.get(key, [])
            if not isinstance(vals, list) or any(v not in _ALLOWABLE for v in vals):
                out.append(f"{where}.{key}: expected kinds from {list(KINDS)} (a kind may end -quote)")
        base = {v.removesuffix(QUOTE) for v in pol.get("allow") or [] if isinstance(v, str)}
        both = base & set(pol.get("forbid") or [])
        if both:
            out.append(f"{where}: {sorted(both)} both allowed and forbidden")
    return out


@dataclass(frozen=True)
class Policy:
    allow: frozenset[str]  # a kind, or a kind + "-quote"

    def permits(self, kind: str, quoted: bool) -> bool:
        return kind in self.allow or (quoted and kind + QUOTE in self.allow)


def policy_for(firm_data: Mapping[str, Any], cls: str) -> Policy | None:
    pol = ((firm_data.get(KEY) or {}).get(cls)) or None
    return Policy(frozenset(pol.get("allow") or [])) if pol else None


def _norm(s: str) -> str:
    return " ".join(_WORDS.findall(s.lower()))


@dataclass
class Corpus:
    texts: Mapping[str, str]

    def __post_init__(self) -> None:
        self.kinds = {n: kind_of(n, t) for n, t in self.texts.items()}
        self._norm = {n: _norm(t) for n, t in self.texts.items()}

    def resolve(self, name: str) -> str | None:
        name = name.strip()
        if name in self.texts:
            return name
        hits = [n for n in self.texts if n.casefold() == name.casefold()]
        return hits[0] if len(hits) == 1 else None

    def deponent(self, who: str) -> list[str]:
        sur = who.split()[-1].casefold()
        deps = [n for n, k in self.kinds.items() if k == "deposition"]
        named = [n for n in deps if sur in n.casefold()]
        return named or deps

    def carries(self, names: list[str], quote: str) -> bool:
        q = _norm(quote)
        return bool(q) and any(q in self._norm[n] for n in names)


def _part(part: str, quote: str | None, corpus: Corpus) -> tuple[str, bool]:
    """(kind, on a verbatim quotation of that file) for one cite part."""
    m = _DEP.match(part)
    if m:
        files = corpus.deponent(m.group("who"))
        return "deposition", bool(quote) and corpus.carries(files, quote or "")
    m = _FILE.match(part)
    if m:
        name = corpus.resolve(m.group("name"))
        if name is None:
            return "other", False
        return corpus.kinds[name], bool(quote) and corpus.carries([name], quote or "")
    return "other", False


@dataclass(frozen=True)
class _Cite:
    start: int  # at the space before "(", when there is one
    end: int  # after ")"
    body: str


def _parens(text: str):
    """Every record-cite parenthetical, balanced: a file name may itself carry
    parentheses ("Exhibit 4 - Provider (Medical Records).pdf")."""
    i = 0
    while True:
        i = text.find("(", i)
        if i < 0:
            return
        depth, j = 0, i
        while j < len(text):
            depth += {"(": 1, ")": -1}.get(text[j], 0)
            if depth == 0 or text[j] == "\n":
                break
            j += 1
        body = text[i + 1 : j]
        if depth == 0 and _RECORD.search(body):
            start = i - 1 if i and text[i - 1] == " " else i
            yield _Cite(start, j + 1, body)
            i = j + 1
        else:
            i += 1


def _split(body: str) -> list[str]:
    """The cite's parts, on ``;`` outside any inner parentheses."""
    parts, depth, cur = [], 0, []
    for ch in body:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if ch == ";" and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def _scan(text: str, policy: Policy, corpus: Corpus):
    """Yield (cite, kept parts, dropped kinds) for every record-cite parenthetical."""
    for m in _parens(text):
        before = text[max(0, m.start - 1200) : m.start]
        q = _QUOTED.search(before.rstrip())
        quote = q.group(1) if q else None
        kept, dropped = [], []
        for part in _split(m.body):
            kind, quoted = _part(part, quote, corpus)
            if not (_FILE.match(part) or _DEP.match(part)):
                kept.append(part)  # not a record cite (a case, a code section): the policy leaves it
            elif policy.permits(kind, quoted):
                kept.append(part)
            else:
                dropped.append(kind + (" (unquoted)" if kind in ("deposition", "discovery") and not quoted else ""))
        yield m, kept, dropped


def apply(md: str, policy: Policy | None, texts: Mapping[str, str]) -> tuple[str, list[str]]:
    """The markdown with every cite the policy forbids removed, and a note per kind dropped."""
    if policy is None:
        return md, []
    corpus = Corpus(texts)
    out, last, counts = [], 0, Counter()
    for m, kept, dropped in _scan(md, policy, corpus):
        if not dropped:
            continue
        counts.update(dropped)
        out.append(md[last : m.start])
        if kept:
            out.append(" (" + "; ".join(kept) + ")")
        last = m.end
    out.append(md[last:])
    notes = [
        f"citation policy: {n} cite(s) to {kind} removed (the firm's standing instruction); the sentences stay"
        for kind, n in sorted(counts.items())
    ]
    return "".join(out), notes


def for_class(
    md: str, firm_data: Mapping[str, Any], cls: str, texts: Mapping[str, str]
) -> tuple[str, list[str], Policy | None]:
    """``apply`` under the class's authored policy: (markdown, notes, the policy for ``check``)."""
    policy = policy_for(firm_data, cls)
    md, notes = apply(md, policy, texts)
    return md, notes, policy


def check(text: str, policy: Policy | None, texts: Mapping[str, str]) -> list[str]:
    """Every forbidden cite still in the rendered text, as a finding."""
    if policy is None:
        return []
    corpus = Corpus(texts)
    return [
        f"citations: a cite the firm's policy forbids ({', '.join(dropped)}): ({m.body[:80]})"
        for m, _kept, dropped in _scan(text, policy, corpus)
        if dropped
    ]
