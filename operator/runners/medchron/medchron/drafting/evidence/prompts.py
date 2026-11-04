"""The evidence stages' prompts: the extract read, the compose MARKUP rules,
the lint repair, and the page-image questions.

These are the lane's defaults. A firm may author its own extract and compose
prompts as firm inputs (the wiring decides); whatever it authors, the MARKUP
block is appended by code, because render and lint depend on it exactly.
"""

from __future__ import annotations

import json

#: Bumped when the extract prompt changes, so a cached window answer from an
#: older prompt is never reused (``extract`` keys the cache on it).
EXTRACT_VERSION = "evidence-extract-1"

EXTRACT_PROMPT = """You read a window of pages from ONE document in a personal-injury file. \
List every fact on these pages that a brief could rely on: what happened, who said or \
found what, every dollar figure, every date, every statement in testimony, every finding, \
and anything that cuts AGAINST the plaintiff as well as for. Do not judge what matters; \
the drafter chooses later.

Output ONLY a JSON array, one object per fact:
{"file": "<the document name exactly as given>", "page": <the [p.N] page the words are on>, \
"kind": "fact|figure|date|quote|testimony|inference", "role": "<figures only: billed|paid|adjusted|\
opposing_question|expert_stated|other>", "excerpt": "<the page's own words that carry the fact, \
copied character for character, at most 300 characters, never paraphrased, never joined across \
a gap>", "claim": "<the fact in one plain sentence>", "value": "<figures and dates only: the \
figure or date exactly as printed in the excerpt>", "tags": ["<provider, body part, event or \
witness, a few words each>"], "tpage": <testimony only: the transcript page>, "lines": \
[<testimony only: first line>, <last line>]}

Rules:
- The excerpt must be words printed on that page. If you cannot copy the words exactly, \
leave the fact out.
- A figure in a question by opposing counsel is role opposing_question, never a finding. \
A figure an expert or examiner states is expert_stated. A bill's charge is billed; what a \
payer paid is paid; a write-off or contractual adjustment is adjusted.
- A form, a verification or a signature block whose text does not show it was signed or \
dated is kind inference: say in the claim what the text does not show.
- Something you conclude rather than read (a cause, a motive, a likely date) is kind \
inference.
- One fact per object. A page with nothing usable contributes nothing. [] when the window \
carries no facts.
"""

RETRY = (
    "Your answer was not a JSON array. Answer again with ONLY the JSON array described, and nothing "
    "else; [] when these pages carry no facts."
)


def extract_user(name: str, pages: dict[int, str]) -> str:
    body = "\n".join(f"[p.{p}]\n{t}" for p, t in sorted(pages.items()))
    return f"DOCUMENT: {name}\n\n{body}"


MARKUP_BLOCK = """## HOW EVERY FACT IS WRITTEN (binding; code enforces it)

You write prose. Code writes every cite, quotation, figure, date, total, count and table, \
from THE FACT INDEX, after you finish. In the document you type none of them. Instead you \
place tokens naming fact ids from the index:

- {{F:F0001}} a cite (several facts: {{F:F0001,F0007}}). Every sentence that states a fact ends with one.
- {{Q:F0001}} quotes the fact's whole excerpt with its cite. {{Q:F0001|a few words}} quotes \
only those words, which must be a contiguous run of that excerpt. Never type quotation marks \
around record words yourself.
- {{N:F0003}} a figure. {{D:F0004}} a date. Each needs a {{F:...}} for the same id in its sentence.
- {{SUM:F0003,F0005}} a total code computes; only figures of the same role (all billed, or all paid).
- {{COUNT:F0002,F0006,F0009}} how many (visits, providers, bills): code counts the ids.
- {{SPAN:F0004,F0009}} the time between two dates, in days; {{SPAN:F0004,F0009|months}} \
(or |weeks, |years).
- {{T:charges}} the charges table, where the skeleton places it.
- {{A:F0010}} raises an UNCONFIRMED fact to the attorney instead of stating it. \
{{A:?|your question}} raises anything else.
- {{L:slug}} cites a legal authority from THE AUTHORITY STORE by its slug. An authority not \
in the store: {{L:?|the citation}}, which goes to the attorney as unconfirmed. Never type a \
case, statute or reporter cite yourself.

Never type: a dollar amount, a date, a number of visits or days or anything counted, a \
percentage, a page or line cite, "FILE:", "Dep.", "Id.", a reporter cite, or a quotation of \
four or more words. Each is refused and sent back to you.

Say what a figure IS: a billed amount is "billed" or "charged", a paid amount "paid". A \
figure marked opposing_question is opposing counsel's, from a question: say so ("defense \
counsel asked whether ..."), never state it as a fact. A figure marked expert_stated is \
attributed to whoever stated it.

Use a fact for what its EXCERPT says, never more. If the excerpt qualifies the point \
(but, except, only, unless), the qualifier is part of the fact.
"""

LINT_REPAIR = """LINT REPAIR. Code refused the sentences listed under FINDINGS for exactly the reasons \
given. Repair them so they pass: replace every typed cite, quotation, figure, date or count with \
the token for the fact that carries it, attribute a figure the way its role requires, and drop \
any claim no fact in the index supports. Change nothing else. Output each section below repaired \
and complete, under its exact original heading line, in the same order, and nothing else.
"""


def lint_repair_user(sections: str, brief: str) -> str:
    """The one scoped repair: the sections that carry findings, and the
    findings (``lint.repair_brief``)."""
    return LINT_REPAIR + "\n## FINDINGS\n" + brief + "\n\n## SECTIONS TO REPAIR\n\n" + sections


def signed_question(claim: str) -> str:
    return (
        f"This page is cited for: {json.dumps(claim)}. Look only at its signature and date lines. Is it "
        "actually signed (a signature mark on the line, not just a printed name) and, where there is a date "
        'line, dated? Answer ONLY a JSON object: {"answer": "yes" | "no" | "unclear", "reading": "<what the '
        'signature and date lines show, under 30 words>"}'
    )


def figure_question(printed: str) -> str:
    return (
        f"Does this page print the amount {printed}? Read the digits from the image, not from memory. Answer "
        'ONLY a JSON object: {"answer": "yes" | "no" | "unclear", "reading": "<the amount exactly as the '
        'page prints it, or what is there instead>"}'
    )


def thin_question(claim: str, excerpt: str) -> str:
    return (
        f"This page is cited for: {json.dumps(claim)}. Its extracted text was too thin to rely on. The "
        f"extracted words were: {json.dumps(excerpt)}. Does the page image show that? Answer ONLY a JSON "
        'object: {"answer": "yes" | "no" | "unclear", "reading": "<what the page shows, under 30 words>"}'
    )
