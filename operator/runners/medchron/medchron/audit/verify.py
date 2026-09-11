"""One verdict: does the cited page support the claim? Image mode sends the
page pictures; text mode sends a cached window of native page text and a
per-claim tail. Both go through the doorway (the frozen image path built its
own SDK request, and its retry policy, effort, cache markers and ledger row
all differed from every other stage).

The tool result is PARSED, never assumed: 'required' in a schema is not a
guarantee the field arrives, and a verdict without 'note' once killed a run
at 104 of 585 claims.
"""

from __future__ import annotations

from typing import Any

from .. import llm

VERDICTS = ("SUPPORTED", "PARTIAL", "UNSUPPORTED", "PAGE_UNREADABLE")

VERDICT_TOOL = {
    "name": "record_verdict",
    "description": "Record whether the cited page images support the claim.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": list(VERDICTS)},
            "unsupported_assertions": {"type": "array", "items": {"type": "string"}},
            "contradictions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Assertions the pages CONTRADICT. Most serious category.",
            },
            "note": {"type": "string", "description": "One sentence: what the pages actually are."},
        },
        "required": ["verdict", "unsupported_assertions", "contradictions", "note"],
    },
}
VERDICT_TOOL_TEXT = {
    "name": "record_verdict",
    "description": "Record whether the cited page text supports the claim.",
    "input_schema": {
        "type": "object",
        "properties": {
            **VERDICT_TOOL["input_schema"]["properties"],
            "supporting_pages": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Page numbers in the window where the supporting text was found. Empty if none.",
            },
        },
        "required": VERDICT_TOOL["input_schema"]["required"] + ["supporting_pages"],
    },
}
TOOL_CHOICE = {"type": "tool", "name": "record_verdict"}

PROMPT = """You are auditing a medical chronology prepared for a law firm.

Below is a CLAIM taken from the chronology, and the page image(s) it cites as its
source. Determine whether the pages actually support the claim.

Be strict and literal. This audit exists to catch claims that drifted from their
source, so a claim that is merely plausible for this kind of record is NOT supported
unless you can see it on the page. Check specific values exactly: doses, vital signs,
dates, measurements, names, laterality (left vs right). A contradicted value is far
more serious than a missing one - report those under contradictions.

Do not be generous. If you cannot find an assertion on these pages, list it.

CLAIM (cited to {cite}):
\"\"\"
{claim}
\"\"\"

Call record_verdict with your findings."""

SYSTEM_TEXT = """You are auditing a medical chronology prepared for a law firm.

The user message carries the TEXT of a window of consecutive pages from one
exhibit (each page under a "===== Exhibit N p.K =====" header), then a CLAIM
taken from the chronology and the page(s) it cites. Determine whether the
CITED pages actually support the claim.

Be strict and literal. This audit exists to catch claims that drifted from their
source, so a claim that is merely plausible for this kind of record is NOT supported
unless you can see it in the page text. Check specific values exactly: doses, vital
signs, dates, measurements, names, laterality (left vs right). A contradicted value
is far more serious than a missing one - report those under contradictions.

The verdict is about the CITED page(s), judged AS ONE SOURCE. The claim's
cited set is listed explicitly under "CITED PAGES"; the union of those pages
is the source. An assertion found on ANY page in the cited set is supported,
whichever cited page carries it, and the claim never has to say which cited
page holds which detail. Only pages outside the cited set are uncited: if an
assertion is supported only by an uncited window page, do not count it as
supported, but list that page in supporting_pages so the citation can be
corrected. Always list in supporting_pages every page where you found
supporting text. Before you call an assertion unsupported, re-read the CITED
PAGES list and check every page on it.

Do not be generous. If you cannot find an assertion anywhere in the cited set,
list it. Call record_verdict with your findings."""

CLAIM_TAIL = """CITED PAGES (judge against these as one set): {cited}

CLAIM (cited to {cite}):
\"\"\"
{claim}
\"\"\"

CITED PAGES again: {cited}. Values to look for: {anchors}.

Call record_verdict with your findings."""


def normalize(v: Any) -> dict[str, Any]:
    v = v if isinstance(v, dict) else {}
    verdict = v.get("verdict")
    if verdict not in VERDICTS:
        verdict = "PAGE_UNREADABLE"

    def strlist(x: Any) -> list[str]:
        return [str(i) for i in x] if isinstance(x, list) else []

    return {
        "verdict": verdict,
        "unsupported_assertions": strlist(v.get("unsupported_assertions")),
        "contradictions": strlist(v.get("contradictions")),
        "note": str(v.get("note") or "(no note returned)"),
    }


def normalize_text(v: Any) -> dict[str, Any]:
    out = normalize(v)
    pages: list[int] = []
    if isinstance(v, dict) and isinstance(v.get("supporting_pages"), list):
        for x in v["supporting_pages"]:
            try:
                pages.append(int(x))
            except (TypeError, ValueError):
                continue
    out["supporting_pages"] = sorted(set(pages))
    return out


def _tool_input(message: Any) -> Any:
    for b in getattr(message, "content", None) or []:
        if getattr(b, "type", None) == "tool_use":
            return getattr(b, "input", None)
    return None


def verify_image(
    doorway: llm.Doorway, model: str, claim: str, images: list[dict], cite_label: str, custom_id: str | None = None
) -> dict[str, Any]:
    r = doorway.call(
        "audit",
        model=model,
        max_tokens=4000,
        thinking={"type": "adaptive"},
        tools=[VERDICT_TOOL],
        tool_choice=TOOL_CHOICE,
        cache_blocks=(),
        messages=[
            {
                "role": "user",
                "content": [*images, {"type": "text", "text": PROMPT.format(cite=cite_label, claim=claim)}],
            }
        ],
        custom_id=custom_id,
    )
    got = _tool_input(r.message)
    if got is None:
        return {
            "verdict": "PAGE_UNREADABLE",
            "unsupported_assertions": [],
            "contradictions": [],
            "note": "no tool call returned",
        }
    return normalize(got)


def text_request(window_block: str, claim: str, cited: list[int], anchors: list[str], cite_label: str) -> list[dict]:
    """Window block first (the cached prefix, content index 0), claim tail
    last (unique per call, never marked)."""
    tail = CLAIM_TAIL.format(
        cite=cite_label,
        claim=claim,
        cited=", ".join(f"p.{p}" for p in cited),
        anchors=", ".join(a.split(":", 1)[1] for a in anchors) or "(none extracted)",
    )
    return [{"type": "text", "text": window_block}, {"type": "text", "text": tail}]


def text_verdict(raw: Any, cited: list[int]) -> dict[str, Any]:
    """SUPPORTED means the cited pages support the claim; support found only
    on uncited window pages is SUPPORTED_WIDENED."""
    v = normalize_text(raw)
    if v["verdict"] == "SUPPORTED" and v["supporting_pages"] and not set(v["supporting_pages"]) & set(cited):
        v["verdict"] = "SUPPORTED_WIDENED"
    return v


def verify_text(
    doorway: llm.Doorway,
    model: str,
    claim: str,
    window_block: str,
    cited: list[int],
    anchors: list[str],
    cite_label: str,
    custom_id: str | None = None,
) -> dict[str, Any]:
    r = doorway.call(
        "audit",
        model=model,
        system=SYSTEM_TEXT,
        messages=[{"role": "user", "content": text_request(window_block, claim, cited, anchors, cite_label)}],
        max_tokens=4000,
        tools=[VERDICT_TOOL_TEXT],
        tool_choice=TOOL_CHOICE,
        thinking={"type": "adaptive"},
        cache_blocks=("user:0",),
        custom_id=custom_id,
    )
    got = _tool_input(r.message)
    if got is None:
        return {
            "verdict": "PAGE_UNREADABLE",
            "unsupported_assertions": [],
            "contradictions": [],
            "note": "no tool call returned",
            "supporting_pages": [],
        }
    return text_verdict(got, cited)
