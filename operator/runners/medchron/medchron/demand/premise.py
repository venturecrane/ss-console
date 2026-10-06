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


_NEGATED = re.compile(
    r"(?i)\b(not|no|never|without|cannot|can ?not|unable to|decline to|not able to|will not|won't)\s+"
    r"(a|an|any|be|to)?\s*(\w+\s+)?$"
)


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


_BILL_OR_RECORD = re.compile(
    r"(?i)\b(bill|billing|statement|ledger|itemi[sz]|ub-?04|hcfa|cms-?1500|invoice|medical record|chart|"
    r"retainer|intake|authori[sz]ation|hipaa)\b"
)
_MEDICAL_CONTENT = re.compile(
    r"(?i)\b(chief complaint|diagnos[ie]s|date of service|dates? of service|cpt|icd-?10|patient (name|account|id)|"
    r"amount due|balance due|guarantor|medical record (number|no)|mrn|statement date|charges)\b"
)


def medical_by_content(text: str) -> bool:
    """A bill or a medical record, by what its first pages SAY: two or more
    distinct clinical or billing terms. A file named "scan 3-1.pdf" is still
    a bill when it reads like one (review of #3074)."""
    return len({m.group(1).lower() for m in _MEDICAL_CONTENT.finditer(text[:4000])}) >= 2


_CARRIER_SIGNAL = re.compile(
    r"(?i)\b(our insured|your client|claims? (adjuster|representative|specialist|professional|department)|"
    r"insurance (company|group|services|exchange)|mutual (insurance|automobile)|casualty (company|insurance))\b"
)
_SIGNOFF = r"(?:cordially|sincerely|very truly yours|respectfully(?: yours| submitted)?)[,\s]{0,60}"


def firm_authored(r: dict[str, Any], text: str, prem: dict[str, Any]) -> bool:
    """A document the firm wrote: an email sent from the firm's domain, or a
    letter signed off over the firm's own signature line. The firm's prior
    demand quotes "accept the policy limits" at the carrier; it is not an
    acceptance (review of #3074). A carrier's letter ADDRESSED to the firm
    names the firm too, so the test is the sign-off, never the name alone."""
    domains = tuple(prem.get("_firm_domains") or ())
    if r.get("kind") == "email_body":
        m = re.search(r"(?im)^From:\s*(\S+@(\S+))", text)
        return bool(m and m.group(2).lower().strip(">") in domains)
    sig = str(prem.get("_firm_signature") or "")
    return bool(sig) and re.search(_SIGNOFF + re.escape(sig), text, re.I) is not None


_CARRIER_WORD = re.compile(r"(?i)\b(insured|adjuster|claims? (representative|specialist|adjuster|department))\b")
_IDENT = re.compile(r"[\s:#.]*([A-Z0-9][A-Z0-9-]{3,})", re.I)


def carrier_documents(texts: list[tuple[dict[str, Any], str]], phrases: list[str]) -> list[dict[str, str]]:
    """G1's evidence: a carrier's OWN document. Each must (1) not be a bill, a
    medical record, or the firm's intake paperwork, by name; (2) use a carrier's
    vocabulary ("your insured", "adjuster"); and (3) carry a claim or policy
    phrase followed by an identifier with a digit in it. A medical bill's
    "claim number" or a retainer's blank "CLAIM NUMBER:" field is not a carrier
    (review of #3074; the blank field passed G1 on a 9/24 trial matter)."""
    pats = [re.compile(re.escape(p), re.I) for p in phrases]
    out = []
    for r, t in texts:
        if not _CARRIER_WORD.search(t) or _BILL_OR_RECORD.search(str(r.get("name") or "")):
            continue
        # A carrier's letter that discusses "dates of service" and "charges"
        # is still the carrier's letter: its own signals win over medical words.
        if medical_by_content(t) and not _CARRIER_SIGNAL.search(t[:4000]):
            continue
        for p in pats:
            m = next(
                (m for m in p.finditer(t) if (i := _IDENT.match(t, m.end())) and re.search(r"\d", i.group(1))), None
            )
            if m:
                out.append({"document": str(r.get("name")), "quote": _quote(t, m)})
                break
    return out


def variant(
    texts: list[tuple[dict[str, Any], str]], preflight: dict[str, Any], facts: dict[str, Any], prem: dict[str, Any]
) -> dict[str, Any]:
    """Pre-suit or litigation, from the file, never guessed (review of #3074):

    * litigation: defense counsel of record on the matter, or a court
      document's own words in the file (a complaint, a case number);
    * pre-suit: neither of those, and no document NAME suggesting a lawsuit;
    * unclear: a name suggests a lawsuit but no court document or counsel
      confirms it. The job holds and says why."""
    counsel = list(facts.get("defense_counsel") or [])
    court = _phrase_hits(texts, prem["litigation_phrases"])
    named = [h["document"] for h in preflight.get("premise_hits") or [] if h["class"] == "lawsuit"]
    evidence = [f"defense counsel of record: {c}" for c in counsel] + [
        f"{h['document']}: {h['quote']}" for h in court[:5]
    ]
    if counsel or court:
        return {"variant": "litigation", "evidence": evidence}
    if named:
        return {
            "variant": "unclear",
            "evidence": [f"a document name suggests a lawsuit: {n}" for n in named[:5]]
            + ["no court document's text and no defense counsel of record confirms it"],
        }
    return {"variant": "pre_suit", "evidence": ["no court document and no defense counsel of record in the file"]}


def decide(
    extracted: list[dict[str, Any]], preflight: dict[str, Any], facts: dict[str, Any], prem: dict[str, Any]
) -> dict[str, Any]:
    texts = _texts(extracted)
    gates: list[dict[str, Any]] = []
    carrier_docs = carrier_documents(texts, prem["carrier_phrases"])
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
    blocking = [f"{h['class']}: {h['document']}" for h in preflight.get("premise_hits") or [] if h["class"] in fail_on]
    # Names alone miss an acceptance filed as "letter 5-5-26.pdf": the TEXT is
    # read for the settlement phrases too, negation-aware (review of #3074).
    blocking += [
        f"settled, in the text: {h['document']}: {h['quote']}"
        for h in _phrase_hits(
            [(r, t) for r, t in texts if not firm_authored(r, t, prem)], prem["settled_phrases"], negatable=True
        )
    ]
    gates.append(
        {"gate": "G4 the right instrument (pre-suit, unresolved)", "passed": not blocking, "evidence": blocking[:10]}
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
    return {
        "passed": all(g["passed"] for g in gates),
        "gates": gates,
        "premise_facts": facts_out,
        "variant": variant(texts, preflight, facts, prem),
    }


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
