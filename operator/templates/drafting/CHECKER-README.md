# drafting_gate_check.py

The mechanical half of the ten-gate enforcement map in `drafting-discipline.md`.
No draft surfaces to the requesting attorney without passing it. Stdlib only,
Python 3.10+, no install step. Execution point per the discipline doc: on seats
where `code_execution` is authored the skill runs it directly; certification and
rehearsal runs execute it repo-side.

> **THE DELIVERY-PATH HOOK DOES NOT EXIST YET (verified 2026-08-13,
> ss-console#2258).** This paragraph used to claim the checker "runs harness-side
> on the delivery path (overlay drafting-gate hook, hermes-smd-overlay#193)."
> Nothing runs it there. In the overlay `drafting_gate_check.py` appears only as
> a presence probe (`establish_intake/gates.py`), and `hermes-smd-drafting`
> explicitly disclaims the record checks.
>
> So on a seat without `code_execution` — which is every client seat, by design —
> **this checker runs nowhere**, and such a seat is the discipline's variant C
> (no gate available), not variant B. See `drafting-discipline.md` for what
> variant C requires. Building the delivery-path gate is tracked as the drafting
> lane's remaining reachability row; until it lands, do not describe a draft on
> such a seat as gated, and do not let this file's first sentence be read as a
> statement of current fact.

A clean run means the draft cleared the mechanical floor. It never means the
draft is correct. Judgment, characterization, and legal merit are PROSE and
CONTEXT enforcement points and are deliberately out of scope here.

## CLI

```
python3 drafting_gate_check.py --draft <file.md> --sources <dir> \
    [--held-out <file-or-comma-list>] \
    [--propounded <items.txt>] \
    [--sprog-lint] [--json]
```

| Flag           | Meaning                                                                                                                         |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `--draft`      | The draft markdown to check. Required.                                                                                          |
| `--sources`    | Directory (or comma list of files and directories) of record sources. Walked for `.md`, `.txt`, `.markdown`, `.text`. Required. |
| `--held-out`   | Documents held out for privilege review. Enables the gate 1 leakage check.                                                      |
| `--propounded` | One propounded item id per line (`SROG 1`, `RFP 4`). Blank lines and `#` comments ignored. Enables gate 7.                      |
| `--sprog-lint` | Run the special-interrogatory subpart lint (gate 8).                                                                            |
| `--json`       | Emit machine JSON instead of the human report.                                                                                  |

Exit codes: `0` no FAIL findings, `1` one or more FAIL findings, `2` usage or IO
error. Severities are FAIL (blocks), WARN (attorney should look), INFO (a
sub-check did not run, and why). Only FAIL affects the exit code.

Malformed input never raises out of `main`. An unexpected internal error is
reported as a FAIL and the run fails closed, because a checker that crashes is
indistinguishable from a checker that passed.

## Gate map

| Gate | Check                                                                                                                                                                                                                                | Severity         |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------- |
| 2a   | Every double-quoted string of 4 or more words appears verbatim-contiguous in a source. Misses report the closest fuzzy region (difflib) to aid review.                                                                               | FAIL             |
| 2a   | A quotation whose omission is marked with an ellipsis, where every segment resolves in one source in order. The finding names the omitted words.                                                                                     | WARN             |
| 2a   | An ellipsis whose gap swallows an intervening question. This is a splice wearing an ellipsis.                                                                                                                                        | FAIL             |
| 2b   | A transcript quote's cited page:line range includes the question the answer actually answered.                                                                                                                                       | FAIL             |
| 2b   | A transcript quote does not appear anywhere within its cited range.                                                                                                                                                                  | FAIL             |
| 2b   | Any condition under which pairing could not be evaluated. Fail-open by design, see limits below.                                                                                                                                     | INFO             |
| 2c   | A quotation followed by `(FILE: <name>, p. N)` is on page N of that document, read from the extractor's `[p.N]` markers. Run by the drafting job (`medchron/drafting/pincheck.py`), after this checker.                              | FAIL             |
| 2c   | A pin that is wrong but whose words are on exactly one other page (or page pair) of the same document is moved there in code, and said in the notes.                                                                                 | repaired         |
| 2c   | A pin that could not be tested (no page markers, or 2a refuses the quotation). Counted in the gate record's `pins`.                                                                                                                  | INFO             |
| 1    | Eight or more consecutive words shared between the draft body and a held-out document, or between the body and the draft's own HELD OUT section.                                                                                     | FAIL             |
| 6    | Internal file paths in the draft body: `operator/` paths, `r2://` URIs, `vaults/` paths, backticked `.md` or `.py` references, `.claude/` paths, absolute `/Users/` paths. Repeats are grouped into one finding per distinct string. | FAIL             |
| 3    | Blanket completeness sentences. Seed list in `_SELF_CERT_PATTERNS`; extend there, not at the call site. Itemized what-was-done reporting does not trigger.                                                                           | FAIL             |
| 7    | Every propounded item has a response heading. Missing items fail; response headings with no matching item warn.                                                                                                                      | FAIL / WARN      |
| 8    | Explicit lettered subparts in a special interrogatory (CCP 2030.060(f)).                                                                                                                                                             | FAIL             |
| 8    | Compound-interrogatory heuristics: semicolon-chained directives, `each and every ... and ...`, two or more directives in one item.                                                                                                   | WARN             |
| 9    | A `{{NOT IN RECORD`, `{{FILL`, or `{{ATTORNEY` marker inside an HTML comment, where it vanishes on render.                                                                                                                           | FAIL             |
| 9    | An unclosed `{{` marker. Nesting-aware, so a CANDIDATE OBJECTION carrying a NOT IN RECORD marker in its basis clause is valid.                                                                                                       | FAIL             |
| 9    | A marker inside a code fence, where it may not read as a reservation.                                                                                                                                                                | WARN             |
| MI   | A `{{FILL: ...}}` marker with no `                                                                                                                                                                                                   | source` segment. | WARN |

## What normalization tolerates, and what it does not

Before any contiguity comparison, both the quote and the source are normalized:
smart quotes and dashes to ASCII, whitespace collapsed, and inline markdown
(`**`, `*`, `__`, `~~`, backticks, blockquote carets) removed. Markdown is
typography, not testimony, and stripping it symmetrically can only remove false
failures: a splice does not become contiguous when asterisks come off.

Three quotation conventions are tolerated, all of them changes to presentation
rather than to the quoted words:

1. A leading letter the draft case-folded to fit its sentence (`Right` quoted as
   `right`).
2. The bracketed form of that same alteration (`"[a]s hard as I could"`).
3. Trailing punctuation the draft added outside the source clause, including the
   closing mark of a nested quotation, where American convention turns a source
   period into a comma.

Nothing inside the passage is loosened. Words added inside quotation marks,
words changed inside quotation marks, and hedges excised without an ellipsis all
still fail.

## Transcript structure the checker looks for

Gate 2b needs two things in a source: page markers on their own line (`Page 23`)
and a numbered line gutter (`22   A.  No, I cannot.`, or a reporter's text
layer with middle-dot spacers, `22· ·A.· · ·No, I cannot.`). Where it finds them
it builds a page:line index and can walk back from a quoted answer to the
question that governs it. Sources are also flattened a second way, with the
line gutter, the spacers and the page-marker lines (a transcript's `Page 23` and
the extractor's `[p.4]`) stripped, so that a quote
spanning transcript lines or a page break reads as contiguous. That second view
only ever removes false failures. The other direction is closed too: a
quotation that itself carries a spacer, or a line number before a `Q.`/`A.`
label, fails 2a even when the raw text layer holds it verbatim, because
typesetting in a quotation is a garble, never the record's words.

Because a phrase can appear in more than one document and more than once in the
same one, pairing is judged at the occurrence the draft cited, not at whichever
copy the contiguity search reached first.

## Known limits, stated plainly

**Gate 2b fails open in five distinct situations**, each of which emits an INFO
note naming itself rather than a failure:

1. The source carries no parseable page:line markers.
2. No governing question can be located above the quoted passage.
3. The sentence carrying the quote has no page:line cite at all. Note that an
   uncited quotation is a gate 2 defect on its own, enforced in prose.
4. The cite is a single point rather than a range, so there is no range to test
   inclusion against.
5. The quote is contiguous in a document with no page:line structure, so the
   cited range cannot be attributed to a transcript with confidence.

This is deliberate. Gate 2b's FAIL classes are narrow and high-confidence; the
sub-check reports when it did not run so the attorney knows what was and was not
mechanically verified.

**Gate 8 is heuristic.** Only explicit lettered subparts fail. The compound
patterns (semicolon chains, `each and every`, multiple directives) warn, because
a legitimate single-fact interrogatory can use two verbs and a compound one can
avoid all three patterns. The lint scopes to items matching
`SPECIAL INTERROGATORY NO. <n>` as a heading or a bold line; a set that numbers
its items some other way is not seen, and the run emits an INFO saying so.

**Gate 3 is a seed list, not a semantic judgment.** It catches the phrasings the
prove-out actually observed. A novel blanket certification phrased differently
passes. Extend `_SELF_CERT_PATTERNS` when a new one is observed.

**Gate 7 matches on headings.** An item answered in prose without a response
heading reads as missing. The alias table in `_ITEM_ALIASES` maps item-file ids
to heading language; an unrecognized id type is matched literally and warns if
unparseable.

**Gate 1 uses an eight-word run.** Held-out material paraphrased rather than
copied is not detected. The structural privilege wall is context assembly, which
keeps held-out material out of the drafting context in the first place; this
check is the backstop, not the wall.

**Gate 2a scopes to the draft body.** Quotes inside the HELD OUT section are not
checked for contiguity, because held-out documents are correctly absent from
`--sources`. Quoted text inside a `{{...}}` marker is also skipped: a skeleton
FILL that illustrates its own format is drafting apparatus, not an assertion.

## Calibration against the prove-out

The checker was run against all 22 artifacts of the 2026-07-28 drafting matrix
with `--sources` pointed at the case record. Both artifacts the adversarial panel
verified quote by quote come back with zero quote-gate failures. The findings it
does produce on the other arms are dominated by true positives of exactly the
classes the prove-out named: a fabricated phrase appended inside quotation marks,
a pronoun changed inside quotation marks, a reservation buried in an HTML
comment, a blanket completeness sentence, and record cites written as internal
filenames.

Five false-positive classes surfaced during that calibration and were fixed
rather than tolerated: markdown emphasis breaking contiguity, bracketed leading
letters, nested-quotation punctuation, nested markers read as unclosed, and
pairing judged at the wrong occurrence of a repeated phrase. Each has a
regression test.

## Tests

```
python3 operator/templates/drafting/tests/test_drafting_gate_check.py
```

49 tests, no third-party dependencies. Also runs under
`python3 -m unittest` from within the tests directory, and under pytest.
