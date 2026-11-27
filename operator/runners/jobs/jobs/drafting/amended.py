"""Amended discovery responses after the other side's meet-and-confer letter (design A, feature 4).

WHY (2026-11-23). "They sent a meet and confer on our rog responses, please do
further responses" stopped for SMD (``served_amended``). The served sets, our
served responses and the letter are all in the matter file, so the job drafts
amended responses itself, carrying ONLY the challenged numbers (Rule 10).

**Trigger, by code** (:func:`decide`, called by the finder): the request asks
for amended, further or supplemental responses (``served_papers.asks_amended``),
or the file says so: every set the request names is already answered by a
served response of ours and a later letter from the other side is in the pull
(``challenge.letters``). The amended path runs only where its lane key
(:data:`KEY`) is proven on the seat, or in a proof run; elsewhere it ends
``served_amended`` exactly as before. The runner marks the job's envelope
``amended`` (:func:`mark_envelope`), so the proof run's result and every later
admission read the amended key (``lane_proof.lane_key``).

**What is drafted.** One unit per challenged set (:func:`plan_units`), its
compose instruction carrying, per challenged number, the request verbatim, our
served response (read by code from our served paper, :func:`our_responses`)
and the challenge in the letter's words (:func:`instruction`). Then the
existing compose, audit, gate, render and review run; the review counts the
subset (``review_map`` ``numbers``). The title reads AMENDED (:func:`retitle`).
A form interrogatory set is never drafted here: it is SMD's, named in the reply.

On disk: ``data/amended.json`` (the answered sets, our served responses and the
letters, written by the finder at $0) and ``data/challenge.json``.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from . import challenge, served as served_mod, served_papers as papers
from .outcome import DraftingFailed
from .served import OPERATOR, SET_TYPES, SMD, ServedSet
from .served_docs import Doc, docs

KEY = "document-drafter.amended_discovery_response"
FILE = "amended.json"
#: The unit label's prefix: "Amended Responses to Special Interrogatories, Set One".
LABEL = "Amended "
#: :func:`decide`'s verdicts other than a stop's reason.
NOT, AMENDED = "", "amended"
STAGE = challenge.STAGE


# ---- the trigger ---------------------------------------------------------------------------


def proven(run_dir: Path | None = None) -> bool:
    """The amended lane may run on this seat: a proof run, or its key proven."""
    from .. import lane_proof, proof_mode, seat_layout

    if proof_mode.active():
        return True
    return lane_proof.load(Path(run_dir or seat_layout.JOBS_RUN)).is_proven(KEY)


def decide(
    request: str, owed: bool, answered: list[Any], letters: list[Doc], is_proven: Callable[[], bool] | None = None
) -> str:
    """``NOT`` (the finder goes on as before), ``AMENDED``, or the reason the job
    stops ``served_amended`` for SMD. ``owed``: a set the request names is still
    unanswered; ``answered``: the (set, our response paper) pairs the finder dropped."""
    asked = papers.asks_amended(request)
    if not asked and (owed or not answered or not letters):
        return NOT
    if not asked and not (is_proven or proven)():
        # Not asked for, and the lane is not proven here: the finder goes on as on
        # main (every set answered ends served_none), never a new stop (review 11-23).
        return NOT
    if not asked and not (is_proven or proven)():
        return NOT  # unproven lane and no amended ask: the finder goes on as on main (served_none)
    if not answered:
        return (
            "the request asks for amended, further or supplemental responses and no served response of ours is on file"
        )
    if not letters:
        return "no letter from the other side challenging our responses is in the matter file"
    if all(s.set_type == "form_interrogatories" for s, _d in answered):
        return "only a form interrogatory set is answered; its amended responses are SMD's"
    if not (is_proven or proven)():
        return f"the amended-responses lane ({KEY}) is not proven on this seat"
    return AMENDED


def find_letters(data: Path, answered: list[tuple[ServedSet, Doc]]) -> list[Doc]:
    """The letters dated on or after our earliest served response."""
    dates = [d.listed for _s, d in answered if d.listed]
    return challenge.letters(docs(data), min(dates) if dates else None)


def active(data: Path) -> bool:
    return (Path(data) / FILE).is_file()


# ---- our served responses, read by code ----------------------------------------------------

_LINE_NO = re.compile(r"^\s*\d{1,2}\s*$")
_LINE_NO_LEAD = re.compile(r"^\s*\d{1,2}\s{2,}")


def our_responses(text: str, set_type: str) -> dict[str, str]:
    """Our served paper's responses by number, each its words on one line; {}
    when the paper does not read (its pleading line numbers are dropped first)."""
    from .revise import responses

    lines = [_LINE_NO_LEAD.sub("", ln).strip() for ln in (text or "").splitlines() if not _LINE_NO.match(ln)]
    try:
        doc = responses.read("\n\n".join(ln for ln in lines if ln), set_type)
    except responses.ResponsesUnread:
        return {}
    return {challenge.canon(r.number): words(doc.text_of(r)) for r in doc.responses}


_HEAD = re.compile(
    r"(?is)^\W*(?:(?:AMENDED|FURTHER|SUPPLEMENTAL)\s+)?RESPONSE\s+TO\s+.{0,80}?NO\.?\s*\d+(?:\.\d+)?\s*[:.]?\s*"
)


def words(text: str) -> str:
    """A response's own words on one line, its heading dropped when it is on the same line."""
    return _HEAD.sub("", " ".join(text.split()), count=1).strip()


def keep_sets(answered: list[tuple[ServedSet, Doc]]) -> list[ServedSet]:
    """The answered sets as the job's served sets: text sets drafted by the
    Operator, a form set by SMD (named in the reply, never drafted here)."""
    return [replace(s, drafted_by=SMD if s.set_type == "form_interrogatories" else OPERATOR) for s, _d in answered]


def write(data: Path, answered: list[tuple[ServedSet, Doc]], letters: list[Doc], trigger: str) -> None:
    rows = [
        {
            "set_type": s.set_type,
            "set_number": s.set_number,
            "propounding_party": s.propounding_party,
            "response_id": d.id,
            "response_name": d.name,
            "response_listed": d.listed,
            "responses": our_responses(d.text, s.set_type),
        }
        for s, d in answered
    ]
    body = {"trigger": trigger, "sets": rows, "letters": [d.id for d in letters]}
    _atomic(Path(data) / FILE, json.dumps(body, indent=1))


def load(data: Path) -> dict[str, Any]:
    p = Path(data) / FILE
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def _row(rec: dict[str, Any], set_type: str, set_number: int) -> dict[str, Any]:
    return next((r for r in rec.get("sets") or [] if (r["set_type"], r["set_number"]) == (set_type, set_number)), {})


def served_numbers(s: ServedSet) -> tuple[str, ...]:
    return tuple(s.checked) if s.checked else tuple(str(n) for n in range(1, s.request_count + 1))


# ---- the challenge stage (inside the find stage, before the plan) --------------------------


def challenge_stage(data: Path, job_dir: Path, ask: challenge.Ask) -> challenge.Challenges:
    """Read the letters (or take SMD's hand ``challenge.json``), check every
    challenged number has our served response, and narrow ``served.json`` to the
    challenged sets. ``challenge_unread`` / ``served_amended`` before the plan."""
    served = served_mod.read(Path(job_dir))
    ch = challenge.load(data, job_dir)
    if ch is None:
        rec = load(data)
        targets = [challenge.Target(s.set_type, s.set_number, served_numbers(s)) for s in served]
        found = [d for d in docs(data) if d.id in set(rec.get("letters") or [])]
        ch = challenge.read(found, ask, targets)
    challenge.save(data, ch)  # SMD's hand file too: everything after reads data/ alone
    keep = [s for s in served if ch.numbers_for(s.set_type, s.set_number)]
    _check_ours(data, keep, ch)
    if not any(s.drafted_by == OPERATOR for s in keep):
        raise DraftingFailed("served_amended: the letters challenge only a set SMD answers (a form set)")
    _atomic(Path(job_dir) / served_mod.FILE, served_mod.to_file(keep))
    return ch


def _check_ours(data: Path, keep: list[ServedSet], ch: challenge.Challenges) -> None:
    rec = load(data)
    for s in keep:
        if s.drafted_by != OPERATOR:
            continue
        have = _row(rec, s.set_type, s.set_number).get("responses") or {}
        missing = [n for n in ch.numbers_for(s.set_type, s.set_number) if n not in have]
        if missing:
            raise challenge.Unread(
                f"our served {s.label} does not read by code for No. {', '.join(missing[:12])} "
                f'(add "responses" to {FILE} in the job data, or answer by hand)'
            )


# ---- the plan and what compose is told ------------------------------------------------------


def plan_units(served: list[ServedSet], ch: challenge.Challenges) -> list[dict[str, Any]]:
    """One unit per challenged Operator set, carrying only its challenged numbers."""
    out = []
    for i, s in enumerate(served):
        nums = ch.numbers_for(s.set_type, s.set_number)
        if s.drafted_by == OPERATOR and nums:
            out.append({"key": f"set-{len(out) + 1}", "variant": s.set_type, "label": LABEL + s.label, "served": i,
                        "numbers": nums})  # fmt: skip
    return out


def title_of(s: ServedSet) -> str:
    return f"AMENDED {s.label.upper()}"


def instruction(data: Path, s: ServedSet, numbers: list[str]) -> str:
    """The compose instruction for one amended set: only the challenged numbers,
    each with the request verbatim, our served response and the challenge."""
    ch = challenge.load(data) or challenge.Challenges()
    ours = _row(load(data), s.set_type, s.set_number).get("responses") or {}
    requests = {str(k): v for k, v in served_mod.numbered(s.request_text).items()}
    label = s.request_label
    items = []
    for n in numbers:
        quotes = "; ".join(
            f'"{i.quote}" ({i.letter}, page {i.page})' for i in ch.quotes_for(s.set_type, s.set_number, n)
        )
        items.append(
            f"{label} NO. {n}: {requests.get(n, '')}\nOUR SERVED RESPONSE: {ours.get(n, '')}\n"
            f"THE OTHER SIDE'S CHALLENGE (their words): {quotes}"
        )
    kind = SET_TYPES[s.set_type][0]
    return (
        f"THIS DOCUMENT (set by the job, from the other side's meet-and-confer letter): draft ONLY the "
        f"{LABEL}{s.label}: {s.responding_party}'s amended responses to the {kind}, Set {s.set_number}, served by "
        f'{s.propounding_party}. Title it "{title_of(s)}". It carries exactly {len(numbers)} responses, to Nos. '
        f"{', '.join(numbers)} only, in that order, numbered as served; no other request is restated or answered. "
        f"Each request is restated verbatim before its response, labeled as the firm's responses label them (where "
        f'they show none: "{label} NO. <n>:" and "RESPONSE TO {label} NO. <n>:"). Each response is complete in '
        "itself and answers the challenge quoted for it: keep what our served response states that the file "
        "supports, supply what the challenge shows is missing from the file, and keep an objection only where our "
        "served response states it."
        "\n\nTHE CHALLENGED REQUESTS (verbatim, as served; never reworded, never renumbered):\n\n" + "\n\n".join(items)
    )


_TITLE_LINE = re.compile(r"(?i)\bRESPONSES?\s+TO\b")


def retitle(doc: str) -> tuple[str, list[str]]:
    """The document's title line (the first capitals line reading "RESPONSES TO",
    not a request heading) reads AMENDED; ([], doc) when it already does."""
    lines = doc.split("\n")
    for i, ln in enumerate(lines[:120]):
        bare = re.sub(r"[*_#|]", "", ln).strip()
        if not bare or bare != bare.upper() or re.search(r"\bNO\.?\s*\d", bare) or not _TITLE_LINE.search(bare):
            continue
        if re.search(r"\bAMENDED\b", bare):
            return doc, []
        lines[i] = re.sub(r"(?i)\b(RESPONSES?\s+TO)\b", r"AMENDED \1", ln, count=1)
        return "\n".join(lines), ["title: AMENDED added by code"]
    return doc, []


def is_unit(label: str) -> bool:
    return label.startswith(LABEL)


# ---- what the attorney is told --------------------------------------------------------------


def unit_notes(data: Path, key: str) -> list[str]:
    """The notes lines for one amended unit: the letters read, each number with
    its quoted challenge, and every item code could not confirm (left out, named)."""
    st = Path(data) / "state.json"
    plan = (json.loads(st.read_text(encoding="utf-8")).get("units") or []) if st.is_file() else []
    p = next((u for u in plan if u.get("key") == key and u.get("numbers")), None)
    ch = challenge.load(data)
    if p is None or ch is None:
        return []
    sp = Path(data) / served_mod.PLAN
    sets = json.loads(sp.read_text(encoding="utf-8"))["sets"] if sp.is_file() else []
    if not isinstance(p.get("served"), int) or p["served"] >= len(sets):
        return []
    s_type, n_set = sets[p["served"]]["set_type"], sets[p["served"]]["set_number"]
    out = ["Read the other side's letter " + ", ".join(f'"{x["name"]}"' for x in ch.letters) + "."]
    for n in p["numbers"]:
        quotes = "; ".join(f'"{i.quote}" (page {i.page})' for i in ch.quotes_for(s_type, n_set, n))
        out.append(f"No. {n}: challenged as {quotes}.")
    out += [f"Not confirmed against the letter's own words, left out: {r}" for r in ch.rejected]
    return out


def relabel(rows: list[dict[str, Any]], data: Path) -> list[dict[str, Any]]:
    """The reply's rows for an amended job: each set the job drafted reads as amended."""
    if not active(data):
        return rows
    return [{**r, "set": LABEL + r["set"]} if r.get("document") and not r.get("prior_draft") else r for r in rows]


def mark_envelope(job_dir: Path) -> None:
    """The job's envelope says ``amended``, so ``lane_proof.lane_key`` reads the
    amended key for the proof result and any later admission. Never raises."""
    p = Path(job_dir) / "envelope.json"
    try:
        env = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(env, dict) and env.get("amended") is not True:
            _atomic(p, json.dumps({**env, "amended": True}, indent=1))
    except (OSError, ValueError):
        pass


def asker(run: Any, stage: str = STAGE, role: str = "audit") -> challenge.Ask:
    """``ask(system, user, max_tokens)`` through the job's own doorway and ledger."""
    model = run.firm.model(role)

    def ask(system: str, user: str, max_tokens: int) -> str:
        return str(run.doorway.call(stage, model=model, system=system, messages=[{"role": "user", "content": user}],
                                    max_tokens=max_tokens, effort="").text or "")  # fmt: skip

    return ask


def _atomic(p: Path, text: str) -> None:
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)
