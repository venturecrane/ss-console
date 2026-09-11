#!/usr/bin/env python3
"""Run a voice spec's own rules against the firm's own writing.

THE MECHANISM THIS IMPLEMENTS
------------------------------
A distilled spec asserts rules about how a firm writes. Some of those rules are
wrong, and the wrong ones are not obviously wrong — they are plausible
generalizations from a corpus that mostly supports them. The 2026-08-01 bake-off
found a candidate card whose own load-bearing threshold was violated by three of
the firm's letters, stated as a hard gate, with nothing anywhere that would have
noticed.

The check is almost embarrassingly direct: **run the rules against the documents
they were derived from.** A rule that blocks output the firm itself produces is
not a description of the firm's voice. It is a description of most of the firm's
voice, promoted past its evidence.

WHY 100% AND NOT 90%
---------------------
A `block` rule must hold on EVERY exemplary document, not most. At corpus sizes
that are realistic here — a dozen letters, sometimes fewer — a 90% threshold
tolerates exactly one falsifying document, and the falsifying document is the
one carrying the information. Tolerance calibrated as a percentage silently
converts "the firm does this differently sometimes" into "the firm does this",
which is the error the whole exercise exists to avoid.

DEMOTION IS NOT FAILURE
------------------------
A `block` rule that fails auto-demotes to `warn` and carries its counterexamples
forward into the card. That is deliberately not an error: an inconsistency in a
firm's own writing is INFORMATION, and the person who can resolve it is the
firm. Refusing the whole spec would throw that away; silently dropping the rule
would hide it. Demoting it and naming the documents puts the disagreement in
front of the only party who can settle whether it is house style, drift, or two
authors who genuinely differ.

WHAT `exemplary` MEANS, AND WHY THE CUSTOMER LABELS IT
-------------------------------------------------------
Only documents the customer marked `exemplary` gate a rule. Nothing in a
filename says whether a letter is house style or an associate's off-day, and an
agent that averages the two produces a spec describing neither. The label is
human-supplied for the same reason the fixed strings are: it is a judgment about
the firm's own intent, and there is no evidence in the corpus that settles it.
An unlabeled document is checked and REPORTED but does not gate, so a corpus
nobody has labeled yet still yields useful signal without silently promoting
drift into a rule.

Usage::

    python3 operator/bin/spec_selftest.py --rules rules.json \\
        --corpus <dir> [--labels labels.json] [--out gate.json]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence

_BIN = Path(__file__).resolve().parent


def _load_profile_module():
    """Import voice_profile by path, so the measurement code is shared not copied.

    Registered in ``sys.modules`` BEFORE ``exec_module`` because ``@dataclass``
    resolves its own module out of ``sys.modules`` during class creation, and a
    module that is not there yet raises on the first decorated class. Cheap
    mistake, obscure traceback.
    """
    spec = importlib.util.spec_from_file_location("_vp_selftest", _BIN / "voice_profile.py")
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError("cannot load voice_profile.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_VP = _load_profile_module()

BLOCK = "block"
WARN = "warn"


@dataclass
class RuleResult:
    """One rule, checked against every document it claims to describe."""

    rule_id: str
    kind: str
    tier_declared: str
    tier_effective: str
    passed_docs: list[str] = field(default_factory=list)
    failed_docs: list[str] = field(default_factory=list)
    failed_exemplary_docs: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def demoted(self) -> bool:
        return self.tier_declared == BLOCK and self.tier_effective == WARN


def _absence_hits(prose: str, pattern: str, flags: int) -> int:
    return len(re.compile(pattern, flags).findall(prose))


def check_rule(rule: dict, prose_by_doc: dict[str, str], exemplary: set[str]) -> RuleResult:
    """Evaluate one rule against every document, then decide its effective tier.

    Rule kinds are deliberately few. Each maps to something the profiler already
    computes, because a rule the profiler cannot recompute is a rule whose
    threshold nobody can check later — and a gate whose threshold cannot be
    recomputed is not a gate.
    """
    rule_id = str(rule.get("id") or "?")
    kind = str(rule.get("kind") or "")
    declared = str(rule.get("tier") or WARN).lower()
    result = RuleResult(rule_id=rule_id, kind=kind, tier_declared=declared, tier_effective=declared)

    for doc, prose in sorted(prose_by_doc.items()):
        ok = True
        if kind == "absence":
            hits = _absence_hits(
                prose, str(rule.get("pattern") or "(?!)"), re.IGNORECASE if rule.get("ignore_case") else 0
            )
            ok = hits == 0
        elif kind in {"min_pct_short_sentences", "max_mean_sentence_words", "max_sentence_words"}:
            lengths = [len(_VP.words(s)) for s in _VP.sentences(prose)]
            lengths = [n for n in lengths if n]
            if not lengths:
                continue
            if kind == "min_pct_short_sentences":
                pct = 100 * sum(1 for n in lengths if n <= int(rule.get("at_most_words", 5))) / len(lengths)
                ok = pct >= float(rule.get("threshold", 0))
                result.detail = f"threshold >= {rule.get('threshold')}%"
            elif kind == "max_mean_sentence_words":
                ok = (sum(lengths) / len(lengths)) <= float(rule.get("threshold", 1e9))
                result.detail = f"threshold <= {rule.get('threshold')} mean words"
            else:
                ok = max(lengths) <= float(rule.get("threshold", 1e9))
                result.detail = f"threshold <= {rule.get('threshold')} words"
        else:
            # An unknown kind is REPORTED, never silently passed. A rule this
            # module cannot evaluate must not read as one it evaluated and
            # approved — that is the shape of every false-confidence defect in
            # this subsystem.
            result.tier_effective = WARN
            result.detail = f"unknown rule kind {kind!r} — not evaluated, demoted"
            return result

        (result.passed_docs if ok else result.failed_docs).append(doc)
        if not ok and doc in exemplary:
            result.failed_exemplary_docs.append(doc)

    if declared == BLOCK and result.failed_exemplary_docs:
        result.tier_effective = WARN
    return result


def selftest(
    rules: Sequence[dict],
    corpus: dict[str, str],
    exemplary: set[str] | None = None,
) -> dict:
    """Check every rule against the corpus and return the effective gate."""
    prose_by_doc = {doc: _VP.segment(text, doc)[0] for doc, text in corpus.items()}
    # No labels supplied ⇒ every document gates. That is the stricter reading
    # and the safer default: it can only demote a rule that would otherwise have
    # blocked the firm's own writing.
    marks = exemplary if exemplary is not None else set(prose_by_doc)

    results = [check_rule(r, prose_by_doc, marks) for r in rules]
    demoted = [r for r in results if r.demoted]
    return {
        "schema_version": 1,
        "corpus_docs": len(prose_by_doc),
        "exemplary_docs": sorted(marks),
        "rules_checked": len(results),
        "rules_demoted": len(demoted),
        "results": [asdict(r) | {"demoted": r.demoted} for r in results],
    }


def render_demotions(report: dict) -> str:
    """The lines that belong in the card, in front of the customer.

    ADR 0083 already requires the customer to approve a spec. What it does not
    require, and what this supplies, is that they are shown where their OWN
    letters break the rule they are approving.
    """
    lines: list[str] = []
    for r in report["results"]:
        if not r["demoted"]:
            continue
        docs = ", ".join(r["failed_exemplary_docs"])
        lines.append(
            f"- `{r['rule_id']}` demoted from block to warn: your own writing does not "
            f"follow it in {docs}. {r['detail']}".rstrip()
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rules", required=True, type=Path)
    ap.add_argument("--corpus", required=True, nargs="+", type=Path)
    ap.add_argument("--labels", type=Path, help='JSON: {"exemplary": ["01-...md", ...]}')
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)

    corpus = _VP.load_corpus(args.corpus)
    if not corpus:
        print("REFUSED: empty corpus", file=sys.stderr)
        return 1

    rules = json.loads(args.rules.read_text())
    if isinstance(rules, dict):
        rules = rules.get("rules", [])

    exemplary = None
    if args.labels and args.labels.exists():
        exemplary = set(json.loads(args.labels.read_text()).get("exemplary") or [])

    report = selftest(rules, corpus, exemplary)
    if args.out:
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    print(f"checked {report['rules_checked']} rule(s) against {report['corpus_docs']} document(s)")
    if report["rules_demoted"]:
        print(f"\n{report['rules_demoted']} rule(s) DEMOTED — the firm's own writing breaks them:\n")
        print(render_demotions(report))
        print("\nDemotion is not an error. Show these to the firm; they are the only")
        print("party who can say whether this is house style, drift, or two authors.")
    else:
        print("no demotions: every block-tier rule holds on all exemplary documents")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
