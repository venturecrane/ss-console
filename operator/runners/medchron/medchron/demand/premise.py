"""`premise`: the skeleton's Section 0, decided from the record's own documents,
for free, before anything is paid.

Section 0 exists because a complete, well-cited policy-limits demand was once
drafted on a file whose carrier had already denied coverage in writing. Every
sentence traced to the record; the DOCUMENT was still wrong. On 2026-09-24 a
matter that had settled at the limit in May was found only after a settlement
statement and $3.91 of reading. So the gate runs on what the file says, and a
failure ends the job with a short coverage report instead of a demand:

  G1  an identified carrier: the matter's insurer field, or a carrier's own
      document in the file (a claim number, "your insured").
  G2  coverage not affirmatively denied: a denial phrase in a document that is
      not behind the privilege wall FAILS.
  G4  the right instrument: a filed action (and any other class the firm lists
      in ``premise.fail_on``, e.g. an acceptance or a release) FAILS.
  G3  is a constraint on FIGURES, not on demanding: conditional limits language
      is carried to the drafter as a premise fact, never a failure.

Every other scan hit (a prior demand, litigation funding, a limits letter) is a
premise fact handed to the drafter and the gap audit before stage 4. Nothing
here is the drafter's reasoning about what coverage probably is; each line
names the document it came from.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_CONDITIONAL = re.compile(r"(?i)\blimits?\b[^.\n]{0,120}\b(if|would be|subject to confirmation)\b")


def _texts(extracted: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str]]:
    out = []
    for r in extracted:
        p = r.get("text_path")
        if p and Path(p).is_file():
            out.append((r, Path(p).read_text(encoding="utf-8", errors="replace")))
    return out


def _quote(text: str, match: re.Match[str]) -> str:
    """The sentence around a hit, with its page marker when one precedes it."""
    start = max(text.rfind("\n", 0, match.start()), match.start() - 160, 0)
    end = min(len(text), match.end() + 160)
    page = re.findall(r"\[p\.(\d+)\]", text[: match.start()])
    snippet = " ".join(text[start:end].split())
    return f"{snippet} (p. {page[-1]})" if page else snippet


_NEGATED = re.compile(r"(?i)\b(not|no|never|without)\s+(a|an|any)?\s*$")


def _phrase_hits(
    texts: list[tuple[dict[str, Any], str]], phrases: list[str], *, negatable: bool = False
) -> list[dict[str, str]]:
    """The first hit per document. ``negatable``: a phrase directly preceded by
    a negation does not count ("This letter is not a denial of coverage", a
    carrier's reservation-of-rights letter on a 9/24 trial matter, read as a
    denial by the first version of this gate)."""
    pats = [re.compile(re.escape(p), re.I) for p in phrases]
    hits = []
    for r, t in texts:
        for p in pats:
            m = next(
                (
                    m
                    for m in p.finditer(t)
                    if not (negatable and _NEGATED.search(t[max(0, m.start() - 30) : m.start()]))
                ),
                None,
            )
            if m:
                hits.append({"document": str(r.get("name")), "quote": _quote(t, m)})
                break
    return hits


def decide(
    extracted: list[dict[str, Any]], preflight: dict[str, Any], facts: dict[str, Any], prem: dict[str, Any]
) -> dict[str, Any]:
    texts = _texts(extracted)
    gates: list[dict[str, Any]] = []
    carrier_docs = _phrase_hits(texts, prem["carrier_phrases"])
    g1 = bool(facts.get("insurer")) or bool(carrier_docs)
    gates.append(
        {
            "gate": "G1 identified carrier",
            "passed": g1,
            "evidence": ([f"matter insurer field: {facts['insurer']}"] if facts.get("insurer") else [])
            + [f"{h['document']}: {h['quote']}" for h in carrier_docs[:5]],
        }
    )
    denials = _phrase_hits(texts, prem["denial_phrases"], negatable=True)
    gates.append(
        {
            "gate": "G2 coverage not denied",
            "passed": not denials,
            "evidence": [f"{h['document']}: {h['quote']}" for h in denials[:10]],
        }
    )
    fail_on = set(prem["fail_on"])
    blocking = [h for h in preflight.get("premise_hits") or [] if h["class"] in fail_on]
    gates.append(
        {
            "gate": "G4 the right instrument (pre-suit, unresolved)",
            "passed": not blocking,
            "evidence": [f"{h['class']}: {h['document']}" for h in blocking],
        }
    )
    facts_out = [
        f"{h['class']}: {h['document']}" for h in preflight.get("premise_hits") or [] if h["class"] not in fail_on
    ]
    for r, t in texts:
        m = _CONDITIONAL.search(t)
        if m:
            facts_out.append(
                f"conditional limits language (G3, a constraint on figures): {r.get('name')}: {_quote(t, m)}"
            )
    return {"passed": all(g["passed"] for g in gates), "gates": gates, "premise_facts": facts_out}


def coverage_report(job: Any, decision: dict[str, Any], today: str) -> str:
    """The deliverable when the premise fails: what the file shows, which gate
    failed, and the question the firm must answer. Markdown for the renderer."""
    lines = [
        f"# Coverage Posture Report: matter {job.matter_number}",
        "",
        f"Prepared {today} at the firm's request for a demand on this file. **No demand was drafted:** the "
        "file does not yet establish the premise a time-limited demand to the liability carrier rests on. "
        "Each line below names the document in the matter file it comes from.",
        "",
        "## The premise gates",
        "",
        "| Gate | Result | What the file shows |",
        "|---|---|---|",
    ]
    for g in decision["gates"]:
        ev = "; ".join(g["evidence"]) or "nothing in the file"
        lines.append(f"| {g['gate']} | {'Passes' if g['passed'] else 'Fails'} | {ev.replace('|', '/')} |")
    if decision["premise_facts"]:
        lines += ["", "## Other premise facts in the file", ""] + [f"- {f}" for f in decision["premise_facts"]]
    failed = [str(g["gate"]) for g in decision["gates"] if not g["passed"]]
    questions: dict[str, str] = {
        "G1 identified carrier": "which liability carrier, and under what claim number, is on this loss?",
        "G2 coverage not denied": "is the carrier's denial final, or is coverage being contested?",
        "G4 the right instrument (pre-suit, unresolved)": "is this claim still open for a pre-suit demand, "
        "given the documents listed above?",
    }
    asked = "; ".join(questions.get(g, g) for g in failed)
    lines += ["", "## The question for the firm", "", f"Before a demand can be written: {asked}"]
    return "\n".join(lines) + "\n"


def run(data: Path, job: Any, facts: dict[str, Any], prem: dict[str, Any], today: str) -> dict[str, Any]:
    extracted = [
        json.loads(line) for line in (data / "extracted.jsonl").read_text(encoding="utf-8").splitlines() if line
    ]
    preflight = json.loads((data / "preflight.json").read_text(encoding="utf-8"))
    decision = decide(extracted, preflight, facts, prem)
    (data / "premise.json").write_text(json.dumps(decision, indent=1), encoding="utf-8")
    if not decision["passed"]:
        (data / "coverage-report.md").write_text(coverage_report(job, decision, today), encoding="utf-8")
    return decision
