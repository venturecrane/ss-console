"""The firm's standing rules made in its filed discovery responses: the model
names values, code writes the words.

A rule in force (``wording_rules_store.applicable`` for ``discovery_response``,
frozen in the job by ``firm_wording``) becomes a checklist item whose source is
the rule's id, not a quote from the request (``checklist.rule_items``). Its
PHRASES are the firm's exact words for it: the rule's ``phrases`` when the
store carries them, else every quoted passage in its wording ("respond with
'After a diligent search ...'"). A rule with no phrase is never applied: code
has no words of the firm's to write, and the note says so.

Per document, one model call reads the responses and returns VALUES only::

    {"values": [{"request": "4", "rule": "<rule id>", "action": "replace" | "start" | "end" | "conflict",
                 "phrase": <1-based number of the rule's phrase>,
                 "sentence": "<replace only: the response's own sentence, copied exactly>",
                 "exhibits": ["<a phrase ending at 'Exhibit' only: each exhibit's title as the index lists it>"],
                 "why": "<conflict only: what in the response the rule would contradict>"}]}

Code keeps a value only when its request is one of the document's responses,
its rule is in force, its phrase exists, and a replaced sentence is in that
response's own paragraphs exactly once (:func:`check`). A refused answer is
asked for once more, told what failed; failing again, nothing is filed. A
``conflict`` is never applied: the response as staff wrote it stands, and the
job's notes name it.

EXHIBIT LETTERS ARE CODE'S (2026-11-23). The firm's production phrase ends
"which can be found at Exhibit", and it was written with no letter at all. The
model now names the exhibit by its TITLE as the document's own index lists it,
never by letter (a lone letter is refused), and code fills the letter from that
index when the response is made (``responses.fill``); a title the index does not
hold exactly once leaves that response as filed, named in the notes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from ..verify.items import parse_json
from . import responses as rs

Ask = Callable[[str, str, str, int], str]
STAGE = "revise-values"
MAX_TOKENS = 8_000
#: A model's output is priced at about five times its input (``core.OUTPUT_WEIGHT``).
OUTPUT_WEIGHT = 5
#: An answer's size per response it could name, for the projection.
ANSWER_CHARS_PER_RESPONSE = 240
_QUOTED = re.compile(r"[\"“]([^\"“”]{12,600})[\"”]")


def phrases(rule: dict[str, Any]) -> list[str]:
    """The firm's exact words for a rule, in its order; [] when it carries none."""
    own = rule.get("phrases")
    if isinstance(own, list) and all(isinstance(p, str) and p.strip() for p in own) and own:
        return [" ".join(p.split()) for p in own]
    found = _QUOTED.findall(" ".join(str(w) for w in rule.get("wording") or []))
    return list(dict.fromkeys(" ".join(p.split()) for p in found))


@dataclass(frozen=True)
class Conflict:
    request: str
    rule: str
    why: str


SYSTEM = (
    "You read discovery responses a law firm has already filed, and the firm's standing rules for them, and say "
    "where each rule applies. You never write wording: code writes the firm's exact phrase. Return ONLY a JSON "
    'object {"values": [...]}, one entry per response a rule applies to: {"request": <the response number as '
    'headed>, "rule": <the rule id>, "action": "replace" | "start" | "end" | "conflict", "phrase": <the number of '
    "the rule's phrase that fits this response>, \"sentence\": <for replace: the response's own sentence the phrase "
    'takes the place of, copied character for character>, "why": <for conflict: one sentence>}. "start" puts the '
    'phrase before the response\'s first paragraph (an objection), "end" after its last. Use "conflict" when the '
    "phrase would contradict what the response says (for example, it says documents will be produced and the "
    "phrase says none exist): code then leaves the response as written. Leave out a response no rule touches and "
    "one that already says what the rule says. Never name a request that is not in the document. When the phrase "
    'you choose ends at "Exhibit", add "exhibits": [<the title of each exhibit the response\'s documents are in, '
    "copied from the document's index of exhibits>]; never a letter, code fills the letter from the index."
)


def user(rules: list[dict[str, Any]], doc: rs.Document, problems: list[str] | None = None) -> str:
    lines = ["THE FIRM'S STANDING RULES:"]
    for r in rules:
        lines.append(f"\n[rule {r['rule_id']}] " + " ".join(str(w).strip() for w in r["wording"]))
        lines += [f"  phrase {n}: {p}" for n, p in enumerate(phrases(r), 1)]
    titles = doc.exhibit_titles()
    lines.append("\nTHE DOCUMENT'S INDEX OF EXHIBITS (titles):")
    lines += [f"- {t}" for t in titles] or ["(the document has no index of exhibits)"]
    lines.append("\nTHE DOCUMENT'S RESPONSES:")
    for resp in doc.responses:
        lines.append(f"\n=== REQUEST {resp.number} ===\n{doc.request_of(resp)}")
        lines.append(f"--- RESPONSE {resp.number} ---\n{doc.text_of(resp)}")
    if problems:
        lines.append("\nYOUR LAST ANSWER WAS REFUSED BY CODE FOR:\n" + "\n".join(f"- {p}" for p in problems))
    return "\n".join(lines) + "\n"


def check(raw: Any, rules: list[dict[str, Any]], doc: rs.Document) -> tuple[list[rs.Value], list[Conflict], list[str]]:
    """(values, conflicts, problems): every problem named, nothing kept on a guess."""
    got = raw.get("values") if isinstance(raw, dict) else None
    if not isinstance(got, list):
        return [], [], ['the answer is an object {"values": [...]}']
    by_rule = {str(r["rule_id"]): phrases(r) for r in rules}
    values: list[rs.Value] = []
    conflicts: list[Conflict] = []
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for n, v in enumerate(got, 1):
        if not isinstance(v, dict):
            problems.append(f"value {n} is an object")
            continue
        req, rid, act = str(v.get("request") or "").strip(), str(v.get("rule") or "").strip(), v.get("action")
        where = f"value {n} (request {req}, rule {rid})"
        if req not in doc.by_number():
            problems.append(f"{where}: request {req!r} is not one of this document's responses")
            continue
        if rid not in by_rule:
            problems.append(f"{where}: rule {rid!r} is not one of the rules listed")
            continue
        if (req, rid) in seen:
            problems.append(f"{where}: one entry per request and rule")
            continue
        seen.add((req, rid))
        if act == "conflict":
            conflicts.append(Conflict(req, rid, " ".join(str(v.get("why") or "").split())[:300]))
            continue
        if act not in rs.ACTIONS:
            problems.append(f"{where}: action is one of {[*rs.ACTIONS, 'conflict']}")
            continue
        k = v.get("phrase")
        if not (isinstance(k, int) and not isinstance(k, bool) and 1 <= k <= len(by_rule[rid])):
            problems.append(f"{where}: phrase is a number from 1 to {len(by_rule[rid])}")
            continue
        sentence = str(v.get("sentence") or "") if act == "replace" else ""
        exhibits, bad = _exhibits(v.get("exhibits"), by_rule[rid][k - 1])
        if bad:
            problems.append(f"{where}: {bad}")
            continue
        value = rs.Value(req, rid, str(act), by_rule[rid][k - 1], sentence, exhibits)
        if act == "replace" and len(rs.holds(doc, value)) != 1:
            problems.append(f"{where}: the sentence is not copied exactly from that response's own paragraphs")
            continue
        values.append(value)
    return values, conflicts, problems


_LETTER = re.compile(r"^(?:exhibits?\s+)?[A-Z]$", re.I)


def _exhibits(raw: Any, phrase: str) -> tuple[tuple[str, ...], str]:
    """(the exhibit titles a value names, "") or ((), what is wrong). Only a
    phrase that ends at "Exhibit" takes titles; a letter is never accepted. No
    titles (a document with no index code can read) is not refused here: the
    response is then left as filed and named (``responses.fill``), so one
    unreadable index never fails every other document's revision."""
    if not rs.has_slot(phrase) or raw is None or raw == []:
        return (), ""
    if not (isinstance(raw, list) and all(isinstance(t, str) and t.strip() for t in raw)):
        return (), 'the phrase ends at "Exhibit": "exhibits" is a list of the exhibits\' titles from the index'
    if any(_LETTER.match(t.strip()) for t in raw):
        return (), "name each exhibit by its title as the index lists it, never by its letter"
    return tuple(" ".join(t.split()) for t in raw), ""


def ask_values(rules: list[dict[str, Any]], doc: rs.Document, ask: Ask) -> tuple[list[rs.Value], list[Conflict]]:
    """The values for one document; ``ValuesRefused`` after the one retry."""
    problems: list[str] | None = None
    for _ in range(2):
        raw = parse_json(ask(STAGE, SYSTEM, user(rules, doc, problems), MAX_TOKENS))
        values, conflicts, problems = check(raw, rules, doc)
        if not problems:
            return values, conflicts
    raise ValuesRefused(problems or ["no answer"])


class ValuesRefused(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems)[:480])
        self.problems = problems


def plan_chars(rules: list[dict[str, Any]], doc: rs.Document) -> int:
    """One document's calls, priced before any: its read and the one retry."""
    asked = len(SYSTEM) + len(user(rules, doc))
    answer = OUTPUT_WEIGHT * ANSWER_CHARS_PER_RESPONSE * len(doc.responses)
    return 2 * (asked + answer)


def writable(rules: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(the rules code can write, the rules with no phrase of the firm's)."""
    return [r for r in rules if phrases(r)], [r for r in rules if not phrases(r)]


def to_json(values: list[rs.Value], conflicts: list[Conflict]) -> str:
    return json.dumps({"values": [v.to_dict() for v in values], "conflicts": [vars(c) for c in conflicts]}, indent=1)


def from_json(text: str) -> tuple[list[rs.Value], list[Conflict]]:
    doc = json.loads(text)
    return [rs.Value(**v) for v in doc["values"]], [Conflict(**c) for c in doc["conflicts"]]
