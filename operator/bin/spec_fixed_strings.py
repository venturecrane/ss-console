#!/usr/bin/env python3
"""Find the strings a firm repeats verbatim, so a human can approve them.

THE CORRECTION THIS IMPLEMENTS
-------------------------------
The no-verbatim rule is right about voice and wrong about boilerplate. Four of
the rehearsal corpus's adversarial letters close on a byte-identical firm
signature. That is not a construction to re-instantiate — it is institutional
form, like a letterhead — and the highest-scoring bake-off arm PARAPHRASED it,
which is the one thing that must not happen to it. A no-verbatim policy that
cannot represent boilerplate cannot represent a law firm.

So there is a fixed-string layer, and it is verbatim BY DESIGN.

WHY THIS IS NOT A HOLE IN THE LEAK CHECK
-----------------------------------------
The obvious objection is exactly right: an exemption a DERIVATION can grant
itself is not an exemption, it is a bypass. A distiller under budget pressure
would classify half the corpus as "boilerplate" and the guarantee would be gone.

So the two powers are split, and the split is the whole safety property:

    this module PROPOSES        — it detects repetition, mechanically
    a human APPROVES            — and approval is what makes a string authored
    the leak check exempts ONLY what the approved file contains

Nothing here writes the approved file. A candidate is evidence that the firm
repeats something; it is not permission to keep it. The distinction matters
because the reason a fixed string is allowed is NOT that it recurs — it is that
a person looked at it and said "yes, that is our boilerplate, keep it exactly."
Recurrence is how we find the candidates worth asking about.

TWO CATEGORIES, TWO GRANULARITIES, AND THE CORPUS TAUGHT BOTH
--------------------------------------------------------------
The first cut of this module was sentence-granular with a single length floor,
and it found NOTHING — including the exact string it was written for. Two
structural mistakes, both visible only by running it:

  * The signature close is TWO SENTENCES. Split at sentence boundaries it
    becomes two five-token halves, each under any sensible floor, and the thing
    that recurs disappears at the moment of measurement. Boilerplate is a BLOCK.
  * Section labels are TWO TOKENS. A floor set high enough to exclude ordinary
    phrasing excludes them by an order of magnitude — and they are exactly the
    category that most needs to be fixed rather than paraphrased, since a firm
    calling a section one thing rather than another is not a stylistic
    preference a production rule can reconstruct.

So:

    BLOCK  paragraph-granular, >= BLOCK_MIN_TOKENS, >= MIN_DOCS
           the signature close, a standard disclaimer, a fixed instruction
    LABEL  line-granular, <= LABEL_MAX_TOKENS, >= MIN_DOCS, header-shaped
           section headers, and only where the line is a heading rather than
           prose that happens to be short

Different floors in opposite directions, because they are different objects. A
single threshold cannot admit a ten-token close and a two-token header while
excluding "we have received your correspondence" — and a category vocabulary is
what makes each bound defensible rather than tuned until the output looks right.

MIN_DOCS is 3 for both, for the same reason the profiler's confidence floor is:
twice is a coincidence a reader can talk themselves into, and the whole point of
a proposal is that a human decides on it rather than rubber-stamping a list.

WHAT A CANDIDATE MAY NOT BE
----------------------------
A candidate carrying a digit, a currency figure, a date, or a proper noun is
DROPPED rather than proposed. A firm's boilerplate does not contain a claim
number, and a span that does is a sentence about a matter that happened to
recur — most likely because the corpus documents share a case. Proposing it
would put a real claimant's details in front of someone whose job in that moment
is to click approve.

Usage::

    python3 operator/bin/spec_fixed_strings.py --corpus <dir> \\
        --out candidates.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

_BIN = Path(__file__).resolve().parent


def _load_profile_module():
    spec = importlib.util.spec_from_file_location("_vp_fixed", _BIN / "voice_profile.py")
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError("cannot load voice_profile.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_VP = _load_profile_module()

#: A BLOCK shorter than this is ordinary professional phrasing, not boilerplate.
BLOCK_MIN_TOKENS = 6

#: A LABEL longer than this is a sentence wearing bold, not a section header.
#: The synthesis's own bound for a declared verbatim item was 12 tokens; 6 is
#: tighter because a label that long is almost certainly prose.
LABEL_MAX_TOKENS = 6

#: Fewer documents than this is a coincidence, and a proposal a human will
#: rubber-stamp is worse than no proposal.
MIN_DOCS = 3

#: A label is a HEADING, not merely a short line. Markdown bold or a heading
#: marker is the signal, because a short line of prose ("He cannot kneel.") is
#: the firm's most distinctive VOICE move and must never be frozen as a string —
#: freezing it would convert a construction the drafter should re-derive into a
#: literal it pastes.
_LABEL_SHAPE = re.compile(r"\A(?:#{1,6}\s+\S|\*\*[^*]+\*\*\s*\Z|__[^_]+__\s*\Z)")

#: A candidate matching any of these is dropped, never proposed. See the module
#: docstring: boilerplate does not carry a claim number, and a span that does is
#: a sentence about a matter.
#:
#: MOST SPECIFIC FIRST. The generic digit probe matches every currency figure and
#: most dates, so listing it first would report "carries a digit" for all three
#: and make the dropped-list useless for deciding whether a drop was right.
_DISQUALIFIERS: tuple[tuple[str, str], ...] = (
    (r"\$", "carries a currency figure"),
    (
        r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\b",
        "carries a date",
    ),
    (r"\d", "carries a digit"),
)

_PROPER_NOUN = re.compile(r"(?<![.!?]\s)(?<!\A)\b[A-Z][a-z]{2,}\b")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

#: Emphasis and heading markers, stripped BEFORE the proper-noun test.
#:
#: Without this the test reads a heading's first word as mid-sentence — the `**`
#: sits at position zero, so "What" in "**What happened**" is not
#: start-of-string and scores as a name. Every section header in the corpus was
#: dropped that way, which is precisely the category the fixed-string layer
#: exists for: a firm calling a section "What it cost" rather than "Damages" is
#: not a preference a production rule can reconstruct. A heading naming a real
#: party still drops, because the test runs on the stripped text and finds the
#: name in a position that is genuinely mid-phrase.
_MARKUP = re.compile(r"(\*\*|__|\A#{1,6}\s+)")


@dataclass
class Candidate:
    """A span the firm repeats, offered for approval. Never self-approved."""

    text: str
    category: str
    tokens: int
    doc_count: int
    docs: list[str]

    def render(self) -> str:
        return (
            f'  [{self.category}] "{self.text}"\n'
            f"      {self.tokens} tokens, {self.doc_count} documents: {', '.join(self.docs)}"
        )


#: Abbreviations whose period is NOT a sentence end.
#:
#: Without this the proper-noun test cannot see a surname in the form legal
#: correspondence actually uses. "Ms. Duarte" puts a period-plus-space directly
#: before "Duarte", so the sentence-initial lookbehind excludes it and the
#: surname reads as an ordinary capitalized first word. Every honorific in the
#: corpus hides the name that follows it, which is the one thing this
#: disqualifier exists to catch.
_ABBREV = re.compile(
    r"\b(Mr|Mrs|Ms|Dr|Prof|Hon|Jr|Sr|St|Ave|Inc|LLP|LLC|Co|Corp|No|vs|v)\.\s",
    re.IGNORECASE,
)


def _disqualify(text: str) -> str | None:
    for pattern, reason in _DISQUALIFIERS:
        if re.search(pattern, text):
            return reason
    # Neutralize abbreviation periods before the proper-noun test, so an
    # honorific cannot smuggle the surname after it past the check.
    probe = _ABBREV.sub(lambda m: m.group(1) + "· ", _MARKUP.sub("", text).strip())
    if _PROPER_NOUN.search(probe):
        return "carries a proper noun"
    return None


def candidates(corpus: dict[str, str]) -> tuple[list[Candidate], list[tuple[str, str]]]:
    """Repeated blocks and labels worth asking a human about, plus what was dropped.

    Paragraph-granular for blocks, because boilerplate is a block and splitting
    it at sentence boundaries destroys the very thing being looked for. Whole
    lines for labels, because a section header is a line.
    """
    blocks: dict[str, set[str]] = defaultdict(set)
    labels: dict[str, set[str]] = defaultdict(set)

    for doc, text in corpus.items():
        prose, _ = _VP.segment(text, doc)
        for para in prose.split("\n\n"):
            clean = " ".join(para.split())
            if not clean:
                continue
            if _LABEL_SHAPE.match(clean):
                labels[clean].add(doc)
            else:
                blocks[clean].add(doc)

    found: list[Candidate] = []
    dropped: list[tuple[str, str]] = []

    def consider(text: str, docs: set[str], category: str, ok_length: bool) -> None:
        if len(docs) < MIN_DOCS or not ok_length:
            return
        n = len(_VP.words(text))
        reason = _disqualify(text)
        if reason:
            # Report the REASON and the shape, never the span itself — the same
            # rule the leak check follows, and for the same reason: a dropped
            # candidate is dropped because it carries matter content.
            dropped.append((f"<{category}, {n} tokens, {len(docs)} documents>", reason))
            return
        found.append(Candidate(text=text, category=category, tokens=n, doc_count=len(docs), docs=sorted(docs)))

    for text, docs in blocks.items():
        consider(text, docs, "block", len(_VP.words(text)) >= BLOCK_MIN_TOKENS)
    for text, docs in labels.items():
        consider(text, docs, "label", len(_VP.words(text)) <= LABEL_MAX_TOKENS)

    found.sort(key=lambda c: (c.category, -c.doc_count, -c.tokens))
    return found, dropped


def approved_strings(path: Path | None) -> list[str]:
    """Load the HUMAN-approved fixed strings. Never written by this module."""
    if not path or not path.exists():
        return []
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        data = data.get("approved") or []
    return [str(s) for s in data if str(s).strip()]


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True, nargs="+", type=Path)
    ap.add_argument("--out", type=Path, help="write candidates as JSON for the approval step")
    args = ap.parse_args(argv)

    corpus = _VP.load_corpus(args.corpus)
    if not corpus:
        print("REFUSED: empty corpus", file=sys.stderr)
        return 1

    found, dropped = candidates(corpus)

    if args.out:
        args.out.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "block_min_tokens": BLOCK_MIN_TOKENS,
                    "label_max_tokens": LABEL_MAX_TOKENS,
                    "min_docs": MIN_DOCS,
                    "candidates": [asdict(c) for c in found],
                    "dropped": [{"shape": s, "reason": r} for s, r in dropped],
                    "approved": [],
                },
                indent=2,
            )
            + "\n"
        )

    print(f"{len(found)} candidate fixed string(s) across {len(corpus)} documents:\n")
    for c in found:
        print(c.render())
    if dropped:
        print(f"\n{len(dropped)} repeated span(s) DROPPED rather than proposed:")
        for shape, reason in dropped:
            print(f"  {shape}: {reason}")
    print(
        "\nNOTHING HERE IS APPROVED. These are strings the firm repeats, which is"
        "\nevidence worth asking about, not permission to keep them. A string"
        "\nbecomes exempt from the leak check only by appearing in the `approved`"
        "\nlist of a file a person edited."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
