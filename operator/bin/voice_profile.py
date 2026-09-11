#!/usr/bin/env python3
"""Compute every number a voice spec is allowed to contain.

THE PROBLEM THIS SOLVES, and it is a representation problem rather than a
diligence one
--------------------------------------------------------------------------
In a prose spec an assertion and a measurement look identical. `Count in
corpus: 0` costs one token to write, reads as computation, and is indistinguish-
able by inspection from a number somebody actually counted. The 2026-08-01
bake-off found exactly this in a candidate card: two suppression rules stated in
the same format from the same corpus, one right and one wrong, with no way for a
reader to tell which was which.

Asking an agent to be more careful does not fix that, because nothing about the
artifact records the difference. Removing the agent's ability to write a number
does. So: this module computes, the agent writes prose, and the card carries no
digits at all outside `{{profile.*}}` interpolation tokens. An agent cannot
assert a count into the system because there is no field for one.

ZONE SEGMENTATION IS NOT TIDINESS, IT IS CORRECTNESS
-----------------------------------------------------
Counts run over PROSE ZONES ONLY, and the discarded ranges are reported so the
discarding is reviewable rather than silent. The corpus proves why with a case
that would otherwise have shipped: a naive hyphen count over documents 12 and 13
returns four body hits AND four frontmatter hits, the latter from
`note: ... voice-derivation ... five-sample`. Half the apparent violations of a
hyphenation rule are the FIXTURE'S OWN METADATA describing the fixture. A rule
derived from that number would be false, and it would be false in the direction
that makes a firm's real writing look like a violation.

Letterhead, recipient block, RE/claim block, salutation, signature block and
enclosure inventory are discarded for the same reason: they are institutional
form, not authored prose, and a firm's address contributes nothing to how it
argues.

EVERY NUMBER CARRIES ITS SUPPORT
--------------------------------
No bare `0`. A rule reads `[n=11, 4 counterexamples]`, because "zero hits across
eleven documents" and "zero hits across one document" are different claims and
the second is not evidence. Anything supported by fewer than three documents is
reported but marked below the confidence floor, so a card cannot promote a
coincidence into a rule.

WHAT THIS DELIBERATELY DOES NOT DO
-----------------------------------
It does not decide anything. Which measurements become rules, and what those
rules mean, is the distilling agent's judgment and the customer's approval. This
module's only job is to make sure that when a number appears in a spec, it was
computed here and can be recomputed here. A gate whose threshold cannot be
recomputed is not a gate.

Usage::

    python3 operator/bin/voice_profile.py --corpus <dir-or-files> \\
        --out profile.json [--zones zones.json]
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence

#: A measurement supported by fewer documents than this is reported with a
#: below_floor flag and must not be promoted to a rule. Three is the smallest
#: number at which "the firm does this" is distinguishable from "one letter did".
CONFIDENCE_FLOOR_DOCS = 3

_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])[\s\n]+")
_WORD = re.compile(r"[A-Za-z0-9']+")

#: Lines that are institutional form rather than authored prose. Matched on a
#: whole stripped line so a sentence mentioning a date is never mistaken for a
#: date line.
_ZONE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "date_line",
        re.compile(
            r"\A(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\Z"
        ),
    ),
    ("re_line", re.compile(r"\A(\*\*)?(RE|Re|VIA|Our File|Your File|Claim No\.?|File No\.?)\b.*", re.IGNORECASE)),
    ("salutation", re.compile(r"\A(Dear|To|Attn\.?|Attention)\b.*[:,]\s*\Z")),
    (
        "signoff",
        re.compile(
            r"\A(Very truly yours|Yours|Sincerely|Respectfully|Cordially|Regards|Best regards)\s*[,.]?\s*\Z",
            re.IGNORECASE,
        ),
    ),
    ("address_line", re.compile(r"\A\d+\s+[A-Z][\w.'-]*(\s+[\w.'-]+)*,?\s*(Suite|Unit|Apt|#)?\s*[\w-]*\Z")),
    ("city_state_zip", re.compile(r"\A[A-Z][\w\s.'-]+,\s*(CA|California|[A-Z]{2})\s+\d{5}(-\d{4})?\Z")),
    ("phone_line", re.compile(r"\A\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\Z")),
    ("enclosure", re.compile(r"\A(Enclosure|Enclosures|cc|Encl\.?)\b.*", re.IGNORECASE)),
    ("letterhead", re.compile(r"\A\*\*[A-Z][A-Z\s&.,']+\*\*\Z")),
)


@dataclass
class Zones:
    """What was kept and what was thrown away, per document."""

    doc: str
    prose_lines: int
    discarded: dict[str, int] = field(default_factory=dict)
    discarded_line_numbers: dict[str, list[int]] = field(default_factory=dict)


@dataclass
class Measurement:
    """One number, with everything needed to judge whether to trust it."""

    key: str
    value: float | int
    unit: str
    support_docs: int
    total_docs: int
    per_doc: dict[str, float | int] = field(default_factory=dict)
    counterexample_docs: list[str] = field(default_factory=list)

    @property
    def below_floor(self) -> bool:
        return self.support_docs < CONFIDENCE_FLOOR_DOCS

    def render(self) -> str:
        """How this number must appear at point of use. Never a bare value."""
        base = f"{self.value} [n={self.support_docs}"
        if self.counterexample_docs:
            base += f", {len(self.counterexample_docs)} counterexample(s)"
        if self.below_floor:
            base += ", BELOW FLOOR"
        return base + "]"


def strip_frontmatter(text: str) -> tuple[str, int]:
    """Drop YAML frontmatter, returning the body and the lines removed.

    First and least glamorous line of defence. The fixture's own `note:` field
    describes the fixture using hyphenated compounds, and counting it as the
    firm's prose inverts a hyphenation rule.
    """
    m = _FRONTMATTER.match(text)
    if not m:
        return text, 0
    return text[m.end() :], m.group(0).count("\n")


def segment(text: str, doc: str = "") -> tuple[str, Zones]:
    """Split a document into authored prose and institutional form."""
    body, fm_lines = strip_frontmatter(text)
    zones = Zones(doc=doc, prose_lines=0)
    if fm_lines:
        zones.discarded["frontmatter"] = fm_lines
        zones.discarded_line_numbers["frontmatter"] = list(range(1, fm_lines + 1))

    kept: list[str] = []
    for number, raw in enumerate(body.splitlines(), start=fm_lines + 1):
        line = raw.strip()
        if not line:
            kept.append("")
            continue
        label = next((name for name, rx in _ZONE_PATTERNS if rx.match(line)), None)
        if label:
            zones.discarded[label] = zones.discarded.get(label, 0) + 1
            zones.discarded_line_numbers.setdefault(label, []).append(number)
            continue
        kept.append(raw)
        zones.prose_lines += 1
    return "\n".join(kept), zones


def sentences(prose: str) -> list[str]:
    out: list[str] = []
    for para in prose.split("\n\n"):
        clean = " ".join(para.split())
        if not clean or clean.startswith("#"):
            continue
        out.extend(s for s in _SENTENCE_SPLIT.split(clean) if s.strip())
    return out


def words(text: str) -> list[str]:
    return _WORD.findall(text)


# --------------------------------------------------------------------------- #
# the measurements                                                            #
# --------------------------------------------------------------------------- #

#: Absence probes. Each is a thing a default register produces and a firm may
#: not — the cheapest signal to carry and the only kind that is content-free by
#: construction, since what it records is that something is NOT there.
#:
#: CASE IS PER-PROBE AND EXPLICIT. It was briefly a single special case, and the
#: bug that produced was silent in the direction that matters: the contraction
#: probe missed every SENTENCE-INITIAL contraction, so "It's on the record"
#: counted as zero. A measurement that undercounts an absence makes a firm look
#: more disciplined than it is, and the rule derived from it tells a drafter to
#: avoid something the firm actually does.
_ABSENCE_PROBES: tuple[tuple[str, str, int], ...] = (
    ("em_dash", r"—", 0),
    ("semicolon", r";", 0),
    ("exclamation", r"!", 0),
    ("rhetorical_question", r"\?", 0),
    (
        "hyphenated_compound_numeral",
        r"\b(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)-(?:one|two|three|four|five|six|seven|eight|nine)\b",
        re.IGNORECASE,
    ),
    # A CLOSED LIST, not a suffix pattern, and the difference is not pedantry.
    # `\w+'s` matches "your driver's door" — a POSSESSIVE, which every register
    # uses and which says nothing about contraction habits. Measured on this
    # corpus the naive pattern reports 71 hits across all 13 documents and the
    # precise one reports what is actually there. A rule derived from 71 would
    # have told a drafter this firm contracts freely, which inverts the truth.
    (
        "contraction",
        r"\b(?:it|that|there|he|she|what|who|let|here|they|we|you|i|"
        r"is|are|was|were|do|does|did|have|has|had|would|could|should|will|can|ai)"
        r"(?:'s|'re|'ve|'ll|'d|'m|n't)\b",
        re.IGNORECASE,
    ),
    (
        "discourse_connective",
        r"\b(?:however|moreover|furthermore|additionally|nevertheless|therefore|thus|indeed)\b",
        re.IGNORECASE,
    ),
    (
        "legalese_formula",
        r"\b(?:please be advised|pursuant to|heretofore|aforementioned|enclosed please find|at your earliest convenience|undersigned)\b",
        re.IGNORECASE,
    ),
    (
        "intensifier",
        r"\b(?:horrific|devastating|egregious|tragic|blatant|shocking|obviously|needless to say)\b",
        re.IGNORECASE,
    ),
)


def profile(corpus: dict[str, str]) -> dict:
    """Every number the system is allowed to render, with its support."""
    prose: dict[str, str] = {}
    zones: list[Zones] = []
    for doc, text in sorted(corpus.items()):
        body, z = segment(text, doc)
        prose[doc] = body
        zones.append(z)

    total = len(prose)
    measurements: list[Measurement] = []

    # --- absence probes -----------------------------------------------------
    for name, pattern, flags in _ABSENCE_PROBES:
        rx = re.compile(pattern, flags)
        per_doc = {doc: len(rx.findall(body)) for doc, body in prose.items()}
        offenders = sorted(d for d, n in per_doc.items() if n)
        measurements.append(
            Measurement(
                key=f"absence.{name}",
                value=sum(per_doc.values()),
                unit="occurrences in prose zones",
                support_docs=total - len(offenders),
                total_docs=total,
                per_doc=per_doc,
                counterexample_docs=offenders,
            )
        )

    # --- sentence shape -----------------------------------------------------
    all_lengths: list[int] = []
    per_doc_mean: dict[str, float] = {}
    per_doc_short: dict[str, float] = {}
    for doc, body in prose.items():
        lengths = [len(words(s)) for s in sentences(body)]
        lengths = [n for n in lengths if n]
        if not lengths:
            continue
        all_lengths.extend(lengths)
        per_doc_mean[doc] = round(statistics.mean(lengths), 1)
        per_doc_short[doc] = round(100 * sum(1 for n in lengths if n <= 5) / len(lengths), 1)

    if all_lengths:
        measurements.append(
            Measurement(
                key="sentence.mean_words",
                value=round(statistics.mean(all_lengths), 1),
                unit="words",
                support_docs=len(per_doc_mean),
                total_docs=total,
                per_doc=dict(per_doc_mean),
            )
        )
        measurements.append(
            Measurement(
                key="sentence.max_words",
                value=max(all_lengths),
                unit="words",
                support_docs=len(per_doc_mean),
                total_docs=total,
            )
        )
        measurements.append(
            Measurement(
                key="sentence.pct_five_or_fewer",
                value=round(100 * sum(1 for n in all_lengths if n <= 5) / len(all_lengths), 1),
                unit="percent of sentences",
                support_docs=len(per_doc_short),
                total_docs=total,
                per_doc=dict(per_doc_short),
            )
        )

    return {
        "schema_version": 1,
        "corpus_docs": total,
        "confidence_floor_docs": CONFIDENCE_FLOOR_DOCS,
        "measurements": {
            m.key: {**asdict(m), "below_floor": m.below_floor, "rendered": m.render()} for m in measurements
        },
        "zones": [asdict(z) for z in zones],
    }


# --------------------------------------------------------------------------- #
# the card invariant                                                          #
# --------------------------------------------------------------------------- #

_INTERPOLATION = re.compile(r"\{\{profile\.[a-z0-9_.]+\}\}")
_DIGIT = re.compile(r"\d")


def card_digit_violations(card: str) -> list[tuple[int, str]]:
    """Lines carrying a digit outside a ``{{profile.*}}`` token.

    The structural half of the guarantee. Without it an agent can still type a
    number into prose and it will read exactly like a computed one — which is
    the whole defect. With it, every number a drafter sees was rendered from
    `profile.json` at compile time with its support attached.

    Fenced code and HTML comments are exempt: a schema example or a provenance
    header is documentation about the card, not an assertion inside it.
    """
    violations: list[tuple[int, str]] = []
    in_fence = False
    in_comment = False
    for number, line in enumerate(card.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if "<!--" in line:
            in_comment = True
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        if in_fence:
            continue
        if _DIGIT.search(_INTERPOLATION.sub("", line)):
            violations.append((number, "digit outside a {{profile.*}} token"))
    return violations


# --------------------------------------------------------------------------- #
# cli                                                                          #
# --------------------------------------------------------------------------- #


def load_corpus(paths: Sequence[Path]) -> dict[str, str]:
    corpus: dict[str, str] = {}
    for path in paths:
        if path.is_dir():
            for f in sorted(path.glob("*.md")):
                if f.name[0].isdigit():
                    corpus[f.name] = f.read_text()
        elif path.suffix == ".jsonl":
            for line in path.read_text().splitlines():
                if line.strip():
                    obj = json.loads(line)
                    corpus[str(obj.get("id") or len(corpus))] = str(obj.get("text") or "")
        else:
            corpus[path.name] = path.read_text()
    return corpus


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True, nargs="+", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--card", type=Path, help="check the digit invariant on this card")
    args = ap.parse_args(argv)

    corpus = load_corpus(args.corpus)
    if not corpus:
        print("REFUSED: empty corpus", file=sys.stderr)
        return 1

    if args.card:
        violations = card_digit_violations(args.card.read_text())
        if violations:
            print(f"REFUSED: {len(violations)} digit(s) in {args.card.name} outside a profile token", file=sys.stderr)
            for line, why in violations[:20]:
                print(f"  line {line}: {why}", file=sys.stderr)
            return 2
        print(f"CLEAN: {args.card.name} carries no asserted numbers.")

    data = profile(corpus)
    if args.out:
        args.out.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        print(f"wrote {args.out} ({data['corpus_docs']} documents)")
    else:
        print(json.dumps(data, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
