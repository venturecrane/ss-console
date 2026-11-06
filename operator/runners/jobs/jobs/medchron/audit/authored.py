"""SMD's own sentences, attested before the audit reads them.

SMD corrects a built chronology by hand in ``drops.json`` (``citation_edits``:
each ``find`` replaced by its ``replace``), and the strip applies the edits
before the audit runs (``stages/strip.py``). The audit then graded SMD's
sentences like the model's, and its repair rewrote the ones it could not
support, toward the transcription SMD had just corrected.

WHY (2026-10-23). After four review rounds SMD wrote 133 corrections into a
client chronology. The audit and its repair, running after the strip,
altered 21 of them (a $0 replay of that audit), and one repair put in a sentence that attributed a
finding to the wrong provider; it reached the firm and stood for about 21
minutes until a post-delivery fix took it out. A person had read the page and
written the sentence. A second reading by the model is not a check on that:
it is the reading SMD's edit corrected.

So every claim of the live document whose text SMD wrote gets one audit row,
``SUPPORTED``, attested by SMD (``mode: "smd"``), as ``update_decide`` writes
for an update SMD corrected (#3615). The audit's resume skips a key with a
row, the repair settles only claims whose last verdict is not SUPPORTED, and
the gate reads the row like any other, so the loop never rewrites SMD's
text, and the row says whose verdict it is.

A claim is SMD's when its text is a claim text of a replacement, cut the way
the audit cuts claims (``claims.extract_claims``), on the same exhibit: equal,
or ending with it when the edit began mid-paragraph (then the replacement's
claim text must be at least ``claims.MIN_CHARS`` long, so a short value cannot
attest a stranger's sentence that happens to end the same way). Page numbers
are not compared: the strip renumbers them after the edit.

A replacement that carries no citation corrects words inside a sentence (a
provider, a value, a phrase) and leaves the model's citation in place. Its
claim is the one whose exact span contains the whole replacement, again only
when the replacement is at least ``claims.MIN_CHARS`` long. In a $0 replay of
the client job's audit, 10 of the 21 rewritten corrections were of this kind,
and matching cited claim texts alone left them to the repair.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import claims as CL

MODE = "smd"


def _flat(text: str) -> str:
    return " ".join(text.split())


def authored_texts(edits: list[dict[str, Any]]) -> set[tuple[int, str]]:
    """(exhibit, claim text) of every claim the replacements carry."""
    out: set[tuple[int, str]] = set()
    for e in edits:
        rep = str(e.get("replace") or "")
        keep = {int(m.group(1)) for m in CL.CITE.finditer(rep)}
        for c in CL.extract_claims(rep, keep):
            out.add((int(c["exhibit"]), _flat(c["claim"])))
    return out


def inline_texts(edits: list[dict[str, Any]]) -> set[str]:
    """The text of every replacement with no citation in it, long enough to
    name one sentence."""
    out: set[str] = set()
    for e in edits:
        rep = _flat(str(e.get("replace") or ""))
        if len(rep) >= CL.MIN_CHARS and not CL.CITE.search(rep):
            out.add(rep)
    return out


def is_authored(
    claim: dict[str, Any], texts: set[tuple[int, str]], span: str = "", inline: frozenset[str] | set[str] = frozenset()
) -> bool:
    ex, seg = int(claim["exhibit"]), _flat(claim["claim"])
    for t_ex, t in texts:
        if t_ex != ex:
            continue
        if seg == t or (len(t) >= CL.MIN_CHARS and seg.endswith(t)):
            return True
    flat = _flat(span)
    return any(t in flat for t in inline)


def attest(results: Path, doc_text: str, keep: set[int], edits: list[dict[str, Any]], source: str) -> int:
    """Append an SMD-attested SUPPORTED row for each live claim SMD wrote that
    does not already carry one as its last verdict. Returns how many."""
    texts, inline = authored_texts(edits), inline_texts(edits)
    if not texts and not inline:
        return 0
    body = CL.body_of(doc_text)
    sha = CL.doc_sha_of(body)
    spans = CL.claim_spans(body, keep)
    latest = CL.latest_real(CL.read_rows(results), set(spans))
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    n = 0
    for c in CL.extract_claims(body, keep):
        if not is_authored(c, texts, spans.get(c["key"], ""), inline):
            continue
        last = latest.get(c["key"]) or {}
        if last.get("mode") == MODE and last.get("verdict") == "SUPPORTED":
            continue
        row = {
            "key": c["key"],
            "kind": "real",
            "exhibit": c["exhibit"],
            "page_spec": c["page_spec"],
            "pages": CL.parse_pages(c["page_spec"]) or [1],
            "claim": c["claim"][:500],
            "verdict": "SUPPORTED",
            "note": f"written by SMD against the page ({source}); the audit does not re-grade it",
            "mode": MODE,
            "smd": {"by": "SMD", "source": source, "at": at},
            "doc_sha": sha,
            "ts": at,
        }
        CL.append_row(results, row)
        latest[c["key"]] = row
        n += 1
    return n
