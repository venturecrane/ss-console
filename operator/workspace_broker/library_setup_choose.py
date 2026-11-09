"""The administrator's choice from a "set up our ..." list, and the hand-off of
the made file to the root writer.

``library_setup_choose`` (gateway or root). The tool sends only the verified
email: its sender, its message id and the reader's own words above the quoted
history (``inbound.InboundOrigin.reply_text``). CODE decides, ADR 0089's
exact-match posture, and anything short of every check makes nothing:

1. the seat's switch: off, nothing is queued and the turn answers as today; a
   switch that cannot be read queues a dark job that holds for SMD;
2. the lists: the delivered lists whose request email is in the same mailbox
   conversation as this reply (the mailbox says so, never the model): a one-kind
   list, or the document library's (one section per kind), or a list made from
   what the administrator pointed at; none, or a thread the mailbox cannot read,
   refuses;
3. the person: the sender is a Named Administrator and the person who asked;
4. each list answered is the newest of its kind for that attorney (or the
   firm), and nothing was chosen for that kind from it yet;
5. the words (``read_acts``): per kind, a line naming the kind ("memos: use 2"),
   or on a one-kind list any line, gives exactly one answer: a listed number or
   file id, a listed document's matter number, "keep" (nothing changes), a
   matter that is not on the list or a quoted name (a new list of what was
   pointed at, ``pointed``). Two answers for one kind, a number not on its list,
   or numbers with no kind on the library's lists refuse the whole reply. A line
   shaped like the list's own item lines, and quoted history, are skipped.

Then one ``accept`` job per kind chosen is queued (``mode: accept``, the chosen
document's ids, the bytes' sha256 the hunt read and the contract the list named)
for the drafting lane, and one list job per document pointed at.

``library_setup_accept_submit`` and ``library_setup_accept_status`` (root only:
the runner daemon). The made file's bytes arrive base64; the job row must be an
``accept`` job that is running, and the bytes must hash to what the child
filed. The run is spooled in the shape of
``establish_intake/library_accept.SUBMISSION_FIELDS`` beside the establishment
runs (broker-written, so the intake's uid check passes), and its result is read
once, as the establishment status read reads every run's.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import library_setup_wording as wording
from .audit_ledger import _iso_utc, _ulid
from .library_setup_ledger import JOB_KIND, KINDS, LIBRARY, EnvelopeError, SetupLedger, Underway
from .logic_in_code import LIBRARY_SETUP, load_switch
from .medchron_ledger import admins_from_customer_yaml
from .recipient_policy import normalize_address


def _obj(value: Any) -> dict[str, Any]:
    """``value`` when it is an object, else an empty one."""
    return value if isinstance(value, dict) else {}


#: The contracts a made library file can carry: the kind's marked plan, or a
#: styles base (``runners/jobs/jobs/library_setup/plan.contract_for``). Never
#: ``prototypes``: a demand's reference file is not made here.
CONTRACT = "marked"
CONTRACTS = ("marked", "styles")
SCOPE = "library_accept"
#: The outcomes of a list a reply can answer.
LIST_OUTCOMES = ("candidates", "none_found", "library")
_ITEM_LINE = re.compile(r"(?i)^\s*\d+\.\s+(matter|saved)\b")
#: A bare number: not part of a date, a decimal, an id or a longer number.
_NUMBER = re.compile(r"(?<![\w.,/:-])(\d{1,3})(?![\w/:-]|[.,]\d)")


def _refused(key: str, **values: Any) -> dict[str, Any]:
    return {"ok": True, "accepted": False, "reason": wording.say(key, **values), "reason_key": key}


def choice_of(text: str, candidates: list[dict[str, Any]]) -> tuple[int | None, str]:
    """(the one listed candidate's index, "") or (None, the refusal's key)."""
    ids = {str(c.get("file_id")): i for i, c in enumerate(candidates) if isinstance(c, dict) and c.get("file_id")}
    picked: set[int] = set()
    for line in (text or "").splitlines():
        if _ITEM_LINE.match(line):
            continue
        for fid, i in ids.items():
            if fid and fid in line:
                picked.add(i)
                line = line.replace(fid, " ")
        for m in _NUMBER.finditer(line):
            n = int(m.group(1))
            if not 1 <= n <= len(candidates):
                return None, "refused_not_listed"
            picked.add(n - 1)
    if len(picked) > 1:
        return None, "refused_two"
    if not picked:
        return None, "refused_no_choice"
    return picked.pop(), ""


def _rows(db: SetupLedger, sql: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
    conn = db._connect()
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def _report(row: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(row.get("report_json") or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _lists_in_thread(verbs: Any, db: SetupLedger, choice_ref: str) -> list[dict[str, Any]] | str:
    """Every delivered list whose request is in this reply's conversation, oldest
    first, or a refusal key. A list is a one-kind list (``candidates`` or
    ``none_found``) or a document library's (``library``)."""
    thread_of = getattr(verbs, "thread_of", None)
    if thread_of is None:
        return "refused_no_list"
    try:
        conversation = thread_of(choice_ref)
        lists = _rows(
            db, "SELECT * FROM library_setup_jobs WHERE mode='list' AND state='delivered' ORDER BY created_at, id", ()
        )
        found = [r for r in lists if conversation and thread_of(str(r["request_ref"])) == conversation]
    except Exception:  # noqa: BLE001 - a thread the mailbox cannot read is "could not tell", never "no list"
        return "refused_unreadable"
    found = [r for r in found if _report(r).get("setup_outcome") in LIST_OUTCOMES]
    return found or "refused_no_list"


def _kind_entry(row: dict[str, Any], kind: str) -> dict[str, Any] | None:
    """The list's entry for ``kind``: its candidates (empty when none were found)."""
    report = _report(row)
    if row["document_class"] == LIBRARY:
        for entry in report.get("kinds") or []:
            if isinstance(entry, dict) and entry.get("kind") == kind:
                return {"candidates": entry.get("candidates") or [], "chart_donors": entry.get("chart_donors")}
        return None
    if row["document_class"] != kind:
        return None
    return {"candidates": report.get("candidates") or [], "chart_donors": report.get("chart_donors")}


def _newest_for(lists: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    """The newest list in the thread that lists ``kind``."""
    for row in reversed(lists):
        if _kind_entry(row, kind) is not None:
            return row
    return None


def _newer_or_chosen(db: SetupLedger, row: dict[str, Any], kind: str) -> str:
    newer = _rows(
        db,
        "SELECT id FROM library_setup_jobs WHERE mode='list' AND document_class=? "
        "AND COALESCE(lower(attorney_named),'')=? AND created_at > ? AND id != ?",
        (row["document_class"], str(row.get("attorney_named") or "").lower(), row["created_at"], row["id"]),
    )
    if newer:
        return "refused_superseded"
    chosen = _rows(
        db,
        "SELECT id FROM library_setup_jobs WHERE parent_id=? AND document_class=? AND state != 'failed'",
        (row["id"], kind),
    )
    if not chosen:
        return ""
    return "refused_chosen_kind" if row["document_class"] == LIBRARY else "refused_chosen"


def _insert_accept(
    db: SetupLedger, parent: dict[str, Any], kind: str, choice: dict[str, Any], env: dict[str, Any], live: bool
) -> str:
    report = _report(parent)
    job_id, now = _ulid(), _iso_utc()
    digest = hashlib.sha256(json.dumps({**env, "choice": choice}, sort_keys=True).encode()).hexdigest()
    conn = db._connect()
    try:
        conn.execute(
            "INSERT INTO library_setup_jobs (id, created_at, updated_at, state, document_class, matter_number, "
            "requester, request_ref, envelope_digest, live, attorney_named, mode, parent_id, choice_json, "
            "attorney_name, attorney_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, now, now, "submitted", kind, parent["matter_number"], env["requested_by"],
             env["request_ref"], digest, int(live), parent.get("attorney_named"), "accept", parent["id"],
             json.dumps(choice, sort_keys=True), report.get("attorney_name"), report.get("attorney_key")),
        )  # fmt: skip
        conn.commit()
    finally:
        conn.close()
    record = {
        "kind": JOB_KIND,
        "mode": "accept",
        "job_id": job_id,
        "parent_id": parent["id"],
        "setup_kind": kind,
        "document_class": kind,
        "attorney_named": parent.get("attorney_named"),
        "attorney_name": report.get("attorney_name"),
        "attorney_key": report.get("attorney_key"),
        "choice": choice,
        "requester": env["requested_by"],
        "message_ref": env["request_ref"],
        "library_matter_number": parent["matter_number"],
        "live": live,
    }
    db.queue_dir.mkdir(parents=True, exist_ok=True)
    tmp = db.queue_dir / f".{job_id}.json.tmp"
    tmp.write_text(json.dumps(record, indent=1, sort_keys=True), encoding="utf-8")
    tmp.chmod(0o640)
    tmp.replace(db.queue_dir / f"{job_id}.json")
    return job_id


# ---- reading a reply that answers lists, one choice per kind ------------------------------

#: How a reply names each kind, most specific first ("discovery responses" before "sets").
KIND_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("discovery_response", re.compile(r"(?i)\b(discovery\s+)?respon\w*|\banswers\b")),
    (
        "discovery_set",
        re.compile(r"(?i)\bdiscovery\s+(sets?|requests?)\b|\bsets\b|\binterrogator\w*|\brogs\b|\brequests\s+for\b"),
    ),
    ("mediation_brief", re.compile(r"(?i)\b(mediation\s+)?briefs?\b")),
    ("memo", re.compile(r"(?i)\bmemo(s|randa|randums?)?\b")),
    ("depo_outline", re.compile(r"(?i)\b(depo(sition)?s?\s+)?outlines?\b")),
)
_KEEP = re.compile(r"(?i)\b(keep|leave|unchanged|no\s+change|as\s+(it|they)\s+(is|are))\b")
_EVERY = re.compile(r"(?i)\b(all|every|each)\b")
_RECOMMENDED = re.compile(r"(?i)\brecommend\w*\b")
_MATTER = re.compile(r"(?i)\bmatter\s*(?:no\.?|number|#)?\s*([A-Za-z0-9][\w.-]*\d[\w.-]*|\d[\w.-]*)")
_QUOTED = re.compile(r"[\"“]([^\"”\n]{3,200})[\"”]")
_QUOTE_LINE = re.compile(r"^\s*>")


@dataclass(frozen=True)
class Act:
    """One kind's answer: ``pick`` (``index``), ``keep``, ``matter`` or ``name`` (``value``)."""

    kind: str
    what: str
    index: int = -1
    value: str = ""
    row: dict[str, Any] | None = None


def _kinds_named(line: str) -> tuple[list[str], str]:
    """(the kinds the line names, the line with those words taken out). Words
    inside quotes are a document's name, never a kind, and are kept as written."""
    named = []
    pieces = re.split(r"([\"\u201c][^\"\u201d\n]*[\"\u201d])", line)
    for kind, pattern in KIND_WORDS:
        if any(pattern.search(x) for x in pieces[0::2]):
            named.append(kind)
            pieces[0::2] = [pattern.sub(" ", x) for x in pieces[0::2]]
    return named, "".join(pieces)


def _act(kind: str, line: str, row: dict[str, Any], whole: bool = False) -> Act | str:
    """The one answer ``line`` gives for ``kind`` on list ``row``, or a refusal key."""
    candidates = (_kind_entry(row, kind) or {}).get("candidates") or []
    quoted = _QUOTED.search(line)
    if quoted:
        return Act(kind, "name", value=" ".join(quoted.group(1).split()), row=row)
    matter = _MATTER.search(line)
    if matter:
        number = matter.group(1).rstrip(".,")
        on_it = [i for i, c in enumerate(candidates) if str(c.get("matter_number") or "").lower() == number.lower()]
        if len(on_it) > 1:
            return "refused_two"
        if on_it:
            return Act(kind, "pick", index=on_it[0], row=row)
        return Act(kind, "matter", value=number, row=row)
    if _KEEP.search(line):
        return Act(kind, "keep", row=row)
    if whole and _RECOMMENDED.search(line) and candidates:
        return Act(kind, "pick", index=0, row=row)
    index, why = choice_of(line, candidates)
    return Act(kind, "pick", index=index, row=row) if index is not None else why


def _lines(text: str) -> list[str]:
    return [x for x in (text or "").splitlines() if x.strip() and not _ITEM_LINE.match(x) and not _QUOTE_LINE.match(x)]


def read_acts(text: str, lists: list[dict[str, Any]], model_kind: str = "") -> list[Act] | tuple[str, str]:
    """Each kind's answer in the reply, in code, or (the refusal key, the kind it is about).

    A line that names a kind answers that kind, from the newest list in the
    thread that lists it. A line that names no kind answers the newest list when
    that is a one-kind list; on a document library's lists it answers every kind
    only when it says so ("all", "every", "each"), and otherwise asks which kind.
    A kind answered twice, two numbers for one kind, or a number not on its list
    refuses the whole reply: nothing is made."""
    newest = lists[-1]
    if model_kind and newest["document_class"] not in (model_kind, LIBRARY):
        return "refused_no_list", ""
    acts: dict[str, Act] = {}
    unplaced = False
    for line in _lines(text):
        named, rest = _kinds_named(line)
        whole = False
        if not named:
            if newest["document_class"] != LIBRARY:
                named = [newest["document_class"]]
            elif _EVERY.search(rest) or _RECOMMENDED.search(rest):
                whole = True
                named = [k for k in KINDS if (_kind_entry(newest, k) or {}).get("candidates")]
            else:
                unplaced = unplaced or bool(_NUMBER.search(rest) or _MATTER.search(rest) or _KEEP.search(rest))
                continue
        for kind in named:
            row = _newest_for(lists, kind) if not whole else newest
            if row is None:
                return "refused_kind_not_listed", kind
            act = _act(kind, rest, row, whole)
            if isinstance(act, str):
                if act == "refused_no_choice":
                    continue  # a line with no number, matter, name or "keep" answers nothing
                return act, kind
            if kind in acts and acts[kind] != act:
                return "refused_two", kind
            acts[kind] = act
    if not acts:
        return ("refused_which_kind" if unplaced else "refused_no_choice"), ""
    return [acts[k] for k in KINDS if k in acts]


def _checks(verbs: Any, db: SetupLedger, env: dict[str, Any], acts: list[Act]) -> tuple[str, str]:
    """("", "") when every list answered may be chosen from, else (the refusal key, its kind)."""
    admins = admins_from_customer_yaml(str(verbs.customer_yaml)) or ()
    for act in acts:
        row = act.row or {}
        if (
            env["requested_by"] != normalize_address(str(row.get("requester") or ""))
            or env["requested_by"] not in admins
        ):
            return "refused_not_requester", act.kind
        if act.what == "keep":
            continue
        blocked = _newer_or_chosen(db, row, act.kind)
        if blocked in ("refused_chosen", "refused_chosen_kind") and act.what != "pick":
            blocked = ""  # pointing again at another document is a new list, not a second choice
        if blocked:
            return blocked, act.kind
        if act.what == "pick":
            picked = (_kind_entry(row, act.kind) or {}).get("candidates", [])[act.index]
            if not all(picked.get(k) for k in ("file_id", "matter_id", "sha256", "saved_on")):
                return "refused_stale", act.kind
    return "", ""


def _choice(act: Act) -> dict[str, Any]:
    entry = _kind_entry(act.row or {}, act.kind) or {}
    c = entry["candidates"][act.index]
    keys = ("file_id", "matter_id", "matter_number", "saved_on", "sha256")
    choice = {"n": act.index + 1, **{k: c.get(k) for k in keys}}
    if c.get("contract") in CONTRACTS:
        choice["contract"] = c["contract"]
    donors = entry.get("chart_donors")
    if isinstance(donors, list) and donors:  # a memo's chart look, by rank (memo_charts): ids only
        choice["chart_donors"] = [
            {k: str(d.get(k) or "") for k in ("file_id", "matter_id", "sha256")} for d in donors if isinstance(d, dict)
        ]
    return choice


def _point(db: SetupLedger, act: Act, env: dict[str, Any], live: bool) -> str:
    """Queue a list of what the administrator pointed at: that kind, that matter or
    those words of a name, in this thread. Its reply is the list's own."""
    row = act.row or {}
    envelope = {
        "kind": act.kind,
        "attorney": row.get("attorney_named") or None,
        "requested_by": env["requested_by"],
        "request_ref": env["request_ref"],
        "request_text": env["reply_text"],
        ("matter" if act.what == "matter" else "document"): act.value,
    }
    return db.submit(envelope, library_number=str(row["matter_number"]), live=live)


def choose(verbs: Any, db: SetupLedger, request: dict[str, Any], audit: Any) -> dict[str, Any]:
    raw = _obj(request.get("envelope"))
    env = {
        "requested_by": normalize_address(str(raw.get("requested_by") or "")),
        "request_ref": str(raw.get("request_ref") or "").strip(),
        "reply_text": str(raw.get("reply_text") or ""),
        "kind": str(raw.get("kind") or ""),
    }
    if not (env["requested_by"] and env["request_ref"]):
        return _refused("refused_no_list")
    sw = load_switch(LIBRARY_SETUP, str(verbs.customer_yaml))
    if not sw.enforcing and sw.error is None:
        return {"ok": True, "accepted": False, "live": False}
    lists = _lists_in_thread(verbs, db, env["request_ref"])
    if isinstance(lists, str):
        return _refused(lists)
    acts = read_acts(env["reply_text"], lists, env["kind"])
    if isinstance(acts, tuple):
        key, kind = acts
        return _refused(key, **_values(key, kind, env["reply_text"]))
    key, kind = _checks(verbs, db, env, acts)
    if key:
        return _refused(key, **wording.names(kind))
    live = sw.error is None
    job_ids, parts = [], []
    for act in acts:
        if act.what == "pick":
            row = act.row or {}
            job_id = _insert_accept(db, row, act.kind, _choice(act), env, live)
            job_ids.append(job_id)
            parts.append((act.kind, "pick", act.index + 1))
            if live:
                audit("LIBRARY_SETUP_CHOSEN", {"job_id": job_id, "parent_id": row["id"], "n": act.index + 1})
        elif act.what == "keep":
            parts.append((act.kind, "keep", None))
        else:
            try:
                job_ids.append(_point(db, act, env, live))
            except Underway:
                return _refused("refused_underway", **wording.names(act.kind))
            except EnvelopeError:
                return _refused("refused_no_choice")
            parts.append((act.kind, act.what, act.value))
    out: dict[str, Any] = {"ok": True, "accepted": True, "live": live, "job_ids": job_ids}
    if job_ids:
        out["job_id"] = job_ids[0]
    if not live:
        return out
    one = acts[0]
    if len(acts) == 1 and one.what == "pick" and (one.row or {}).get("document_class") != LIBRARY:
        attorney = _report(one.row or {}).get("attorney_name") or None
        out["say"] = wording.chosen(one.kind, one.index + 1, attorney)
    else:
        out["say"] = wording.chosen_library(parts)
    return out


def _values(key: str, kind: str, text: str) -> dict[str, Any]:
    """The values a choice refusal's sentence names."""
    return dict(wording.names(kind)) if kind else {}


# ---- the made file, to the root writer ---------------------------------------------------


def spool(runs_dir: Path, run_id: str, submission: dict[str, Any], data: bytes) -> None:
    """The run, assembled in a dot-prefixed directory the intake ignores, then renamed."""
    tmp = Path(runs_dir) / f".tmp-{run_id}"
    try:
        tmp.mkdir(parents=True)
        (tmp / "file.docx").write_bytes(data)
        (tmp / "submission.json").write_text(json.dumps(submission, sort_keys=True), "utf-8")
        tmp.rename(Path(runs_dir) / run_id)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def submission(
    row: dict[str, Any], run_id: str, data: bytes, filed: dict[str, Any], request: dict[str, Any]
) -> dict[str, Any]:
    fixed = request.get("fixed_strings")
    replaces = request.get("replaces")
    chart = request.get("chart_source")
    return {
        "scope": SCOPE,
        "phase": "install",
        "run_id": run_id,
        "created_at": _iso_utc(),
        "acceptance": {
            "kind": row["document_class"],
            "attorney": row.get("attorney_key") or None,
            "file_id": str(filed["file_id"]),
            "version": str(filed["version"]),
            "contract": _contract(request),
            "accepted_by": row["requester"],
            "accepted_at": row["created_at"],
            "sha256": hashlib.sha256(data).hexdigest(),
            "fixed_strings": [str(s) for s in fixed] if isinstance(fixed, list) else [],
            "replaces": replaces if isinstance(replaces, dict) else None,
            # A memo whose tables take another file's look (memo_charts): which, by id.
            "chart_source": chart if isinstance(chart, dict) else None,
        },
    }


def _contract(request: dict[str, Any]) -> str:
    """The made file's contract as the job made it; ``marked`` when a runner from
    before contracts were named sends none. Anything else is refused."""
    value = request.get("contract") or CONTRACT
    if value not in CONTRACTS:
        raise ValueError(f"contract must be one of {list(CONTRACTS)}")
    return str(value)


def accept_submit(store: Any, db: SetupLedger, request: dict[str, Any]) -> dict[str, Any]:
    """Root only: spool the made file's acceptance for the root writer."""
    row = db.read(str(request.get("job_id") or ""))
    if row is None or row.get("mode") != "accept" or row.get("state") != "running":
        raise ValueError("library_setup_accept_submit names no running accept job")
    if store is None:
        raise ValueError("the establishment spool is not configured on this broker")
    filed = _obj(request.get("filed"))
    try:
        data = base64.b64decode(str(request.get("data_b64") or ""), validate=True)
    except ValueError as exc:
        raise ValueError("data_b64 is not base64") from exc
    if not (filed.get("file_id") and filed.get("version")) or hashlib.sha256(data).hexdigest() != filed.get("sha256"):
        raise ValueError("the made file's bytes are not the ones the job filed")
    _contract(request)
    run_id = _ulid().lower()
    spool(store.runs_dir, run_id, submission(row, run_id, data, filed, request), data)
    _set_run(db, row["id"], run_id)
    return {"ok": True, "run_id": run_id}


def _set_run(db: SetupLedger, job_id: str, run_id: str) -> None:
    conn = db._connect()
    try:
        conn.execute("UPDATE library_setup_jobs SET run_id=? WHERE id=?", (run_id, job_id))
        conn.commit()
    finally:
        conn.close()


def accept_status(store: Any, db: SetupLedger, request: dict[str, Any]) -> dict[str, Any]:
    """Root only: the intake's answer for the job's run (one-shot, as every run's)."""
    row = db.read(str(request.get("job_id") or ""))
    if row is None or not row.get("run_id") or store is None:
        raise ValueError("library_setup_accept_status names no spooled accept job")
    try:
        return store.status({"run_id": row["run_id"]})
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise ValueError(f"the run's result could not be read: {exc}") from exc


__all__ = ["Act", "accept_status", "accept_submit", "choice_of", "choose", "read_acts", "spool", "submission"]
