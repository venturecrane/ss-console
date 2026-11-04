"""The check's instructions, one per call kind. In code, not a firm input:
they are SMD's method (measured 2026-11-04 by hand on a real brief), and a
firm edit to them could quietly let the digest back in as evidence.

Each system prompt opens with a fixed line naming the call kind; the tests'
scripted model and a reader of a stored call key on it.

What a checker is shown is built by code (``check.py``): the sentence with
each token shown as what it renders to plus its fact id, and, as evidence,
each cited fact's EXCERPT (the page's own characters) and the pages around it.
Never the fact's ``claim``: the claim is the extraction model's words, and a
sentence checked against the model's own words is the digest check again.
"""

from __future__ import annotations

CHARACTERIZE = "SOURCE CHECK, CHARACTERIZATION: DOES EACH SENTENCE SAY WHAT ITS SOURCES SAY?"
OMISSION = "SOURCE CHECK, OMISSION: DOES THE RECORD CARRY SOMETHING THAT CUTS AGAINST THE SENTENCE?"
AUTHORITY = "SOURCE CHECK, AUTHORITY: DOES THE PRIMARY TEXT HOLD WHAT THE SENTENCE SAYS?"
ADVERSARY = "SOURCE CHECK, WHOLE DOCUMENT: READ IT AS OPPOSING COUNSEL, THEN AS THE MEDIATOR."
SECOND = "SOURCE CHECK, SECOND READ: ONE CLAIM, ITS SOURCE, NOTHING ELSE."

NOT_EVIDENCE = """WHAT IS NOT A SOURCE. The record digest, the fact index, earlier drafts,
the attorney's request, these instructions and your own memory of the facts or
the law are NOT sources. Nothing is confirmed from them. When the only support
you can find is one of these, the item could not be verified."""

GRADES = """Grade each item:
- "confirmed": every part of it is in the evidence shown. List the evidence
  ids you used. A confirmed grade naming no evidence id is not a grade.
- "wrong": the evidence shows it is false, overstated, mis-attributed (a
  question's number stated as fact, a billed amount called paid), cited to a
  place that does not say it, or contradicted. "serious" when a mediator or a
  court would be misled about a fact, a figure or the law; "minor" for wording
  that is loose but not misleading. List the ids that show it, and give the
  fix: one line saying what the source says.
- "framing": true as far as it goes, but the evidence shown carries a
  qualifier or contrary fact the sentence leaves out. List that id.
- "could_not_verify": the evidence shown does not hold what is needed. Never
  round this up to confirmed."""

CHARACTERIZE_SYSTEM = (
    CHARACTERIZE
    + """

You receive sentences from one section of a legal document written for a law
firm (a mediation brief, or the attorney notes filed beside it). Code already
proved that every quotation, figure, date and page cite in them is the page's
own: quoted words and numbers are shown exactly as they will print. What code
cannot prove is whether the SENTENCE AROUND them says what the cited record
says. That is your job.

Each sentence carries its fact ids in brackets, like [F0007]. For each item,
EVIDENCE blocks follow, each with an id like [E7]: the cited fact's excerpt
(the page's own characters) and the text of the pages around it. The evidence
blocks are the only sources.

"""
    + NOT_EVIDENCE
    + """

For each sentence check: the sentence states what the cited excerpts and
pages state, at the same strength, about the same person, event, date and
document; nothing in it goes past them; who said or did it is right.

"""
    + GRADES
    + """

Answer with JSON only, no prose before or after:
{"grades": [{"n": 1, "grade": "confirmed|wrong|framing|could_not_verify", "severity": "serious|minor|",
  "evidence": ["E3"], "note": "<one sentence>", "fix": "<one line, or empty>"}]}"""
)

OMISSION_SYSTEM = (
    OMISSION
    + """

You receive sentences from a legal document, each with the record excerpts it
cites, and for each, CANDIDATES: other excerpts from the same documents, dates
or subjects that the document does not cite anywhere. Each candidate has an id
like [C4].

For each sentence, say which candidates (if any) cut against it: a reader who
saw that candidate would trust the sentence less (an earlier injury, a gap in
treatment, a contrary finding, a qualifier on the cited statement). A
candidate that merely adds detail, or agrees, does not cut against it.

"""
    + NOT_EVIDENCE
    + """

Answer with JSON only:
{"omissions": [{"n": 1, "against": ["C4"], "note": "<one sentence: what the candidate shows>"}]}
List a sentence only when a candidate cuts against it."""
)

AUTHORITY_SYSTEM = (
    AUTHORITY
    + """

You receive sentences from a legal document that rely on a legal authority (a
case, statute, rule or jury instruction), and for each the PRIMARY TEXT of that
authority as the firm has it on file, in blocks with ids like [P2]. The blocks
are the only sources. Check that the authority holds or requires what the
sentence says it does, at the same strength, and that the sentence does not
attribute to it a holding it does not contain.

"""
    + NOT_EVIDENCE
    + """

"""
    + GRADES
    + """

Answer with JSON only:
{"grades": [{"n": 1, "grade": "confirmed|wrong|framing|could_not_verify", "severity": "serious|minor|",
  "evidence": ["P2"], "note": "<one sentence>", "fix": "<one line, or empty>"}]}"""
)

ADVERSARY_SYSTEM = (
    ADVERSARY
    + """

You receive a whole legal document. First read it as opposing counsel looking
for anything to attack: a statement one part makes that another part
contradicts, a figure printed two ways, an argument that overstates what its
own cites show, a missing answer to the obvious defense. Then read it as the
mediator deciding how much of it to trust.

Report what each would attack. Give the sentence exactly as printed, and for a
contradiction the other sentence exactly as printed. """
    + NOT_EVIDENCE
    + """

Answer with JSON only:
{"findings": [{"text": "<the sentence, character for character>", "kind": "contradiction|overstatement|omission",
  "against": "<the other sentence, character for character, or empty>", "severity": "serious|minor",
  "note": "<one sentence>", "fix": "<one line, or empty>"}]}"""
)

SECOND_SYSTEM = (
    SECOND
    + """

Another reader found the claim below WRONG. You did not see that reader's
reasons and must not guess them. Read the claim and the source text given, and
decide for yourself whether the claim is wrong: false, overstated,
mis-attributed, cited to a place that does not say it, or contradicted by the
source shown. A claim the source supports in other words, at the same
strength, is not wrong. """
    + NOT_EVIDENCE
    + """

Answer with JSON only:
{"wrong": true, "severity": "serious|minor", "why": "<one sentence>", "fix": "<one line: what the source says>"}"""
)

REPAIR_RULE = """SOURCE REPAIR. A check of every sentence against the record's own pages
found the sentences below wrong, and an independent second reader agreed.
Rewrite EACH so it says what its sources say, using only the fact ids it
already cites or ids from the fact index. Change only those sentences; keep
every other sentence and every token exactly as it is. Never type a quotation,
figure, date or cite by hand: they stay tokens. Where a sentence cannot be
made true from the record, remove it and leave a {{A:?|confirm}} marker."""
