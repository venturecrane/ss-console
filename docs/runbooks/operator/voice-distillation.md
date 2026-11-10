# Distilling a firm voice

How a customer's own writing becomes a voice specification their Operator drafts
from, without the specification keeping their prose.

**Read first:** [ADR 0083](../../adr/0083-authorship-model-output-classes.md) for
the authorship model, and
[the 2026-08-01 bake-off](../../research/voice-distillation/2026-08-01-spec-representation-bakeoff.md)
for why the spec is a characterization rather than a set of examples. This
runbook is the procedure; those two are the reasoning behind it.

**Why this lives here and not in a skill file.** `.agents/skills/` is gitignored,
so a method written there is neither version-controlled nor reviewable. The
parts of distillation that must not drift are executable and live in
`operator/bin/`; the parts that are judgment live here, where a change to them
goes through a PR like any other. A local skill wrapper is a convenience over
this document, never a substitute for it.

---

## The one-line version

The compiler computes every number and refuses every copy. The agent supplies
judgment and hypotheses. The customer approves, and is shown where their own
letters disagree with the spec they are approving.

## What the compiler owns, and why it is not a matter of care

An assertion and a measurement are typographically identical in prose. `Count in
corpus: 0` costs one token, reads as computation, and cannot be told apart by
inspection from a number somebody counted. A candidate card in the bake-off
carried two suppression rules in the same format from the same corpus, one right
and one wrong, and no reader could distinguish them.

That is a representation problem, so asking for more diligence does not touch
it. Four modules make the load-bearing properties mechanical:

| Module                               | Makes unforgeable                                                                     |
| ------------------------------------ | ------------------------------------------------------------------------------------- |
| `operator/bin/spec_leak_check.py`    | no sentence was copied — with name-swap masking, so the cheapest evasion fails        |
| `operator/bin/voice_profile.py`      | every number was computed — a card may carry no digit outside a `{{profile.*}}` token |
| `operator/bin/spec_selftest.py`      | no `block` rule refuses output the firm itself produces                               |
| `operator/bin/spec_fixed_strings.py` | boilerplate stays verbatim only where a human approved it                             |

---

## The procedure

Steps are marked **[C]** compiler-computed, **[A]** agent-judged, **[H]**
human-supplied. Anything a compiler can compute is never left to a model.

### 1. [H] Label the corpus before anything reads it

Four axes per document: `audience`, `speech_act`, `signing_author`, and
`exemplary | incidental`.

The fourth is the one that cannot be skipped. Nothing in a filename says whether
a letter is house style or an associate's off-day, and an agent that averages
the two produces a spec describing neither. In the rehearsal corpus, documents
12 and 13 are the only two that break the firm's open-numeral habit and they were
added after the original eleven — newer house style and associate drift are
indistinguishable from the text alone, and only the firm can say which.

An unlabeled corpus still yields signal: `spec_selftest` gates on every document
when no labels are supplied, which is the stricter reading.

### 2. [C] Freeze the corpus as a hashed manifest

Path, SHA-256, word count, labels, date. Emit it into the spec header.

A spec without a manifest is not re-runnable, and everything downstream of an
unrecorded corpus boundary is unfalsifiable. The bake-off's reviewed card read 9
of 13 documents and did not record which 9 — and the four it skipped were
precisely the four that falsify its hardest rules.

### 3. [C] Zone-segment, and discard institutional form

`voice_profile.segment` drops frontmatter, letterhead, recipient block, RE line,
salutation, signature block, enclosure inventory. Discarded ranges are reported
per document and per line so the discarding is reviewable.

This is correctness, not tidiness. A naive hyphen count over documents 12 and 13
returns four body hits **and four frontmatter hits**, the latter from the
fixture's own `note:` field describing the fixture. Half the apparent violations
are the corpus describing itself, and a rule derived from the contaminated
number is inverted.

### 4. [C] Measure

`voice_profile.profile` over prose zones only. Absence probes, sentence-shape
distributions, per-document counterexamples, support counts. Output is
`profile.json`.

**No agent-authored integer survives into any artifact.** Every number renders
with its support — `4 [n=11, 2 counterexample(s)]` — because zero-across-eleven
and zero-across-one are different claims and the second is not evidence.
Anything under three documents is flagged below the confidence floor.

### 5. [A] Read the corpus cold, before seeing the numbers

The agent reads the documents and drafts candidate constructions as
trigger / transform / antitrigger triples, each citing document and line.

Order matters here and is easy to get wrong: reading the statistics first
produces rationalization of the statistics rather than observation of the
writing.

### 6. [A] Propose suppressions and overrides as hypotheses, never as counts

_"I believe this firm never uses discourse connectives"_ is the agent's job.
Returning `0` or `3` is the compiler's. The digit invariant enforces the
division rather than trusting it.

### 7. [C] Apply the confidence ladder

Nothing under three documents enters a table. Nothing with a counterexample
holds `block` tier.

### 8. [C] Propose fixed strings, and approve none of them

`spec_fixed_strings` detects repeated blocks and labels and offers them for
approval. It approves nothing: recurrence is how boilerplate is **found**, not
why it is permitted, and an exemption a derivation could grant itself would be a
bypass rather than an exemption.

A candidate carrying a digit, a currency figure, a date, or a proper noun is
dropped rather than proposed — boilerplate does not contain a claim number, and
a span that does is a sentence about a matter that happened to recur.

### 9. [C] Run the rules against the corpus. This is the highest-value step

`spec_selftest`. Any `block` rule must hold on **100%** of exemplary documents.

Not 90%. At a dozen letters a percentage tolerates exactly one falsifying
document, and the falsifying document is the one carrying the information.

A failing `block` rule **auto-demotes to `warn`** and carries its
counterexamples into the card. That is not an error: an inconsistency in a
firm's own writing is information, and the firm is the only party who can
resolve it. Refusing the spec throws it away; dropping the rule hides it.

### 10. [C] Leak check, and it is a pre-write gate

`spec_leak_check` with the approved fixed strings. Containment at N=8 with digit
and proper-noun masking, per-sentence near-duplicate detection, and an identifier
scan that runs on the **unmasked** spec — because the firm approving its closing
line says nothing about a string carrying a claimant's name, and one approval
must not carry two permissions.

The refusal report carries offsets and counts, never the matched text. The audit
trail for a privacy control must not become the largest copy of the thing it
protects.

**Never retain a hashed n-gram index "so we can re-verify later."** Overlapping
shingle hash-sets chain, and a beam search over legal-English vocabulary recovers
substantial passages. That is the false-privacy claim this design replaced,
wearing a new costume. The corpus exists only inside the run and is discarded;
nothing retained can re-run the check, and that is correct rather than a gap.

### 11. [C] Held-out validation

Exclude two documents from steps 4 through 10. Draft against the compiled spec
and grade against the held-out originals (the `operator/voice-gate/` blind-test
harness that once did this was removed 2026-11-10, unused; grade by hand).

A spec that cannot reproduce a document it never saw is overfit.

### 12. [H] Customer approval, per rule, shown against their own writing

ADR 0083 already requires approval. What it does not require, and what
`spec_selftest.render_demotions` supplies, is that the customer sees **where
their own letters break the rule they are approving**.

### 13. [C] Publish

The approved spec becomes the `voice` body of the relevant output class in
`vaults/<slug>/output-classes.json`, authored through the portal. The applier
installs it root-owned, the pointer reaches the drafting skills, and
`smd_deliver_draft` refuses a draft composed without reading it.

---

## What this procedure has not established

The bake-off ran against a **synthetic** corpus written to instantiate eight
named traits, so every arm was partly rediscovering a specification rather than
discovering a voice. The headline score is an upper bound on a task easier than
the real one.

The measurement that would settle it: run this procedure against a real firm's
corpus with no trait list supplied, and have the firm's principal grade blind.
Until that runs, treat the representation as chosen on good evidence and the
magnitude as unproven.

**And one number is tokenizer-dependent in a way worth internalizing.** The
bake-off's sentence splitter put three documents under a 15% short-sentence
threshold; the splitter in `voice_profile.py` puts every document over it, about
a factor of two apart on the same files. Neither is wrong. The same rule blocks
under one and passes under the other, which is why the tokenizer ships beside
the spec and why a threshold quoted without it is a number someone remembers
rather than a gate.
