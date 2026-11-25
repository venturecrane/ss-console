"""The firm's rules read into clauses: one model call per job, values only,
every value verified by code against the rule's own words (design C, "What the
model returns").

Per rule the model returns clauses ``{quote, standing, standing_quote,
doc_types, act, keys, grounds, words, kind, when}``. Code keeps a clause only when:

* ``quote`` is a span of the rule (quotes, spacing and case folded);
* each book key it names has a short name (``Entry.names``) inside the quote,
  and each ground is named inside the quote;
* ``doc_types`` are of the closed set, ``act`` and ``when`` too;
* ``words``, when given, is a span of the rule: the firm's wording for the
  clause, stated or quoted, made a book entry of its own (``book.cased``);
  a quoted passage in the quote is taken as the words when none is named.

A clause from a part of the rule labelled for one response ("Response #9:",
"#3:") is one matter's, never standing, unless the model names a
``standing_quote`` code finds in that same part ("make this a rule"). Code
decides it; the model's ``standing`` alone never makes a one-matter clause
standing.

Every rule must come back with at least one clause. A refused answer is asked
once more, told what failed; failing again raises :class:`ReadRefused`. Answers
are cached per rule-text sha in ``rule-read.json`` in the job's data, so a
resumed or re-rendered job asks nothing again.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

from ...matter.store import write_atomic
from ..verify.items import parse_json
from .book import Book, Entry, entry_groups, fold, ground_groups, norm
from .types import ACTS, DOC_TYPES, Ask, Clause

STAGE = "rulebook-read"
MAX_TOKENS = 6_000
#: Rules per call. One call for every rule outgrew MAX_TOKENS once the firm had
#: enough of them (a cut-off answer parses as no object), so they are read a few
#: at a time; each batch is saved as it lands, so a retry resumes.
BATCH = 6
FILE = "rule-read.json"
WHENS = ("judged", "always")
WORD_KINDS = ("objection", "disposition", "statement")
#: A part of a rule labelled for one response: "Response #9:", "Response No. 9:", "#3:".
_LABEL = re.compile(r"(?:\bresponse\s*(?:no\.?\s*)?#?\s*|(?<![\w#])#\s*)(\d+(?:\.\d+)?)\s*:", re.I)
_QUOTED = re.compile(r"[\"“]([^\"“”]{12,600})[\"”]")
#: Rule document kinds (the store's ``doc_kinds``) and the clause document types they cover.
KIND_TYPES = {
    "discovery_response": ("rfp_response", "srog_response", "frog_response", "rfa_response", "index"),
    "discovery_set": ("rfp_set", "srog_set", "rfa_set"),
}


def rule_text(rule: dict[str, Any]) -> str:
    """Everything the rule says, as one text: its text and its recorded wording."""
    return " ".join(str(w) for w in [rule.get("text") or "", *(rule.get("wording") or [])] if str(w).strip())


def sha(rule: dict[str, Any]) -> str:
    return hashlib.sha256(fold(rule_text(rule)).encode("utf-8")).hexdigest()


def segments(text: str) -> list[tuple[int, int, str]]:
    """(start, end, response number or "") of each part of a folded rule text."""
    marks = [(m.start(), m.group(1)) for m in _LABEL.finditer(text)]
    out = [(0, marks[0][0] if marks else len(text), "")]
    for n, (at, num) in enumerate(marks):
        out.append((at, marks[n + 1][0] if n + 1 < len(marks) else len(text), num))
    return [s for s in out if s[1] > s[0]]


SYSTEM = (
    "You read a law firm's standing rules for its discovery responses and split each rule into clauses: one "
    'thing it asks a response to do. You never write wording. Return ONLY a JSON object {"rules": [{"rule": '
    '<rule id>, "clauses": [{"quote": <the rule\'s own words for this clause, copied exactly>, "standing": '
    'true | false, "standing_quote": <the rule\'s words that make it apply to every matter, copied exactly, or '
    '"">, "doc_types": [one or more of '
    + json.dumps(list(DOC_TYPES))
    + '], "act": one of '
    + json.dumps(list(ACTS))
    + ', "keys": [<phrase book keys whose words the clause calls for>], "grounds": '
    '[<the short name of each objection ground the clause calls for, as the rule says it>], "words": <when the '
    "rule itself gives the words to write (quoted, or 'state that ...'), those words copied exactly from the rule, "
    'else "">, "kind": "objection" | "disposition" | "statement" | "", "when": "judged" | '
    '"always"}]}]}. Every rule listed gets at least one clause; a clause code cannot make is act "other". '
    'A part of a rule said of one response ("Response #9: ...") is standing false unless the rule says it '
    'applies to every matter. "always" only when the clause applies to every response of its document types '
    "with no judgment of what the request asks."
)


def user(rules: list[dict[str, Any]], book: Book, problems: list[str] | None = None) -> str:
    lines = ["THE FIRM'S RULES:"]
    lines += [f"\n[rule {r['rule_id']}] {rule_text(r)}" for r in rules]
    lines.append("\nTHE PHRASE BOOK (key, kind, short names):")
    lines += [f"- {e.key} ({e.kind}): {', '.join(e.names) or '-'}" for e in book.entries.values()]
    if problems:
        lines.append("\nYOUR LAST ANSWER WAS REFUSED BY CODE FOR:\n" + "\n".join(f"- {p}" for p in problems))
    return "\n".join(lines) + "\n"


def _span(text: str, got: Any) -> str:
    """``got`` folded when it is a span of the folded ``text``, else ""."""
    want = fold(str(got or "")).strip(" \"'")
    return want if want and want in text else ""


def _doc_types(rule: dict[str, Any], raw: Any) -> tuple[tuple[str, ...], str]:
    got = tuple(raw) if isinstance(raw, list) and all(isinstance(t, str) for t in raw) else ()
    if not got or not set(got) <= set(DOC_TYPES):
        return (), f"doc_types is one or more of {list(DOC_TYPES)}"
    kinds = rule.get("doc_kinds") or []
    allowed = {t for k in kinds for t in KIND_TYPES.get(k, ())} if kinds else set(DOC_TYPES)
    return (got, "") if set(got) <= allowed else ((), "doc_types outside the rule's recorded document kinds")


def _named(quote: str, book: Book, keys: Any, grounds: Any) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    keys = keys if isinstance(keys, list) else []
    grounds = grounds if isinstance(grounds, list) else []
    for k in keys:
        e = book.entries.get(str(k))
        if e is None:
            return (), (), f"key {k!r} is not in the phrase book"
        # by a short name, or by a word of its ground's synonyms ("Privilege" names the attorney-client entries)
        if not any(fold(n) in quote for n in e.names) and not (ground_groups(quote) & entry_groups(e)):
            return (), (), f"key {k!r}: none of its short names {list(e.names)} is in the quote"
    for g in grounds:
        if not fold(str(g)) or fold(str(g)) not in quote:
            return (), (), f"ground {g!r} is not named in the quote"
    return tuple(str(k) for k in keys), tuple(fold(str(g)) for g in grounds), ""


def _standing(text: str, quote: str, raw: dict[str, Any]) -> tuple[bool, tuple[str, ...]]:
    """(standing, the response number of a one-matter part): code's decision."""
    at = text.find(quote)
    labelled = [s for s in segments(text) if s[2] and s[0] < at + len(quote) and at < s[1]]
    if not labelled:
        return raw.get("standing") is True, ()
    seg = labelled[0]
    sq = _span(text[seg[0] : seg[1]], raw.get("standing_quote"))
    return (raw.get("standing") is True and bool(sq)), tuple(s[2] for s in labelled)


def _as_written(rule: dict[str, Any], folded: str) -> str:
    """The rule's own text, in its own casing, of a folded span of it."""
    written = norm(rule_text(rule))
    at = written.casefold().find(folded)
    return written[at : at + len(folded)] if at >= 0 and len(written) == len(written.casefold()) else folded


def _words(rule: dict[str, Any], text: str, quote: str, raw: dict[str, Any]) -> tuple[str, str]:
    """(the rule's own words for the clause, in its casing, and their kind) or ("", "")."""
    kind = raw.get("kind") if raw.get("kind") in WORD_KINDS else ""
    words = _span(text, raw.get("words"))
    if not words:
        found = [fold(f) for f in _QUOTED.findall(text) if fold(f) in quote]
        words = found[0] if len(found) == 1 else ""
    if not words or len(words) < 12:
        return "", ""
    return _as_written(rule, words), kind or ("objection" if raw.get("act") == "add_objection" else "disposition")


def _clause(rule: dict[str, Any], n: int, raw: Any, book: Book) -> tuple[Clause | None, Entry | None, str]:
    if not isinstance(raw, dict):
        return None, None, "a clause is an object"
    text = fold(rule_text(rule))
    quote = _span(text, raw.get("quote"))
    if not quote:
        return None, None, "the quote is not copied exactly from the rule"
    if raw.get("act") not in ACTS or raw.get("when", "judged") not in WHENS:
        return None, None, f"act is one of {list(ACTS)} and when one of {list(WHENS)}"
    types, bad = _doc_types(rule, raw.get("doc_types"))
    keys, grounds, bad2 = _named(quote, book, raw.get("keys"), raw.get("grounds"))
    if bad or bad2:
        return None, None, bad or bad2
    standing, numbers = _standing(text, quote, raw)
    rid = str(rule["rule_id"])
    entry = None
    words, kind = _words(rule, text, quote, raw)
    if words and raw["act"] in ("add_objection", "use_phrase"):
        entry = Entry(f"{rid}#{n}", kind, book.cased(words), grounds, (), f"rule:{rid}")
        keys = (*keys, entry.key)
    clause = Clause(rid, n, quote, standing, types, raw["act"], keys, grounds, raw.get("when", "judged"), numbers)
    return clause, entry, ""


def check(raw: Any, rules: list[dict[str, Any]], book: Book) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """({rule id: {"clauses": [...], "entries": [...]}}, problems)."""
    got = raw.get("rules") if isinstance(raw, dict) else None
    if not isinstance(got, list):
        return {}, ['the answer is an object {"rules": [...]}']
    by_id = {str(r["rule_id"]): r for r in rules}
    out: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for row in got:
        rid = str(row.get("rule") or "") if isinstance(row, dict) else ""
        if rid not in by_id or rid in out:
            problems.append(f"rule {rid!r} is not one of the rules listed, or is listed twice")
            continue
        clauses = row.get("clauses")
        if not isinstance(clauses, list) or not clauses:
            problems.append(f"rule {rid}: at least one clause")
            continue
        rec: dict[str, Any] = {"clauses": [], "entries": []}
        for n, c in enumerate(clauses, 1):
            clause, entry, bad = _clause(by_id[rid], n, c, book)
            if bad:
                problems.append(f"rule {rid} clause {n}: {bad}")
                continue
            rec["clauses"].append(clause)
            rec["entries"] += [entry] if entry else []
        out[rid] = rec
    problems += [f"rule {rid}: missing from the answer" for rid in by_id if rid not in out]
    return out, problems


class ReadRefused(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems)[:480])
        self.problems = problems


def _cache(data: Path) -> dict[str, Any]:
    p = Path(data) / FILE
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


#: One lock per job data directory: units draft four at a time (``finish._draft_units``),
#: and the job-level rule read must be asked once and written once, not raced.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_LOCK = threading.Lock()


def lock(data: Path) -> threading.Lock:
    """The lock that guards ``data``'s job-level rulebook files."""
    key = str(Path(data).resolve())
    with _LOCKS_LOCK:
        return _LOCKS.setdefault(key, threading.Lock())


def save(path: Path, text: str) -> None:
    """``text`` at ``path``, atomically, with a temporary name of this thread's
    own: two units writing at once never share a temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, text)


def _save(data: Path, cache: dict[str, Any]) -> None:
    save(Path(data) / FILE, json.dumps(cache, indent=1, sort_keys=True))


def _ask(rules: list[dict[str, Any]], book: Book, ask: Ask) -> dict[str, dict[str, Any]]:
    problems: list[str] | None = None
    for _ in range(2):
        got, problems = check(parse_json(ask(STAGE, SYSTEM, user(rules, book, problems), MAX_TOKENS)), rules, book)
        if not problems:
            return got
    raise ReadRefused(problems or ["no answer"])


def read(data: Path, rules: list[dict[str, Any]], book: Book, ask: Ask | None) -> tuple[list[Clause], Book]:
    """(every rule's clauses, the book with the rules' own words added). Rules
    already read in this job come from ``rule-read.json``; the rest are asked
    about BATCH at a time. ``ReadRefused`` after the retry; no ``ask`` with a rule
    unread is a refusal too, never a silent skip. Held under :func:`lock`, so
    units drafting together ask once: the second finds the first's answer."""
    with lock(data):
        return _read(data, rules, book, ask)


def _read(data: Path, rules: list[dict[str, Any]], book: Book, ask: Ask | None) -> tuple[list[Clause], Book]:
    cache = _cache(data)
    todo = [r for r in rules if sha(r) not in cache]
    if todo:
        if ask is None:
            raise ReadRefused([f"rule {r['rule_id']} is unread and no model call is available" for r in todo])
        for at in range(0, len(todo), BATCH):
            batch = todo[at : at + BATCH]
            for rid, rec in _ask(batch, book, ask).items():
                rule = next(r for r in batch if str(r["rule_id"]) == rid)
                cache[sha(rule)] = {
                    "rule": rid,
                    "clauses": [c.to_dict() for c in rec["clauses"]],
                    "entries": [e.to_dict() for e in rec["entries"]],
                }
            _save(data, cache)
    clauses: list[Clause] = []
    entries: list[Entry] = []
    for r in rules:
        rec = cache[sha(r)]
        clauses += [Clause.from_dict({**c, "rule": str(r["rule_id"])}) for c in rec["clauses"]]
        entries += [Entry(**{**e, "names": tuple(e["names"]), "sets": tuple(e["sets"])}) for e in rec["entries"]]
    return clauses, book.with_entries(entries)


def plan_chars(rules: list[dict[str, Any]], book: Book, data: Path) -> int:
    """The read's calls, priced before any: nothing when every rule is cached."""
    todo = [r for r in rules if sha(r) not in _cache(data)]
    return 2 * (len(SYSTEM) + len(user(todo, book)) + 5 * 400 * len(todo)) if todo else 0
