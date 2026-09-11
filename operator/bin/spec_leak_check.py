#!/usr/bin/env python3
"""Refuse a distilled voice spec that copied the customer's prose.

WHAT THIS MAKES TRUE, precisely
-------------------------------
    No sentence in this specification was copied from a document we read.

Provenance-scoped, and deliberately not artifact-scoped. "This file contains no
customer prose" is a claim about a file, and it is falsified the first time a
Named Administrator types one of their own sentences into the portal — which is
their right, and which no check should stop. What IS checkable, at the only
moment it is checkable, is that the DERIVATION did not copy. So this runs as a
pre-write gate inside the distiller, never as a post-hoc audit, and the honest
sentence downstream is "the derivation cannot produce a leaking spec", never
"the seat cannot hold one".

WHY A COMPILER RATHER THAN AN INSTRUCTION
-----------------------------------------
Because an instruction was already tried and already failed, in this repo, on
the first attempt. `operator/customers/pilot-smokeball/seed/voice/
drafting-voice-spec.md` is a production-rule spec authored by an agent told to
characterize rather than copy, and line 55 of it is byte-identical to line 58 of
`01-demand-mva-duarte.md`. Nobody was careless; the instruction simply does not
hold this line. An agent that skips a compiler has NO artifact, not an unchecked
one, and that is a different kind of guarantee from an agent that skips a step.

N = 8, AND IT IS PRECEDENT RATHER THAN A FRESH GUESS
-----------------------------------------------------
`operator/templates/drafting/drafting_gate_check.py` has run this exact check in
production since the drafting lane shipped, at `_HELD_OUT_NGRAM = 8`, with its
rationale in place: "Eight is long enough that shared legal boilerplate does not
trip it." Same threshold, same document type, already CI-covered. This module
imports that constant rather than restating it, so the two can never drift.

Below 8, professional boilerplate trips it — and the spec's own never-does list
is an inventory of boilerplate ("please be advised", "enclosed please find"),
so a low N would refuse a spec for correctly naming what the firm avoids. Above
8, a copied sentence with two words changed passes.

MEASURED 2026-08-01, and one prediction did NOT hold
-----------------------------------------------------
Shingling the 13-document rehearsal corpus against ITSELF — same firm, same
register, same document types, the hardest innocent-collision control available
— gives 383 / 133 / 80 / 41 collisions at N = 4 / 6 / 8 / 10. The expectation
going in was zero at 8. It is not zero, and the reason is not noise: this firm
REPEATS ITSELF BY DESIGN. "We would rather resolve this. We are prepared not to."
closes four of its letters. The anaphora on "You have" recurs across three. Its
recurring constructions are its signature, so its own documents legitimately
share long runs.

That does not weaken the check, because the check never compares two corpus
documents. It compares a SPEC to the corpus, and a spec that CHARACTERIZES a
recurring construction ("closes to adjusters with a two-sentence construction
offering resolution and signaling readiness to litigate") shares nothing with
it, while a spec that QUOTES it shares all of it. The intra-corpus number
measures the firm's self-repetition; it is not a false-positive rate. What it
does mean is that the "0 at 8, nonzero at 6" defense is unavailable, and the
threshold rests on precedent instead — which was always the stronger leg.

Run against the two real artifacts in that directory, the check separates them
the way it should: `voice-profile.md`, built on 38 verbatim exemplars, returns
59 findings; `drafting-voice-spec.md`, a production-rule spec with 3 embedded
verbatim shapes, returns 8. Both are correctly refused, and the ratio tracks how
much each actually copied.

The never-does list needs NO exemption, and must not be given one. Those phrases
are absent from the corpus BY CONSTRUCTION — that is what the trait asserts — so
an n-gram check against the corpus structurally cannot trip on them. If one does
trip, the firm does write it, the trait is false, and the refusal is correct
information about a bad distillation. The list is self-policing. Do not
allowlist it.

WHAT DEFEATS A NAIVE CHECK, AND WHY MASKING IS LOAD-BEARING
------------------------------------------------------------
Swapping the names. "…hit Marisol Duarte" -> "…hit Serena Okafor" passes an
exact comparison while being, in every respect that matters, the customer's
sentence. This is not hypothetical: it is the cheapest edit a distiller under
budget pressure makes, and therefore the one it will make. So digit runs
collapse to `#` and out-of-vocabulary capitalized tokens collapse to `@` before
shingling, and `test_a_name_swapped_sentence_is_still_a_copy` pins it.

A second pass with different failure characteristics catches what n-grams miss:
per-sentence token-trigram Jaccard against every corpus sentence, which sees
reordering and single-word substitution that break an 8-run. Lemmatization is
deliberately absent — it raises false positives, adds a runtime dependency to a
stdlib-only script, and catches little the Jaccard pass does not.

THE REPORT IS OFFSETS-ONLY, AND THAT IS A SECURITY PROPERTY
------------------------------------------------------------
A refusal that prints the matched text puts the customer's prose into a
terminal, a shell history, a CI log, a PR comment, and an agent transcript. The
audit trail for a privacy control must not become the largest copy of the thing
it protects. So findings carry token counts, corpus document ids, and offsets —
never the matched span, never a source excerpt, never a did-you-mean. Note that
the production `_overlap_findings` DOES emit `run[:300]`: correct there, because
the other side is the firm's own record which they already hold, and wrong here.
`test_the_report_never_echoes_the_matched_text` pins the difference.

THE TRAP, NAMED BEFORE SOMEONE PROPOSES IT
-------------------------------------------
Do not retain a hashed n-gram index "so we can re-verify the spec later".
Overlapping shingle hash-sets chain, and a beam search over a legal-English
vocabulary recovers substantial passages from them. That would be exactly the
false-privacy claim this module replaces — "it's only hashes, therefore
content-free" — wearing a new costume. The corpus exists only inside the
distiller run, the check runs there, and the corpus is discarded. Nothing
retained can re-run it. That is correct, not a gap.

Usage::

    python3 operator/bin/spec_leak_check.py \\
        --spec build/voice-spec.md \\
        --corpus build/corpus.client.jsonl \\
        --attestation build/attestation.json

Exit 0 clean, 2 on a leak, 1 on a usage fault.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GATE_CHECK = _REPO_ROOT / "operator" / "templates" / "drafting" / "drafting_gate_check.py"


def _load_gate_check():
    """Import the drafting checker by path for its tokenizer and threshold.

    Imported rather than reimplemented so the containment threshold cannot drift
    between the check that keeps a draft from reproducing held-out record text
    and the check that keeps a spec from reproducing the corpus. They are the
    same question asked in two directions, and one constant should answer both.
    """
    spec = importlib.util.spec_from_file_location("_smd_drafting_gate_check", _GATE_CHECK)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging fault
        raise RuntimeError(f"cannot load {_GATE_CHECK}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GATE = _load_gate_check()

#: THE threshold. Imported, never restated — see the module docstring.
NGRAM = _GATE._HELD_OUT_NGRAM

#: Sentence-level near-duplicate ceiling: above this share of trigrams in common
#: with any corpus sentence, a spec sentence is a PARAPHRASE of that sentence
#: rather than a characterization of it.
#:
#: MEASURED, not chosen. The first value here was a guessed 0.6 and it let the
#: canonical evasion through — reordering a sentence around its own clauses
#: breaks every 8-run and most trigrams, so the number that catches it is far
#: lower than intuition suggests. Measured 2026-08-01 on the rehearsal corpus:
#:
#:   reordered paraphrase of a corpus sentence   0.294
#:   worst legitimate characterization, over
#:     5 hand-written trait descriptions against
#:     every sentence in all 13 documents        0.038
#:
#: 0.15 sits about 4x above the worst legitimate score and about half the
#: evasion, which is the widest margin the data supports. Both populations are
#: small, so this is a threshold to re-measure on a real corpus rather than
#: inherit — the distillation procedure's held-out step is where that happens.
JACCARD_MAX = 0.15

#: Sentences shorter than this are not compared for near-duplication. A short
#: professional sentence collides with everything and means nothing.
MIN_SENTENCE_TOKENS = 6

#: Shapes that are identifying regardless of paraphrase. A fully reworded
#: demonstration can still name a real carrier, provider, or claim number, which
#: no containment check can see. This is the half of `assert_style_only` that
#: was ever doing real work, carried forward; its closed-enum walk is not, its
#: premise ("the output is numbers") being dead for prose.
_IDENTIFIER_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b\d{3}-\d{2}-\d{4}\b", "ssn-shaped"),
    (r"\b\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b", "phone-shaped"),
    (r"\b[A-Z]{2,}[-\s]?\d{6,}\b", "claim-or-policy-shaped"),
    (r"\b\d+\s+[A-Z][a-z]+\s+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln)\b", "street-address"),
    (r"\bsk-[A-Za-z0-9]{16,}\b", "api-key-shaped"),
    (r"\bAKIA[0-9A-Z]{16}\b", "aws-key-shaped"),
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_CAP_TOKEN = re.compile(r"\b[A-Z][a-z]{2,}\b")
_DIGIT_RUN = re.compile(r"\d+")

#: Capitalized words that are ordinary English rather than names. Kept small on
#: purpose: a word wrongly masked is a HOLE (two different names both become
#: `@`, so a real copy can slip through), while a word wrongly left unmasked is
#: only a false positive a human resolves. Err toward masking less.
_CAP_STOPWORDS = frozenset(
    """The This That These Those There Their They Them Then Than When Where While
    What Which Who Whom Whose Why How And But Not Nor For Yet Because Since If
    Although Though After Before During Until Unless Every Each Both Either
    Neither Some Most Many Few All Any One Two Three Four Five Six Seven Eight
    Nine Ten First Second Third Never Always Often Rarely Here Once Twice
    Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February
    March April May June July August September October November December""".split()
)


@dataclass(frozen=True)
class Finding:
    """A leak, described without reproducing it.

    Every field is a measurement or a pointer. There is deliberately no field
    that could carry the matched text — the omission is the design, not an
    oversight, and a future edit that adds one re-opens the hole the
    offsets-only rule closes.
    """

    kind: str
    corpus_doc: str
    spec_offset: int
    spec_line: int
    tokens: int
    detail: str = ""


@dataclass
class Report:
    clean: bool
    ngram: int
    jaccard_max: float
    spec_sha256: str
    corpus_docs: int
    corpus_tokens: int
    findings: list[Finding] = field(default_factory=list)
    sweep: dict[int, int] = field(default_factory=dict)
    #: How much verbatim survived, and it is on the artifact's face on purpose.
    #: An attestation that names its exemption budget is checkable; one that
    #: does not is a promise.
    approved_used: int = 0
    approved_tokens: int = 0

    def to_json(self) -> str:
        payload = asdict(self)
        payload["findings"] = [asdict(f) for f in self.findings]
        payload["sweep"] = {str(k): v for k, v in sorted(self.sweep.items())}
        return json.dumps(payload, indent=2, sort_keys=True)


# --------------------------------------------------------------------------- #
# normalization                                                               #
# --------------------------------------------------------------------------- #


def mask(text: str) -> str:
    """Collapse the two things a lazy distiller edits, before tokenizing.

    Digit runs become ``#`` and out-of-vocabulary capitalized tokens become
    ``@``. Without this, changing "Marisol Duarte" to "Serena Okafor" and
    "$47,500" to "$52,000" turns a copied sentence into a passing one while
    leaving it, in every respect that matters, the customer's sentence.
    """
    text = unicodedata.normalize("NFKC", text)
    text = _CAP_TOKEN.sub(lambda m: m.group(0) if m.group(0) in _CAP_STOPWORDS else "@", text)
    return _DIGIT_RUN.sub("#", text)


def tokens(text: str) -> list[str]:
    """Masked, normalized word tokens — the drafting checker's tokenizer."""
    return _GATE.word_tokens(_GATE.strip_markdown(mask(text)))


def ngrams(seq: Sequence[str], size: int) -> set[tuple[str, ...]]:
    return {tuple(seq[i : i + size]) for i in range(len(seq) - size + 1)}


def sentences(text: str) -> list[tuple[int, str]]:
    """(offset, sentence) pairs, offsets into the ORIGINAL text for reporting."""
    out: list[tuple[int, str]] = []
    cursor = 0
    for part in _SENTENCE_SPLIT.split(text):
        idx = text.find(part, cursor)
        if idx < 0:
            idx = cursor
        out.append((idx, part))
        cursor = idx + len(part)
    return out


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, max(offset, 0)) + 1


# --------------------------------------------------------------------------- #
# the checks                                                                  #
# --------------------------------------------------------------------------- #


def containment_findings(spec: str, corpus: dict[str, str], size: int) -> list[Finding]:
    """Any run of ``size`` masked tokens the spec shares with a corpus document."""
    spec_tokens = tokens(spec)
    if len(spec_tokens) < size:
        return []
    findings: list[Finding] = []
    for doc_id, doc in corpus.items():
        doc_grams = ngrams(tokens(doc), size)
        if not doc_grams:
            continue
        i = 0
        while i <= len(spec_tokens) - size:
            if tuple(spec_tokens[i : i + size]) in doc_grams:
                end = i + size
                while end < len(spec_tokens) and tuple(spec_tokens[end - size + 1 : end + 1]) in doc_grams:
                    end += 1
                findings.append(
                    Finding(
                        kind="containment",
                        corpus_doc=doc_id,
                        spec_offset=i,
                        spec_line=0,
                        tokens=end - i,
                        detail=f"{end - i} consecutive masked tokens shared",
                    )
                )
                i = end
                continue
            i += 1
    return findings


def jaccard_findings(spec: str, corpus: dict[str, str], ceiling: float) -> list[Finding]:
    """Spec sentences that are paraphrases rather than characterizations.

    Catches what an 8-run cannot: reordering, and single-word substitution
    spaced closely enough to break every window.
    """
    corpus_tri: list[tuple[str, set[tuple[str, ...]]]] = []
    for doc_id, doc in corpus.items():
        for _, sentence in sentences(doc):
            tri = ngrams(tokens(sentence), 3)
            if len(tri) >= 3:
                corpus_tri.append((doc_id, tri))

    findings: list[Finding] = []
    for offset, sentence in sentences(spec):
        stoks = tokens(sentence)
        if len(stoks) < MIN_SENTENCE_TOKENS:
            continue
        stri = ngrams(stoks, 3)
        if not stri:
            continue
        for doc_id, tri in corpus_tri:
            union = len(stri | tri)
            if not union:
                continue
            score = len(stri & tri) / union
            if score > ceiling:
                findings.append(
                    Finding(
                        kind="near_duplicate",
                        corpus_doc=doc_id,
                        spec_offset=offset,
                        spec_line=line_of(spec, offset),
                        tokens=len(stoks),
                        detail=f"trigram jaccard {score:.2f} > {ceiling:.2f}",
                    )
                )
                break
    return findings


def identifier_findings(spec: str, proper_nouns: Iterable[str]) -> list[Finding]:
    """Identifying content a containment check structurally cannot see."""
    findings: list[Finding] = []
    for pattern, label in _IDENTIFIER_PATTERNS:
        for match in re.finditer(pattern, spec):
            findings.append(
                Finding(
                    kind="identifier",
                    corpus_doc="",
                    spec_offset=match.start(),
                    spec_line=line_of(spec, match.start()),
                    tokens=0,
                    detail=label,
                )
            )
    for noun in {n.strip() for n in proper_nouns if n and len(n.strip()) > 2}:
        for match in re.finditer(rf"\b{re.escape(noun)}\b", spec):
            findings.append(
                Finding(
                    kind="identifier",
                    corpus_doc="",
                    spec_offset=match.start(),
                    spec_line=line_of(spec, match.start()),
                    tokens=0,
                    detail="proper noun from the provenance map",
                )
            )
    return findings


def sweep(spec: str, corpus: dict[str, str], lo: int = 4, hi: int = 12) -> dict[int, int]:
    """Containment count at each N, recorded in every attestation.

    So that a future innocent collision reads as a curve someone can look at,
    rather than arriving as a mysterious refusal at one magic number.
    """
    return {n: len(containment_findings(spec, corpus, n)) for n in range(lo, hi + 1)}


def apply_approved(spec: str, approved: Sequence[str]) -> tuple[str, int, int]:
    """Mask HUMAN-approved fixed strings out of the spec before checking.

    The fixed-string layer (`spec_fixed_strings.py`) exists because a firm's
    boilerplate — its signature close, its section labels — is institutional
    form, and paraphrasing it is the one thing that must not happen to it. Those
    strings are verbatim BY DESIGN, so the containment check must not refuse
    them.

    THE EXEMPTION IS SAFE ONLY BECAUSE OF WHERE IT COMES FROM. Nothing in the
    derivation can put a string in this list. The detector PROPOSES candidates
    and a person writes the approved file; recurrence is how boilerplate is
    FOUND, not why it is permitted. An exemption a distiller could grant itself
    would not be an exemption, it would be the bypass that voids the guarantee.

    The counts are returned and land in the attestation, so "how much of their
    prose did we keep, and with whose permission" is a number on the artifact's
    face rather than a judgment nobody recorded.
    """
    masked = spec
    used = 0
    tokens_exempt = 0
    for item in approved:
        text = item.strip()
        if not text or text not in masked:
            continue
        used += 1
        tokens_exempt += len(tokens(text))
        masked = masked.replace(text, " ")
    return masked, used, tokens_exempt


def check(
    spec: str,
    corpus: dict[str, str],
    proper_nouns: Iterable[str] = (),
    approved: Sequence[str] = (),
) -> Report:
    checked, used, tokens_exempt = apply_approved(spec, approved)
    findings = (
        containment_findings(checked, corpus, NGRAM)
        + jaccard_findings(checked, corpus, JACCARD_MAX)
        # The identifier scan runs on the ORIGINAL spec, never the masked one.
        # An approved string is exempt from CONTAINMENT — the firm said keep it —
        # and that says nothing about whether someone approved a string with a
        # claimant's name in it. Exempting it from both checks at once would let
        # one approval carry two very different permissions.
        + identifier_findings(spec, proper_nouns)
    )
    import hashlib

    return Report(
        clean=not findings,
        ngram=NGRAM,
        jaccard_max=JACCARD_MAX,
        spec_sha256=hashlib.sha256(spec.encode()).hexdigest(),
        corpus_docs=len(corpus),
        corpus_tokens=sum(len(tokens(d)) for d in corpus.values()),
        findings=findings,
        sweep=sweep(checked, corpus),
        approved_used=used,
        approved_tokens=tokens_exempt,
    )


# --------------------------------------------------------------------------- #
# cli                                                                          #
# --------------------------------------------------------------------------- #


def load_corpus(paths: Sequence[Path]) -> dict[str, str]:
    """Corpus JSONL from the read-in-place bridge, or plain files."""
    corpus: dict[str, str] = {}
    for path in paths:
        if path.suffix == ".jsonl":
            for line in path.read_text().splitlines():
                if not line.strip():
                    continue
                obj = json.loads(line)
                corpus[str(obj.get("id") or f"{path.name}:{len(corpus)}")] = str(obj.get("text") or "")
        else:
            corpus[path.name] = path.read_text()
    return corpus


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", required=True, type=Path)
    ap.add_argument("--corpus", required=True, nargs="+", type=Path)
    ap.add_argument("--provenance", type=Path, help="provenance JSON from voice-fetch-corpus.py")
    ap.add_argument(
        "--approved-strings",
        type=Path,
        help="HUMAN-approved fixed strings, exempt from containment (never from the identifier scan)",
    )
    ap.add_argument("--attestation", type=Path, help="write the content-free attestation here")
    args = ap.parse_args(argv)

    spec = args.spec.read_text()
    corpus = load_corpus(args.corpus)
    if not corpus:
        print("REFUSED: empty corpus — nothing to check against", file=sys.stderr)
        return 1

    nouns: list[str] = []
    if args.provenance and args.provenance.exists():
        prov = json.loads(args.provenance.read_text())
        for doc in prov.get("documents", []):
            for key in ("matter_name", "file_name"):
                value = str(doc.get(key) or "")
                nouns.extend(_CAP_TOKEN.findall(value))

    approved: list[str] = []
    if args.approved_strings and args.approved_strings.exists():
        data = json.loads(args.approved_strings.read_text())
        approved = [str(s) for s in (data.get("approved") if isinstance(data, dict) else data) or []]

    report = check(spec, corpus, nouns, approved)
    if args.attestation:
        args.attestation.write_text(report.to_json() + "\n")

    if report.clean:
        exempt = (
            f" {report.approved_used} approved fixed string(s) exempt, {report.approved_tokens} token(s)."
            if report.approved_used
            else ""
        )
        print(
            f"CLEAN: {args.spec.name} shares no {NGRAM}-token run with "
            f"{report.corpus_docs} corpus document(s); no near-duplicate sentence; "
            f"no identifier match.{exempt}"
        )
        return 0

    # Offsets only. Never the matched text — see the module docstring.
    print(f"REFUSED: {len(report.findings)} finding(s) in {args.spec.name}", file=sys.stderr)
    for f in report.findings:
        where = f"line {f.spec_line}" if f.spec_line else f"token {f.spec_offset}"
        src = f" vs {f.corpus_doc}" if f.corpus_doc else ""
        print(f"  [{f.kind}] {where}{src}: {f.detail}", file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
