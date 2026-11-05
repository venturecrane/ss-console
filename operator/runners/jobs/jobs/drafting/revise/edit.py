"""The edits: code makes every change code can make; a model rewrites only the
sections the remaining items name, one section per call.

Code's own changes, deterministic and never sent to a model:

* ``replace_text`` whose words occur exactly once in the brief;
* ``remove_citations`` of record or deposition cites (a parenthetical);
* ``restructure_section`` ``move_before`` / ``move_after`` (section order).

A model call gets ONE section and the items for it, and returns
``{"text": <the section, heading line first>}``. Code keeps the answer only
when it starts with the section's own heading line; otherwise the section is
left as filed and the items read ``not_met``. Formatting items change no text:
they are renderer options (``run.py``).
"""

from __future__ import annotations

import json
from typing import Callable

from ..verify.items import parse_json
from . import checks, sections as sec
from .checklist import Item

MAX_SECTION_TOKENS = 16_000

SYSTEM = (
    "You revise ONE section of a filed legal brief exactly as the attorney's listed changes ask, and nothing else. "
    "Rules, all binding: change only what a listed change asks; keep the heading line first and unchanged; never "
    "add a fact, a figure, a number, a date, a name, a quotation or a citation the section does not already carry "
    "(you may remove or shorten them); keep every {{...}} marker exactly; keep every table exactly unless a change "
    "is about that table; keep the markdown conventions (# headings, **bold**, *italic*, | tables |). If a change "
    "would need facts the section does not have, leave that part as it is. Return ONLY a JSON object "
    '{"text": <the whole revised section>}.'
)


def code_edits(prior: list[sec.Section], items: list[Item]) -> tuple[list[sec.Section], list[str], set[str]]:
    """(sections after code's changes, what code did, ids of items code fully handled)."""
    out = [sec.Section(s.id, s.heading, s.level, s.body) for s in prior]
    did: list[str] = []
    done: set[str] = set()
    whole = sec.join(out)
    for it in items:
        if it.kind == "replace_text":
            old, new = it.params["old"], it.params["new"]
            if whole.count(old) != 1:
                continue
            for s in out:
                if old in s.text:
                    t = s.text.replace(old, new)
                    head, _, body = t.partition("\n") if s.heading else ("", "", t)
                    s.heading, s.body = (head + "\n", body) if s.heading else ("", body)
                    did.append(f"{it.id}: replaced the words asked for")
                    done.add(it.id)
            whole = sec.join(out)
        elif it.kind == "remove_citations" and it.params["cite_kind"] in checks.CODE_REMOVABLE:
            n_all = 0
            for s in sec.find(out, it.target):
                s.body, n = checks.strip_cites(s.body, it.params["cite_kind"])
                n_all += n
            did.append(f"{it.id}: removed {n_all} {it.params['cite_kind']} citation(s)")
            done.add(it.id)
    for it in items:
        if it.kind == "restructure_section" and set(it.params) & {"move_before", "move_after"}:
            out = _move(out, it)
            did.append(f"{it.id}: moved the section")
            if not set(it.params) - {"move_before", "move_after", "must_include", "must_exclude"}:
                done.add(it.id)
    return out, did, done


def _move(out: list[sec.Section], it: Item) -> list[sec.Section]:
    where = str(it.params.get("move_before") or it.params.get("move_after") or "")
    me, other = sec.find(out, it.target), sec.find(out, where)
    if not me or not other or me[0] is other[0]:
        return out
    rest = [s for s in out if s is not me[0]]
    i = rest.index(other[0]) + (0 if "move_before" in it.params else 1)
    return rest[:i] + [me[0]] + rest[i:]


def by_section(sections: list[sec.Section], items: list[Item], done: set[str]) -> dict[str, list[Item]]:
    out: dict[str, list[Item]] = {}
    for it in items:
        if it.id in done or it.kind == "formatting":
            continue
        if it.kind == "restructure_section" and not set(it.params) - {"move_before", "move_after"}:
            continue
        for s in sec.find(sections, it.target):
            out.setdefault(s.id, []).append(it)
    return out


def user(section: sec.Section, items: list[Item], findings: list[str] | None = None) -> str:
    asks = [{"id": i.id, "attorney_said": i.quote, "kind": i.kind, "params": i.params} for i in items]
    out = (
        "THE CHANGES THE ATTORNEY ASKED FOR IN THIS SECTION:\n"
        + json.dumps(asks, indent=1, ensure_ascii=False)
        + "\n\nTHE SECTION AS FILED:\n"
        + section.text
    )
    if findings:
        out += "\n\nYOUR LAST REVISION WAS REFUSED BY CODE FOR:\n" + "\n".join(f"- {f}" for f in findings)
    return out


def accept(section: sec.Section, answer: str) -> sec.Section | None:
    """The revised section, or None when the answer is not one."""
    got = parse_json(answer)
    text = got.get("text") if isinstance(got, dict) else None
    if not isinstance(text, str) or not text.strip():
        return None
    if section.heading:
        head = section.heading.rstrip("\n")
        if not text.lstrip("\n").startswith(head):
            return None
        body = text.lstrip("\n")[len(head) :]
        body = body[1:] if body.startswith("\n") else body
        tail = "\n" if section.body.endswith("\n") and not body.endswith("\n") else ""
        return sec.Section(section.id, section.heading, section.level, body + tail)
    tail = "\n" if section.body.endswith("\n") and not text.endswith("\n") else ""
    return sec.Section(section.id, "", 0, text + tail)


Ask = Callable[[str, str, str, int], str]  # (stage, system, user, max_tokens) -> answer text


def model_edits(
    current: list[sec.Section],
    todo: dict[str, list[Item]],
    ask: Ask,
    findings: dict[str, list[str]] | None = None,
) -> tuple[list[sec.Section], list[str]]:
    """Each section in ``todo`` revised by one call; a refused answer leaves the
    section as it was and says so."""
    out, said = list(current), []
    for i, s in enumerate(out):
        its = todo.get(s.id)
        if not its:
            continue
        answer = ask("revise-edit", SYSTEM, user(s, its, (findings or {}).get(s.id)), MAX_SECTION_TOKENS)
        got = accept(s, answer)
        if got is None:
            said.append(f"{s.title or 'preamble'}: the revision did not keep the section's heading; left as filed")
            continue
        out[i] = got
    return out, said


def changed(prior: list[sec.Section], edited: list[sec.Section]) -> list[str]:
    before = {s.id: s.text for s in prior}
    return [s.id for s in edited if before.get(s.id) != s.text]
