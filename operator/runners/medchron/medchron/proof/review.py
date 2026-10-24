"""The independent pre-delivery review, lane-agnostic: findings, the model call,
the record, the hold and the release.

Generalized from ``drafting/review.py`` (the 2026-10-12 2030.050 lesson) so
every lane that hands prose or legal judgement to a firm reviews it the same
way. A lane supplies three things: its code findings (``checks`` plus its own),
its adversarial prompt, and the material (sources and the output as it would be
delivered). This module grades, records and decides.

HOLDS COME FROM A CLOSED LIST. Our own gates refusing legitimate work was 21% of
every defect recorded on the A&P seat (fixes ledger, 2026-10-13). A reviewer
that could hold on any concern it wrote would be the next such gate. So a model
finding holds only when it is graded MUST-FIX, cites where it is, AND names one
of the lane's ``hold_checks`` (the numbered checks its prompt defines as
MUST-FIX-capable). Anything else is downgraded to SHOULD-FIX and rides into the
attorney notes, where a person reads it. Code findings hold as graded: code
only grades MUST-FIX where it has settled the question.

THE HOLD IS RESUMABLE TWO WAYS, never a dead end:

* ``redo`` the review (the resume names the ``review`` stage) after the content
  is fixed: a fresh review, paid once more;
* ``release`` (``--release-review``, an SMD act through the root-only
  ``*_job_resume`` verb, audited by the broker): the job files as it stands,
  with every finding, the released ones included, in the attorney notes, and
  the record says who released past what.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

MUST, SHOULD, NOTE = "MUST-FIX", "SHOULD-FIX", "NOTE"
GRADES = (MUST, SHOULD, NOTE)
RECORD = "review.json"
# The output budget of one review call: thinking at effort "high" plus the
# findings table plus the VERDICT line all come out of it. 16,000 ran out on a
# 422-entry update (138,138 in / 16,000 out, twice, no verdict, 2026-10-14).
REVIEW_MAX_TOKENS = 64_000
# The largest budget a review escalates to: the documented streamed output
# maximum of every model a firm tiers for review (``demand.gapaudit.OUTPUT_MAX``).
REVIEW_MAX_TOKENS_CEILING = 128_000
_ROW = re.compile(r"^\s*\|\s*(MUST-FIX|SHOULD-FIX|NOTE)\s*\|(.*)\|\s*$", re.I)
_VERDICT = re.compile(r"(?im)^\s*VERDICT:\s*(\d+)\s*MUST-FIX\b")
_CHECK_NO = re.compile(r"^\s*(\d{1,2})\b")


class ReviewIncomplete(RuntimeError):
    """The model review did not finish (twice). An unreviewed document is
    never filed. ``retry``: the call returned without a verdict for a reason a
    later call can clear (no VERDICT line, a pause, an empty stop), as opposed
    to a refusal, a truncation at the ceiling, or a caller's own defect; the
    chronology lane exits infrastructure on it (stages/review.py)."""

    def __init__(self, msg: str, *, retry: bool = False) -> None:
        super().__init__(msg)
        self.retry = retry


class ReviewTruncated(ReviewIncomplete):
    """The review ran out of output at the largest budget before its VERDICT.
    The same request would stop the same way, so it is never sent again: a
    caller that can split its material reviews it in smaller parts."""


@dataclass
class Finding:
    grade: str
    check: str
    finding: str
    cite: str
    by: str  # "code" or "model"


def code(grade: str, check: str, finding: str, cite: str = "") -> Finding:
    return Finding(grade, check, finding, cite, "code")


def parse(text: str, hold_checks: Iterable[int]) -> list[Finding]:
    """The model's table rows, graded under the closed-list rule."""
    holds = set(hold_checks)
    out: list[Finding] = []
    for ln in text.splitlines():
        m = _ROW.match(ln)
        if not m:
            continue
        cells = [c.strip() for c in m.group(2).split("|")]
        cells += [""] * (3 - len(cells))
        grade = m.group(1).upper()
        check = cells[0]
        finding = " | ".join(cells[1:-1]) if len(cells) > 3 else cells[1]
        cite = cells[-1]
        n = _CHECK_NO.match(check)
        if grade == MUST and (not cite or n is None or int(n.group(1)) not in holds):
            grade = SHOULD
        out.append(Finding(grade, check, finding, cite, "model"))
    return out


def complete(text: str) -> bool:
    return bool(_VERDICT.search(text))


def _next_budget(max_tokens: int, stop: str) -> int | None:
    """The budget for the retry, or None when there is to be none. A stop at
    ``max_tokens`` retries only with MORE output (an identical request would
    stop identically); any other unfinished stop retries once as it was."""
    if stop != "max_tokens":
        return max_tokens
    bigger = min(max_tokens * 2, REVIEW_MAX_TOKENS_CEILING)
    return bigger if bigger > max_tokens else None


def run_model(
    doorway: Any,
    model: str,
    system: str,
    user: str,
    key: str,
    hold_checks: Iterable[int],
    *,
    max_tokens: int = REVIEW_MAX_TOKENS,
    stage: str = "review",
) -> list[Finding]:
    """One adversarial review call, retried once when it does not finish:
    with a larger output budget when it stopped at ``max_tokens``, never the
    identical request. Raises ``ReviewTruncated`` when the output ran out at
    the ceiling (the caller may split), ``ReviewIncomplete`` otherwise. The
    stage name is ``review`` so the job's ledger prices it apart."""
    holds = tuple(hold_checks)
    budget: int | None = max_tokens
    stop = ""
    for attempt in (1, 2):
        if budget is None:
            break
        r = doorway.call(
            stage,
            model=model,
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=budget,
            stream=True,
            effort="high",
            custom_id=f"{stage}-{key}-{attempt}",
        )
        stop = str(r.stop_reason or "")
        if stop != "max_tokens" and complete(r.text):
            return parse(r.text, holds)
        budget = _next_budget(budget, stop)
    if stop == "max_tokens":
        raise ReviewTruncated(
            "the pre-delivery review ran out of output before its verdict at the largest output budget"
        )
    raise ReviewIncomplete(f"the pre-delivery review did not complete (stop {stop or 'none'})", retry=stop != "refusal")


def save(data: Path, findings: list[Finding], *, model: str = "", lane: str = "") -> dict[str, Any]:
    out = {
        "lane": lane,
        "model": model,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "must_fix": sum(f.grade == MUST for f in findings),
        "should_fix": sum(f.grade == SHOULD for f in findings),
        "notes": sum(f.grade == NOTE for f in findings),
        "findings": [asdict(f) for f in findings],
        "released": False,
    }
    _write(data, out)
    return out


def _write(data: Path, rec: dict[str, Any]) -> None:
    data.mkdir(parents=True, exist_ok=True)
    tmp = data / f".{RECORD}.tmp"
    tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    tmp.replace(data / RECORD)


def load(data: Path) -> dict[str, Any] | None:
    p = data / RECORD
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def forget(data: Path) -> None:
    """A ``redo`` of the review: the next run reviews afresh."""
    (data / RECORD).unlink(missing_ok=True)


def release(data: Path, why: str) -> dict[str, Any]:
    """SMD's release of a held review. Idempotent; the findings stay."""
    rec = load(data)
    if rec is None:
        raise FileNotFoundError("no review record to release")
    if not rec.get("released"):
        rec["released"] = True
        rec["released_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        rec["released_reason"] = str(why or "")[:500]
        _write(data, rec)
    return rec


def status(rec: dict[str, Any] | None) -> str:
    """``passed`` / ``released`` / ``held`` / ``missing``."""
    if rec is None:
        return "missing"
    if not rec.get("must_fix"):
        return "passed"
    return "released" if rec.get("released") else "held"


def held_reason(rec: dict[str, Any]) -> str:
    musts = [f for f in rec["findings"] if f["grade"] == MUST]
    first = "; ".join(f"{f['check']}: {f['finding']}" for f in musts)
    return f"review_held: the pre-delivery review found {len(musts)} must-fix finding(s); nothing was filed: {first}"[
        :480
    ]


def notes_section(rec: dict[str, Any] | None, heading: str = "Independent review before delivery") -> list[str]:
    """Findings for the attorney notes: SHOULD-FIX and NOTE always, and the
    MUST-FIX ones too when SMD released past them (the attorney must see what
    the review said before signing)."""
    if not rec:
        return []
    keep = (SHOULD, NOTE, MUST) if rec.get("released") else (SHOULD, NOTE)
    rows = [f for f in rec["findings"] if f["grade"] in keep]
    if not rows:
        return []
    return ["", f"## {heading}", ""] + [
        f"- {f['grade']}: {f['finding']}" + (f" ({f['cite']})" if f.get("cite") else "") for f in rows
    ]


RELEASE_MARKER = "release-review.json"
# SMD's ``redo_review``, left in the job dir by ``resume.start_run``: the next
# attempt forgets the recorded review and reviews afresh (paid once more).
REDO_MARKER = "redo-review.json"


def release_requested(job_dir: Path) -> str | None:
    """SMD's release, as the daemon left it in the job dir for this attempt
    (``resume.start_run``): the reason, or None. The daemon writes the marker
    only from a root-only ``*_job_resume`` carrying ``release_review``; the
    child never writes it."""
    p = Path(job_dir) / RELEASE_MARKER
    if not p.is_file():
        return None
    try:
        why = str(json.loads(p.read_text(encoding="utf-8")).get("reason") or "released by SMD")
    except (OSError, ValueError):
        why = "released by SMD"
    return why


def gate(data: Path, job_dir: Path, *, consume_redo: bool = True) -> tuple[str, dict[str, Any] | None]:
    """The review's decision for this attempt, applying a pending redo and a
    pending release: ``("passed"|"released"|"held"|"missing", record)``.

    A redo marker (``REDO_MARKER``) is consumed first: the record is forgotten
    and the marker unlinked, so this attempt returns ``missing`` and the lane
    reviews afresh. Consuming it here keeps a later attempt from forgetting a
    review it has just paid for. ``consume_redo=False``: the lane applies the
    redo itself (the chronology reviews once per client and a joint matter has
    several; ``stages/review._consume_redo``)."""
    redo = Path(job_dir) / REDO_MARKER
    if consume_redo and redo.is_file():
        forget(data)
        redo.unlink(missing_ok=True)
    rec = load(data)
    if rec is not None and rec.get("must_fix") and not rec.get("released"):
        why = release_requested(job_dir)
        if why is not None:
            rec = release(data, why)
    return status(rec), rec


def stage_cents(by_stage: Callable[[], dict[str, float]] | dict[str, float]) -> int:
    """The review's spend in cents from a ``Budget.by_stage()`` table."""
    table = by_stage() if callable(by_stage) else by_stage
    return int(round(sum(v for k, v in table.items() if str(k).startswith("review")) * 100))


__all__ = [
    "GRADES",
    "MUST",
    "NOTE",
    "RECORD",
    "RELEASE_MARKER",
    "REDO_MARKER",
    "SHOULD",
    "gate",
    "release_requested",
    "Finding",
    "ReviewIncomplete",
    "ReviewTruncated",
    "code",
    "complete",
    "forget",
    "held_reason",
    "load",
    "notes_section",
    "parse",
    "release",
    "run_model",
    "save",
    "stage_cents",
    "status",
]
