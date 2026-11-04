"""The check's records: one ``Item`` per thing checked, the ``Report`` over a
document, and the per-claim ``Cache``.

Grades (2026-11-04 method): ``confirmed``, ``wrong`` (``serious`` or
``minor``), ``framing``, ``disclosed`` (an omission the notes now name), and
``could_not_verify``. A ``wrong`` blocks only when a second, independent read
agreed (``counted``, ``adjudicate``); "could not verify" is never rounded up.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .sources import sha

GRADES = ("confirmed", "wrong", "framing", "disclosed", "could_not_verify")
SEVERITIES = ("serious", "minor")
PARTS = ("characterization", "omission", "authority", "adversary")
#: Bumped when a prompt or the evidence shown changes what a grade means, so
#: a cached grade from an older method is never reused.
METHOD = "check-v1"

Ask = Callable[[str, str], str]


@dataclass
class Block:
    """One piece of evidence shown to a checker: ``ref`` is what it is
    (``F0007 excerpt``, ``name p.3``, ``P: slug``), ``text`` what was shown."""

    ref: str
    text: str


@dataclass
class Item:
    key: str
    part: str
    section: str
    sentence: str
    preview: str
    ids: tuple[str, ...] = ()
    slugs: tuple[str, ...] = ()
    grade: str = "could_not_verify"
    severity: str = ""
    note: str = ""
    fix: str = ""
    evidence: tuple[str, ...] = ()
    against: tuple[str, ...] = ()
    counted: bool = False
    second: dict[str, Any] | None = None
    cached: bool = False
    control: str = ""  # a planted control's kind; never in the report's counts
    blocks: list[Block] = field(default_factory=list, repr=False)

    @property
    def blocking(self) -> bool:
        return self.grade == "wrong" and self.counted

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("blocks", None)
        d["ids"], d["slugs"], d["evidence"], d["against"] = (
            list(self.ids),
            list(self.slugs),
            list(self.evidence),
            list(self.against),
        )
        return d

    def graded(self) -> dict[str, Any]:
        """The fields a cache stores."""
        return {
            "grade": self.grade,
            "severity": self.severity,
            "note": self.note,
            "fix": self.fix,
            "evidence": list(self.evidence),
            "against": list(self.against),
        }

    def take(self, g: dict[str, Any]) -> None:
        self.grade = str(g.get("grade") or "could_not_verify")
        self.severity = str(g.get("severity") or "")
        self.note = str(g.get("note") or "")
        self.fix = str(g.get("fix") or "")
        self.evidence = tuple(g.get("evidence") or ())
        self.against = tuple(g.get("against") or ())


def item_key(part: str, sentence: str, ids: tuple[str, ...], page_sha: str, extra: str = "") -> str:
    """sha(sentence + ids + page_sha): a sentence, its ids and the text of the
    pages behind them unchanged is a check already made."""
    return sha("\x1f".join((METHOD, part, sentence, ",".join(ids), page_sha, extra)))


class Cache:
    """One JSON file per checked claim, under the job's data directory, so a
    resumed job pays only for sentences that changed."""

    def __init__(self, directory: Path | None) -> None:
        self.dir = directory
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> dict[str, Any] | None:
        if self.dir is None:
            return None
        p = self.dir / f"{key}.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def put(self, key: str, body: dict[str, Any]) -> None:
        if self.dir is None:
            return
        p = self.dir / f"{key}.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, sort_keys=True), encoding="utf-8")
        tmp.replace(p)


def parse_json(text: str) -> dict[str, Any] | None:
    """The one JSON object in a model's answer, or None."""
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        got = json.loads(s[a : b + 1])
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


def apply_grade(item: Item, g: dict[str, Any], shown: dict[str, str]) -> None:
    """A checker's grade, under code's rules. ``shown`` maps the evidence ids
    the checker was given (``E3``) to what each was. An id it was not given is
    dropped; a ``confirmed`` naming none it was given is not a confirmation
    (the only way a digest, a memory or a fact's claim could be its basis)."""
    grade = str(g.get("grade") or "").strip().lower()
    if grade not in ("confirmed", "wrong", "framing", "could_not_verify"):
        grade = "could_not_verify"
    used = [str(e).strip().strip("[]") for e in (g.get("evidence") or []) if isinstance(e, (str, int))]
    refs = tuple(shown[e] for e in used if e in shown)
    note = " ".join(str(g.get("note") or "").split())[:400]
    if grade == "confirmed" and not refs:
        grade, note = "could_not_verify", "graded confirmed without naming any evidence shown"
    sev = str(g.get("severity") or "").strip().lower()
    item.grade = grade
    item.severity = (sev if sev in SEVERITIES else "minor") if grade == "wrong" else ""
    item.note = note
    item.fix = " ".join(str(g.get("fix") or "").split())[:400] if grade in ("wrong", "framing") else ""
    item.evidence = refs


@dataclass
class Report:
    items: list[Item]
    controls: dict[str, Any] = field(default_factory=dict)
    wanted: list[dict[str, Any]] = field(default_factory=list)
    calls: int = 0
    refused_sources: list[str] = field(default_factory=list)

    def real(self) -> list[Item]:
        return [i for i in self.items if not i.control]

    def counts(self) -> dict[str, int]:
        out = {g: 0 for g in GRADES}
        for i in self.real():
            out[i.grade] = out.get(i.grade, 0) + 1
        out["wrong_counted_serious"] = sum(1 for i in self.real() if i.blocking and i.severity == "serious")
        out["wrong_counted_minor"] = sum(1 for i in self.real() if i.blocking and i.severity == "minor")
        out["cached"] = sum(1 for i in self.real() if i.cached)
        return out

    def blocking(self) -> list[Item]:
        return [i for i in self.real() if i.blocking]

    def repair_brief(self) -> str:
        """The two-of-two wrong sentences, as the scoped repair receives them."""
        lines = []
        for i in self.blocking():
            lines.append(f"- SENTENCE ({i.section or 'opening'}): {i.sentence}")
            lines.append(f"  - [{i.severity}] {i.note}" + (f" FIX: {i.fix}" if i.fix else ""))
        return "\n".join(lines)

    def notes_md(self) -> str:
        """What the attorney notes carry from the check: every judgment risk
        a reader should weigh, disclosed rather than hidden."""
        real = self.real()
        out = ["## Source check", ""]
        c = self.counts()
        out.append(
            f"Every sentence that cites the record was read against the cited pages: {c['confirmed']} confirmed, "
            f"{c['wrong']} wrong, {c['framing']} framing, {c['disclosed']} disclosed, "
            f"{c['could_not_verify']} could not be verified."
        )
        groups = (
            ("Still wrong after repair (two independent readers agreed)", [i for i in real if i.blocking]),
            ("Framing: true as far as it goes, with a qualifier left out", [i for i in real if i.grade == "framing"]),
            ("The record also shows (not cited in the brief)", [i for i in real if i.grade == "disclosed"]),
            ("Could not be verified", [i for i in real if i.grade == "could_not_verify"]),
        )
        for title, rows in groups:
            if not rows:
                continue
            out += ["", f"### {title}", ""]
            for i in rows:
                why = i.fix or i.note
                out.append(f"- {i.section or 'Opening'}: {_short(i.preview)}" + (f" ({why})" if why else ""))
        if self.wanted:
            out += ["", "### Authorities with no primary text on file", ""]
            out += [f"- {w['citation']}" for w in self.wanted]
        return "\n".join(out) + "\n"

    def to_json(self) -> dict[str, Any]:
        return {
            "method": METHOD,
            "counts": self.counts(),
            "calls": self.calls,
            "controls": self.controls,
            "wanted": self.wanted,
            "refused_sources": self.refused_sources,
            "items": [i.to_json() for i in self.items],
        }


def _short(s: str, n: int = 240) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 3] + "..."
