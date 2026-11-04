"""The source check's instructions, one per call kind. In code, not a firm
input: they are SMD's method, measured on 2026-11-04, and a firm edit to them
could quietly let the digest back in as evidence.

Each system prompt opens with a fixed line (``ENUMERATE`` etc.) that names
the call kind; the ledger, the tests' scripted model and a reader of a stored
call all key on it."""

from __future__ import annotations

ENUMERATE = "SOURCE CHECK, STEP 1: LIST EVERY CHECKABLE ITEM."
GRADE = "SOURCE CHECK, STEP 2: GRADE EACH ITEM AGAINST ITS SOURCES."
ADVERSARY = "SOURCE CHECK, WHOLE DOCUMENT: READ IT AS OPPOSING COUNSEL, THEN AS THE MEDIATOR."
SECOND = "SOURCE CHECK, SECOND READ: ONE CLAIM, ITS SOURCE, NOTHING ELSE."

NOT_EVIDENCE = """WHAT IS NOT A SOURCE. The record digest, earlier drafts, the attorney's
request, these instructions and your own memory of the law are NOT sources.
Nothing is confirmed from them. When the only support you can find for an
item is one of these, the item could not be verified."""

ENUMERATE_SYSTEM = (
    ENUMERATE
    + """

You receive one PART of a legal document written for a law firm (a mediation
brief, a memo, or the attorney notes filed beside it), the index of the
documents in the matter's file, the index of the primary legal texts on file,
and ORIENTATION notes taken from the record digest.

Go through the part sentence by sentence and list every item a careful second
lawyer would check against the record before the document leaves the firm.
One sentence often holds several items. List each:
- every quotation (the quoted words, exactly as the part prints them);
- every record cite, and what the sentence says the cited place shows;
- every fact about a person, an event, a record or the case;
- every figure, date, age, count, dollar amount and duration;
- every legal authority (a case, statute, rule or jury instruction) and what
  the sentence says it holds or requires;
- every statement about the document itself or a cross-reference to another
  part of it.

For each item give the document it rests on, by its EXACT name from the index,
and the file pages (or, for a deposition, the transcript pages) the cite names.
If the cite names no page, give the pages where you expect it. If the item
rests on no document in the index, give no source. For an authority, give the
name of its primary text from the legal-text index, or "" when none is on file.
Give two to five search terms for each item: the distinctive words (names,
dates, figures, places, terms of art) a contradicting record would also carry.
Never a generic word.

The ORIENTATION notes only help you find where a fact came from. """
    + NOT_EVIDENCE
    + """

Answer with JSON only, no prose before or after:
{"items": [{"n": 1, "text": "<the sentence, copied character for character from the part>",
  "kind": "quote|cite|fact|figure|authority|crossref|characterization",
  "statement": "<the one assertion this item checks>", "quote": "<quoted words, or empty>",
  "cite": "<the cite as printed, or empty>",
  "sources": [{"file": "<exact index name>", "pages": [3], "transcript_pages": []}],
  "authority": "<legal-text index name, or empty>", "terms": ["..."]}]}"""
)

GRADE_SYSTEM = (
    GRADE
    + """

You receive items listed from one part of a legal document, and EVIDENCE: the
pages of the matter's documents each item cites, the pages a search found that
may contradict it, any reading of a page image, and any primary legal text on
file. Each evidence block has an id like [E7]. The evidence blocks are the only
sources.

"""
    + NOT_EVIDENCE
    + """

Run six checks on every item:
1. A quotation is word for word on the cited page and lines, and the words
   around it do not change its meaning.
2. The cite resolves to that place, and that place says what the sentence
   claims.
3. The fact has support, and the other evidence shown (the search pages) does
   not contradict it.
4. Every figure, date and age matches the source exactly. Never compute one.
5. An authority is in a primary text shown here and holds what the sentence
   says. With no primary text shown, it could not be verified.
6. The item agrees with itself and with what the part says elsewhere.

Grade each item:
- "confirmed": every part of it is in the evidence. List the ids you used.
- "wrong": the evidence shows it is false, misquoted, mis-cited (including a
  pin cite to the wrong page) or contradicted. "serious" when a mediator or a
  court would be misled about a fact, a figure, a quotation or the law;
  "minor" for a pin cite off by a page, a citation form, punctuation. List
  the ids that show it, and give the fix: one line saying what the source
  says, with its page.
- "framing": true as far as it goes, but the cited passage or another record
  shown has a qualifier or a contrary fact the sentence leaves out. List the id
  of that record.
- "could_not_verify": the evidence does not hold what is needed. Never round
  this up to confirmed.

Answer with JSON only:
{"grades": [{"n": 1, "grade": "confirmed|wrong|framing|could_not_verify", "severity": "serious|minor|",
  "check": 2, "evidence": ["E3"], "note": "<one sentence>", "fix": "<one line, or empty>"}]}"""
)

ADVERSARY_SYSTEM = (
    ADVERSARY
    + """

You receive a whole legal document and the index of the matter's documents.
First read it as opposing counsel looking for anything to attack: a statement
one part makes that another part contradicts, a figure printed two ways, an
argument that overstates what its own cites show, a missing answer to the
obvious defense. Then read it as the mediator deciding how much of it to trust.

Report what each would attack. For a contradiction, give both sentences
exactly as printed. """
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
decide for yourself whether the claim is wrong: false, misquoted, cited to a
place that does not say it, or contradicted by the source shown. A claim the
source supports in other words, at the same strength, is not wrong. """
    + NOT_EVIDENCE
    + """

Answer with JSON only:
{"wrong": true, "severity": "serious|minor", "why": "<one sentence>", "fix": "<one line: what the source says, with its page>"}"""
)

REPAIR_RULE = """SOURCE REPAIR. A check of every statement against the record's own pages
found the statements below wrong, and a second reader agreed. Correct EACH to
what its source page says, using the source text given. Change only those
statements; keep every other sentence, citation and marker exactly as it is.
Never add a fact the source page does not carry. Where a statement cannot be
made true from the source, remove it and leave a {{ATTORNEY: confirm}} marker.
Output each section repaired and complete, under its exact original heading
line, in the same order, and nothing else."""
