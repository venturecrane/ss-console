"""Whose page is it: the pre-delivery review's other-person decision, made by
a model looking at the page PICTURE. Paid, one page per request.

WHY (2026-11-22). Nine entries citing a household member's pages were
delivered in two chronologies. A $0 replay of the real saved jobs showed the
text rules (``review_evidence``'s old ``whose``: labelled dates of birth and
"Patient:" names) caught none of the nine: the value sat on the line after
its label, the page was handwritten, or it had no label at all. A paid proof
on the page pictures (Opus 5.5, one page per request, this prompt)
caught 15 of 15 other-person pages on two runs and read 0 of 77 client pages
as another person's, at about $0.0134 a look.

The model reads; code decides (``review_policy.on_record``): "other" on any
flagged page leaves the entry out by rule, anything else keeps it. Only
flagged pages are looked at, never every page: the entry reader's recorded
``another_person`` findings and the review's own check 1. Each verdict is
saved in ``runs/<unit>/whose_page.json`` under the page picture's sha256, the
prompt's hash and the model, so a resumed or re-settled review never pays for
a page twice. The answer carries no name or date (the prompt forbids it, and
only the two codes are kept).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

from ..medchron.prompts import raw
from ..medchron.config import IDENTITY_MODEL
from .base import StageRun, read_json

PROMPT = "whose-page-system"
#: The optional ``models.tiers.identity`` row, else this model (the proof's).
TIER, DEFAULT_MODEL = "identity", IDENTITY_MODEL
MAX_TOKENS = 400
STAGE = "review"  # the ledger prices the looks with the review that asks for them
CACHE = "whose_page.json"
SUBJECTS = ("client", "other", "both", "none", "unreadable")
BASES = ("dob", "name", "dob_and_name", "account", "none")
UNREADABLE = "unreadable"
#: A page no picture can be made of: never sent, kept, and noted as not shown.
NOT_SHOWN = "not_shown"
#: One look, priced for the limits: a page picture and the short system
#: prompt in, a one-line answer out. At Opus 5.5's rates ($4/$20 per
#: million) this is about $0.0132, against $0.0134 measured on the proof.
LOOK_IN_TOKENS, LOOK_OUT_TOKENS = 2_800, 100
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)
#: (exhibit, page) -> the page's picture block, or None when none can be made.
Images = Callable[[int, int], dict[str, Any] | None]
#: (exhibit, page, attempt, the request's content) -> the reply text.
Ask = Callable[[int, int, int, list[dict[str, Any]]], str]


class LookFailed(RuntimeError):
    """A look that did not come back (2026-11-23): the review exits through
    its retry exit with the chronology unedited, and a resume settles again
    from the saved review findings, looking only at what is still missing."""


def model(cfg: Any) -> str:
    tiers = cfg.get("models", "tiers") or {}
    return str(tiers.get(TIER) or DEFAULT_MODEL)


def system() -> str:
    return raw(PROMPT).strip()


def prompt_sha() -> str:
    return hashlib.sha256(system().encode()).hexdigest()[:12]


def parse(text: str) -> str | None:
    """The reply's subject, or None when it is not the answer asked for: a
    code fence is stripped, then the JSON must be an object with a known
    subject and basis."""
    m = _FENCE.match(text or "")
    try:
        got = json.loads(m.group(1) if m else text or "")
    except ValueError:
        return None
    if not isinstance(got, dict) or got.get("subject") not in SUBJECTS or got.get("basis") not in BASES:
        return None
    return str(got["subject"])


class Looker:
    """The verdicts of one review, cached on disk. `ask` and `images` are
    injectable (a test or a rehearsal answers at $0); by default the page
    picture is the one the update review and the entry read render
    (``update_review_io._image_source``) and the reply comes through the
    unit's paid doorway."""

    def __init__(self, sr: StageRun, *, ask: Ask | None = None, images: Images | None = None) -> None:
        self.sr = sr
        self.model = model(sr.cfg)
        self.path = sr.slug_dir / "runs" / sr.unit.unit / CACHE
        self.cache = _load(self.path)
        self.ask = ask or self._doorway
        if images is None:
            from .update_review_io import _image_source

            images = _image_source(sr)
        self.images = images

    def _doorway(self, exhibit: int, page: int, attempt: int, content: list[dict[str, Any]]) -> str:
        r = self.sr.doorway.call(
            STAGE,
            model=self.model,
            system=system(),
            messages=[{"role": "user", "content": content}],
            max_tokens=MAX_TOKENS,
            custom_id=f"whose-page-ex{exhibit}-p{page}-{attempt}",
        )
        if str(getattr(r, "stop_reason", "") or "") == "refusal":
            raise LookFailed(f"the look at Exhibit {exhibit} p. {page} was refused")
        return str(r.text or "")

    def subject(self, exhibit: int, page: int) -> str:
        """The page's subject: a saved verdict, else one look (asked again
        once when the reply is not the answer asked for; twice, unreadable).
        A page with no picture is ``NOT_SHOWN`` and never sent. A look that
        fails (the doorway gave up past its retries, a 400, a refusal) raises
        ``LookFailed`` and saves nothing; a cost or stop hold raises as itself."""
        from ..limits import LimitHold

        img = self.images(exhibit, page)
        if img is None:
            return NOT_SHOWN
        data = str((img.get("source") or {}).get("data") or "")
        key = f"{hashlib.sha256(data.encode()).hexdigest()}:{prompt_sha()}:{self.model}"
        row = self.cache.get(key)
        if isinstance(row, dict) and row.get("subject") in SUBJECTS:
            return str(row["subject"])
        u = self.sr.unit
        text = {"type": "text", "text": f"Client: {u.client_name}, date of birth {u.dob}. Page {page}."}
        got = None
        try:
            for attempt in (1, 2):
                got = parse(self.ask(exhibit, page, attempt, [text, img]))
                if got is not None:
                    break
        except (LimitHold, LookFailed):
            raise
        except Exception as exc:  # every failed look is the review's retry exit, never a kept entry
            raise LookFailed(f"the look at Exhibit {exhibit} p. {page} failed ({type(exc).__name__})") from exc
        subject = got or UNREADABLE
        self.cache[key] = {"subject": subject, "exhibit": exhibit, "page": page}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.cache, indent=1, sort_keys=True), encoding="utf-8")
        return subject


def _load(path: Path) -> dict[str, Any]:
    """The saved verdicts; {} when there are none or the file does not parse
    (a page is then looked at again, never guessed)."""
    try:
        got = read_json(path, {})
    except ValueError:
        return {}
    return got if isinstance(got, dict) else {}


def reader_pages(slug_dir: Path, unit: str) -> set[tuple[int, int]]:
    """The pages the entry reader recorded an ``another_person`` finding on."""
    from ..medchron.gates import claim_audit

    rec = claim_audit.entry_read(slug_dir, unit) or {}
    rows = rec.get("rows")
    out: set[tuple[int, int]] = set()
    for row in rows if isinstance(rows, list) else []:
        recorded = row.get("recorded") if isinstance(row, dict) else None
        for item in recorded if isinstance(recorded, list) else []:
            if isinstance(item, dict) and item.get("class") == "another_person":
                ex, pg = item.get("exhibit"), item.get("page")
                if type(ex) is int and type(pg) is int:
                    out.add((ex, pg))
    return out


def to_look(slug_dir: Path, unit: str, cfg: Any) -> int:
    """The reader's flagged pages with no saved verdict on this model and
    prompt (the review's own check 1 is not known before it runs; the
    doorway's per-call check covers those looks)."""
    cache = _load(slug_dir / "runs" / unit / CACHE)
    tail = f":{prompt_sha()}:{model(cfg)}"
    seen = {
        (r.get("exhibit"), r.get("page")) for k, r in cache.items() if str(k).endswith(tail) and isinstance(r, dict)
    }
    return sum(1 for p in reader_pages(slug_dir, unit) if p not in seen)


def projected_usd(slug_dir: Path, unit: str, cfg: Any, pricing: Any) -> float | None:
    """The looks the review is known to need, for the driver's limits; None
    when the rate card has no row for the model."""
    from ..budget import BudgetError

    try:
        rate = pricing.rate_for(model(cfg))
    except BudgetError:
        return None
    per = (LOOK_IN_TOKENS * rate.input_per_million_cents + LOOK_OUT_TOKENS * rate.output_per_million_cents) / 1e8
    return to_look(slug_dir, unit, cfg) * per
