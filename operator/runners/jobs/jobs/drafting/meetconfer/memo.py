"""The meet-and-confer memo, the amended job's last file (design C, part 8).

Write-once, after the amended units are rendered. Per challenged request, in
served order, code assembles what is already on disk and decided:

* the request (verbatim from the served set), our served response (read by code
  from our served paper, ``amended.json``) and their challenge (the letter's own
  words, ``challenge.json``);
* the firm rule that governs it: each recorded rule the rules stage applied to
  that response, in the attorney's words with the date he stated it;
* the amended response, read by code from the amended file just drafted;
* the position, by code: ``changed`` (the amended response differs from the
  served one), ``unchanged`` (we stand on it), or ``attorney decides`` (no
  amended response, or one carrying an attorney marker).

The one model call writes a paragraph per item, labelled "Draft assessment";
code refuses a paragraph that names a number not in that item's inputs, and an
item left without one carries an attorney marker. No deadline is computed: the
memo carries a ``{{ATTORNEY: ...}}`` marker for it. Filed as "Meet and Confer
Memo - <matter> - <date>.docx".
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .. import amended, challenge, served as served_mod
from ..finish_review import FINAL
from ..served import OPERATOR, ServedSet

DIR = "meetconfer"
STAGE = "mc-assess"
MAX_TOKENS = 4_000
CHANGED, UNCHANGED, ATTORNEY = "changed", "unchanged", "attorney decides"
POSITIONS = {
    CHANGED: "Changed: the amended response above replaces the served one.",
    UNCHANGED: "Unchanged: we stand on the served response.",
    ATTORNEY: "Attorney decides: no amended response is drafted for this request.",
}
DEADLINE = "{{ATTORNEY: apply the motion to compel deadline and any extension agreed; none is computed here}}"
NO_ASSESSMENT = "{{ATTORNEY: write the assessment of this challenge}}"
_DIGITS = re.compile(r"\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Entry:
    id: str
    set_label: str
    label: str
    number: str
    request: str
    ours: str
    challenge: list[str]
    rules: list[str]
    amended: str
    position: str


def name(matter_number: str, date_stamp: str) -> str:
    return f"Meet and Confer Memo - {matter_number} - {date_stamp}.docx"


def position(ours: str, amended_text: str) -> str:
    if not amended_text.strip() or "{{ATTORNEY" in amended_text:
        return ATTORNEY
    return UNCHANGED if challenge.flat(amended_text) == challenge.flat(ours) else CHANGED


def governing(unit_data: Path | None, key: str, number: str, rules: list[dict[str, Any]]) -> list[str]:
    """Each recorded rule the rules stage applied to this response, as stated, with its date."""
    p = Path(unit_data) / "rulebook" / f"{key}.json" if unit_data else None
    applied = json.loads(p.read_text(encoding="utf-8")).get("applied") or [] if p and p.is_file() else []
    ids = [str(a["source"]).removeprefix("rule:") for a in applied if challenge.canon(a.get("item", "")) == number]
    by_id = {str(r.get("rule_id")): r for r in rules}
    out = []
    for rid in dict.fromkeys(ids):
        r = by_id.get(rid)
        if r:
            out.append(f'"{" ".join(str(r.get("text") or "").split())}" (stated {str(r.get("at") or "")[:10]})')
    return out


def amended_by_number(final_md: str, set_type: str) -> dict[str, str]:
    from ..render import split_notes
    from ..revise import responses

    try:
        doc = responses.read(split_notes(final_md)[0], set_type)
    except responses.ResponsesUnread:
        return {}
    return {challenge.canon(r.number): amended.words(doc.text_of(r)) for r in doc.responses}


def entries(
    served: list[ServedSet], ch: challenge.Challenges, rec: dict[str, Any], drafted: dict[tuple[str, int], Any],
    rules: list[dict[str, Any]],
) -> list[Entry]:  # fmt: skip
    """Every challenged request, in served order. ``drafted``: (set type, set
    number) -> (unit key, unit data dir, final markdown) for each amended unit."""
    out = []
    for s in served:
        nums = ch.numbers_for(s.set_type, s.set_number)
        row = next(
            (r for r in rec.get("sets") or [] if (r["set_type"], r["set_number"]) == (s.set_type, s.set_number)), {}
        )
        key, udata, md = drafted.get((s.set_type, s.set_number)) or ("", None, "")
        made = amended_by_number(md, s.set_type) if md and s.drafted_by == OPERATOR else {}
        requests = {str(k): v for k, v in served_mod.numbered(s.request_text).items()}
        for n in nums:
            ours = (row.get("responses") or {}).get(n, "")
            text = made.get(n, "")
            out.append(Entry(f"{s.set_type}:{s.set_number}:{n}", s.label.removeprefix("Responses to "), s.request_label,
                             n, requests.get(n, ""), ours,
                             [f'"{i.quote}" ({i.letter}, page {i.page})' for i in ch.quotes_for(s.set_type, s.set_number, n)],
                             governing(udata, key, n, rules), text, position(ours, text)))  # fmt: skip
    return out


def assess_system() -> str:
    return (
        "You assist a litigation attorney answering opposing counsel's meet-and-confer letter about our client's "
        "discovery responses. For each item you get the request, our served response, their challenge in their "
        "words, the firm rules that govern it, our amended response and the position. Write one short paragraph "
        "per item assessing the challenge against our response: whether it has merit and why, in plain words. "
        "Use only the facts in the item; cite no case, statute or date that is not in it. Return ONLY a JSON "
        'object {"<item id>": "<paragraph>", ...} with every item id. No other text.'
    )


def _ok(e: Entry, text: Any) -> bool:
    if not isinstance(text, str) or not text.strip() or len(text) > 2_000:
        return False
    inputs = " ".join([e.number, e.request, e.ours, *e.challenge, *e.rules, e.amended])
    have = set(_DIGITS.findall(inputs))
    return all(d in have for d in _DIGITS.findall(text))


def assess(es: list[Entry], ask: challenge.Ask) -> dict[str, str]:
    """One paragraph per entry, each checked by code; one retry for the rest."""
    out: dict[str, str] = {}
    todo = list(es)
    for _attempt in range(2):
        if not todo:
            break
        raw = ask(assess_system(), json.dumps([asdict(e) for e in todo], indent=1), MAX_TOKENS)
        m = re.search(r"\{.*\}", raw or "", re.S)
        try:
            got = json.loads(m.group(0)) if m else {}
        except ValueError:
            got = {}
        got = got if isinstance(got, dict) else {}
        for e in todo:
            if _ok(e, got.get(e.id)):
                out[e.id] = " ".join(str(got[e.id]).split())
        todo = [e for e in todo if e.id not in out]
    return out


def render_md(es: list[Entry], said: dict[str, str], ch: challenge.Challenges, rec: dict[str, Any], head: str) -> str:
    lines = ["# Meet and Confer Memo", "", head, ""]
    lines += ["Letters read: " + ", ".join(f'"{x["name"]}" ({x.get("listed") or "undated"})' for x in ch.letters), ""]
    current = ""
    for e in es:
        if e.set_label != current:
            current = e.set_label
            lines += [f"## {current}", ""]
        lines += [
            f"### {e.label.title()} No. {e.number}", "",
            f"**Request.** {e.request}", "",
            f"**Our served response.** {e.ours}", "",
            f"**Their challenge.** {'; '.join(e.challenge)}", "",
            f"**Governing rule.** {'; '.join(e.rules) or 'No recorded firm rule names this request.'}", "",
            f"**Amended response.** {e.amended or 'None drafted.'}", "",
            f"**Position.** {POSITIONS[e.position]}", "",
            f"**Draft assessment.** {said.get(e.id) or NO_ASSESSMENT}", "",
        ]  # fmt: skip
    served_on = sorted({str(r.get("response_listed")) for r in rec.get("sets") or [] if r.get("response_listed")})
    lines += ["## Deadlines", "", DEADLINE, ""]
    lines += [f"Record dates: our responses served {', '.join(served_on) or 'date not read'}; letters dated "
              f"{', '.join(str(x.get('listed') or 'undated') for x in ch.letters)}.", ""]  # fmt: skip
    return "\n".join(lines)


def build(run: Any) -> Path | None:
    """The memo for an amended job, written once; None for any other job."""
    from ...demand import deliver

    data = Path(run.data)
    if not amended.active(data):
        return None
    out_dir = deliver.out_dir(data)
    path = out_dir / name(run.job.matter_number, run.date_stamp)
    work = data / DIR
    if path.is_file() and (work / "memo.md").is_file():
        return path
    work.mkdir(parents=True, exist_ok=True)
    ch = challenge.load(data) or challenge.Challenges()
    served = served_mod.frozen(data, run.job.job_dir)
    plan = {u.get("key"): u for u in run._state().get("units") or []}
    drafted = {}
    for u in run.units():
        p = plan.get(u.key) or {}
        if isinstance(p.get("served"), int) and p.get("numbers"):
            s = served[p["served"]]
            final = u.data / FINAL
            drafted[(s.set_type, s.set_number)] = (
                u.key,
                u.data,
                final.read_text(encoding="utf-8") if final.is_file() else "",
            )
    rec = amended.load(data)
    from ..rulebook.stage import frozen as frozen_rules

    es = entries(served, ch, rec, drafted, frozen_rules(data))
    saved = work / "assess.json"
    said = json.loads(saved.read_text(encoding="utf-8")) if saved.is_file() else assess(es, amended.asker(run, STAGE))
    saved.write_text(json.dumps(said, indent=1), encoding="utf-8")
    md = render_md(es, said, ch, rec, f"Matter {run.job.matter_number} · {run.date_stamp}")
    (work / "memo.md").write_text(md, encoding="utf-8")
    return deliver.render_plain(md, out_dir, path.name, str(run.firm.get("firm", "display_name")))


__all__ = ["Entry", "build", "entries", "name", "position", "render_md"]
