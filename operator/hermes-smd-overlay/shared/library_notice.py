"""The library-change email: what we now say, one email per person (guide seat).

WHAT IT IS. When the product library a guide seat answers from changes, each
listed person gets one short email saying what we now say. The words are
AUTHORED, in ``operator/skills/product-guide/changes.yaml``: an append-only list
of entries, each with a strictly increasing integer ``id``, the rep-facing
``lines``, and ``library_sha256``, a hash over the skill's ``references/``
files (:func:`library_sha256`). A test holds the newest entry's hash equal to
the library as committed, so the library cannot change without an entry (or the
entry's hash regenerated on purpose: ``python -m shared.library_notice rehash``).
No dates appear in any entry or body, so nothing the reply checks hold can be
rendered.

ONE MANAGED CRON JOB, DAILY (``op-managed:<persona>:product-guide``,
``wake_policy: pre_run_decides``). Its ``pre_run.py`` calls :func:`pre_run`, and
CODE does all of it, the same way ``shared.person_digest`` does the rep desk's
digest (copied from it on purpose; read that module's docstring for the
envelope and the gate):

* reads the seat's roster (``person_digest.people``: authored exact addresses
  and, on a guide seat, the enrolled list);
* for each person, reads ONLY their own mark, ``library-seen.json`` in their
  partition of the training progress store (``rep-progress``, walled per
  person, written by code only). The mark holds the id of the newest entry the
  person has been sent. It is its own file, never a field of the progress
  record, so the training tools' whole-record writes cannot race it;
* decides, per person, by these rules and no others:

  - no mark: send the NEWEST entry only (a person never gets the history);
  - mark equal to the newest id: nothing;
  - mark a known, older id: send every entry newer than it, in id order;
  - mark unknown (not an id in the file, newer than the newest, or unreadable):
    set it to the newest and send nothing (a renamed or removed entry never
    floods anyone);

* renders ONE email per person, addressed to that person only, from the
  authored lines only (:func:`render`);
* writes the person's mark BEFORE the envelope (a mark that could not be
  written drops that person from the chunk), restores every mark of the chunk
  if the envelope cannot be written, and writes at most :data:`CHUNK`
  dispatches (the envelope drops anything over ten whole); the rest wait for the
  next day.

ENROLLMENT sets the new person's mark to the newest id
(:func:`mark_newest`, called from the webhook router when it starts their
training), so a person who enrols after a change is not told about it: the
library they meet already says it.

NOTHING NEW FOR ANYONE: ``wakeAgent: false``, so no model call and no send. A
run that cannot read ``changes.yaml`` or write its envelope wakes the turn with
nothing to send, so its wake row is an obligation nothing closes and the
work-health triage reports it.

WHAT STDOUT AND THE ROWS CARRY: the verdict and counts (people, due, chunk,
deferred, reset, unreadable, the newest id). Never an address or a line.

REMOVAL. The mark lives in the person's own partition directory, so removing
the person (``workspace_broker.person_purge``) deletes it with their progress
record; a pending envelope addressed to them is removed by the same purge.

DRY RUN: ``SMD_LIBRARY_NOTICE_DRY=1`` on the pre_run reports the counts and
writes nothing (no mark, no envelope, no row).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared import person_digest, pre_run_handoff, record_store

logger = logging.getLogger(__name__)

SKILL = "product-guide"
#: The training progress store (``shared.training.PROGRESS_STORE``); pinned
#: equal by test. Named here so this module imports nothing of training's
#: curriculum loading.
STORE = "rep-progress"
MARK_NAME = "library-seen.json"
CHANGES_FILE = "changes.yaml"
REFERENCES_DIR = "references"
#: ``prerendered_dispatch._MAX_DISPATCHES``; pinned equal by test.
CHUNK = 10
#: Lines per email before the rest is summarized.
MAX_LINES = 20
ROUTING_LEG = "person"

SUBJECT = "What changed in the product library"
INTRO = "The product library the guide answers from has changed. Here is what we now say."
FOOTER = "To ask about any of it, reply in this thread."
SKELETON = (
    "Hello,\n\n"
    "The product library the guide answers from has changed. This message could not carry "
    "the details; reply in this thread and ask what changed in the library."
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class NoticeError(RuntimeError):
    """``changes.yaml`` is missing or malformed. The message names the rule."""


# ---------------------------------------------------------------------------
# The authored entries
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    id: int
    lines: tuple[str, ...]
    library_sha256: str


def parse_changes(text: str) -> list[Entry]:
    """The entries of a ``changes.yaml`` text, validated: ids are positive
    integers in strictly increasing order, each entry has at least one
    non-empty line, and each hash is 64 lowercase hex characters."""
    import yaml

    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise NoticeError(f"{CHANGES_FILE} is not valid YAML") from exc
    raw = doc.get("entries") if isinstance(doc, dict) else None
    if not isinstance(raw, list) or not raw:
        raise NoticeError(f"{CHANGES_FILE} has no entries list")
    out: list[Entry] = []
    for item in raw:
        if not isinstance(item, dict):
            raise NoticeError("an entry is not a mapping")
        eid = item.get("id")
        if isinstance(eid, bool) or not isinstance(eid, int) or eid < 1:
            raise NoticeError("an entry id is not a positive integer")
        if out and eid <= out[-1].id:
            raise NoticeError("entry ids must strictly increase")
        lines = item.get("lines")
        if (
            not isinstance(lines, list)
            or not lines
            or not all(isinstance(x, str) and x.strip() for x in lines)
        ):
            raise NoticeError(f"entry {eid} has no lines")
        digest = item.get("library_sha256")
        if not isinstance(digest, str) or not _HEX64.match(digest):
            raise NoticeError(f"entry {eid} has no library_sha256")
        out.append(Entry(eid, tuple(" ".join(x.split()) for x in lines), digest))
    return out


def load_changes(skill_dir: Path) -> list[Entry]:
    try:
        text = (skill_dir / CHANGES_FILE).read_text(encoding="utf-8")
    except OSError as exc:
        raise NoticeError(f"{CHANGES_FILE} could not be read") from exc
    return parse_changes(text)


def library_sha256(skill_dir: Path) -> str:
    """sha256 over every file under ``<skill_dir>/references/``, in sorted
    relative-path order: each file contributes its posix relative path, a NUL,
    the hex sha256 of its bytes, and a newline."""
    root = skill_dir / REFERENCES_DIR
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        h.update(rel.encode("utf-8") + b"\0")
        h.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii") + b"\n")
    return h.hexdigest()


def product_guide_dir(home: str | None = None) -> Path:
    """The product-guide skill's directory on this seat (the training module's
    resolver: the persona home's skills, the profiles', then the image's)."""
    from shared import training

    try:
        return training.skill_dir(SKILL, home=home)
    except training.TrainingError as exc:
        raise NoticeError(str(exc)) from exc


# ---------------------------------------------------------------------------
# The words
# ---------------------------------------------------------------------------


def render(entries: list[Entry]) -> str:
    """One person's email body: the authored lines of ``entries``, in order."""
    lines = ["Hello,", "", INTRO, ""]
    all_lines = [line for e in entries for line in e.lines]
    for line in all_lines[:MAX_LINES]:
        lines.append(f"- {line}")
    hidden = len(all_lines) - MAX_LINES
    if hidden > 0:
        lines.append(f"- And {hidden} more; reply and ask what changed in the library.")
    lines += ["", FOOTER]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The mark: one per person, in their own partition
# ---------------------------------------------------------------------------

#: :func:`read_mark` for a person who has no mark file.
MISSING = "missing"
#: :func:`read_mark` for a mark file that exists but holds no integer id.
UNREADABLE = "unreadable"


def _policy(cfg: Any) -> record_store.StorePolicy | None:
    policy = record_store.authored_policies(cfg).get(STORE)
    if policy is None or not policy.partitioned or not policy.managed:
        return None
    return policy


def read_mark(person: str, cfg: Any) -> int | str:
    """The id in ``person``'s mark, or :data:`MISSING`, or :data:`UNREADABLE`.
    Raises ``RecordStoreError`` when the partition cannot be examined."""
    policy = _policy(cfg)
    if policy is None:
        raise record_store.RecordStoreError(f"{STORE} is not authored walled and managed")
    if not (record_store.partition_dir(policy, person) / MARK_NAME).is_file():
        return MISSING
    try:
        raw = json.loads(record_store.code_read(STORE, person, MARK_NAME, config=cfg))
    except ValueError:
        return UNREADABLE
    seen = raw.get("seen") if isinstance(raw, dict) else None
    if isinstance(seen, bool) or not isinstance(seen, int):
        return UNREADABLE
    return seen


def write_mark(person: str, seen: int | None, cfg: Any) -> bool:
    """Set ``person``'s mark to ``seen``; ``None`` removes it (back to
    "no mark"). True on success. Written as ``person_digest.write_mark``
    writes the digest's mark: through the code writer, overwrite on."""
    try:
        if seen is None:
            policy = _policy(cfg)
            if policy is not None:
                (record_store.partition_dir(policy, person) / MARK_NAME).unlink(missing_ok=True)
            return True
        record_store.code_write(
            STORE, person, MARK_NAME, json.dumps({"seen": seen}), overwrite=True, config=cfg
        )
        return True
    except (record_store.RecordStoreError, OSError):
        return False


def mark_newest(cfg: Any, person: str, *, home: str | None = None) -> bool:
    """At enrollment: set ``person``'s mark to the newest entry's id, so a
    change made before they joined is not sent to them. True when written;
    False (and nothing written) where the seat authors no walled progress
    store or the entries cannot be read. Never raises."""
    try:
        if _policy(cfg) is None:
            return False
        entries = load_changes(product_guide_dir(home))
        return write_mark(person, entries[-1].id, cfg)
    except Exception as exc:  # noqa: BLE001 - the enrollment stands; the first run resets it
        logger.warning("library_notice: enrollment mark not written (%s)", type(exc).__name__)
        return False


# ---------------------------------------------------------------------------
# The plan and the run
# ---------------------------------------------------------------------------


@dataclass
class Dispatch:
    person: str
    body: str
    previous_mark: int | None


@dataclass
class Plan:
    status: str
    newest: int = 0
    chunk: list[Dispatch] = field(default_factory=list)
    #: People whose mark is unknown: set to the newest, sent nothing.
    reset: list[tuple[str, int | None]] = field(default_factory=list)
    people: int = 0
    due_people: int = 0
    deferred: int = 0
    unreadable: int = 0

    def counts(self) -> dict[str, int]:
        return {
            "people": self.people,
            "due": self.due_people,
            "chunk": len(self.chunk),
            "deferred": self.deferred,
            "reset": len(self.reset),
            "unreadable": self.unreadable,
            "newest": self.newest,
        }


def due_entries(entries: list[Entry], mark: int | str) -> list[Entry] | None:
    """What a person with ``mark`` is sent: a list (empty means nothing), or
    None when the mark is unknown and must be reset to the newest."""
    newest = entries[-1].id
    if mark == MISSING:
        return [entries[-1]]
    if mark == UNREADABLE or not isinstance(mark, int):
        return None
    if mark == newest:
        return []
    if mark not in {e.id for e in entries}:
        return None
    return [e for e in entries if e.id > mark]


def plan(cfg: Any, entries: list[Entry]) -> Plan:
    """Who gets a notice today, and its words. Reads; writes nothing."""
    if _policy(cfg) is None:
        return Plan(status="store_unauthored")
    everyone = person_digest.people(cfg)
    out = Plan(status="nothing_new", newest=entries[-1].id, people=len(everyone))
    for person in everyone:
        try:
            mark = read_mark(person, cfg)
        except record_store.RecordStoreError:
            out.unreadable += 1
            continue
        due = due_entries(entries, mark)
        if due is None:
            out.reset.append((person, None))
            continue
        if not due:
            continue
        out.due_people += 1
        if len(out.chunk) >= CHUNK:
            out.deferred += 1
            continue
        previous = mark if isinstance(mark, int) else None
        out.chunk.append(Dispatch(person, render(due), previous))
    if out.chunk:
        out.status = "notice_ready"
    return out


def envelope(chunk: list[Dispatch], started_at: str) -> dict[str, Any]:
    """The consume-once envelope: one dispatch per person, to that person only."""
    from shared.prerendered_dispatch import canonical_body_sha256

    return {
        "skill": SKILL,
        "render_mode": "templated",
        "started_at": started_at,
        "dispatches": [
            {
                "recipients": [d.person],
                "cc": [],
                "routing_leg": ROUTING_LEG,
                "subject": SUBJECT,
                "full_body": d.body,
                "skeleton_body": SKELETON,
                "body_sha256_full": canonical_body_sha256(d.body),
                "body_sha256_skeleton": canonical_body_sha256(SKELETON),
                "appends": [],
            }
            for d in chunk
        ],
        "unroutable": [],
        "memo_matters": [],
        "in_turn": [{"name": "library_notice_skeleton", "template": SKELETON, "slots": {}}],
        "in_turn_enforce": True,
    }


def write_envelope(payload: dict[str, Any], hermes_home: str | None = None) -> bool:
    """Atomic 0600 write beside the handoff, as ``person_digest.write_envelope``.
    False on any failure."""
    try:
        directory = pre_run_handoff.handoff_dir(hermes_home)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = directory / f"{SKILL}.dispatch.json"
        fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=".notice-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return True
    except Exception as exc:  # noqa: BLE001 - the caller reports it; never raised
        logger.warning("library_notice: envelope write failed (%s)", type(exc).__name__)
        return False


def run(
    cfg: Any,
    now: datetime | None = None,
    *,
    dry: bool = False,
    skill_dir: Path | None = None,
    wake: list[dict] | None = None,
) -> dict[str, Any]:
    """Plan, mark, write the envelope and the handoff. Returns the verdict:
    ``{"wakeAgent": bool, "status": str, **counts}``, content-free. ``wake`` as
    ``person_digest.run``: filled with the envelope's body hashes for the wake
    row, never the verdict."""
    moment = now if now is not None else datetime.now(timezone.utc)
    try:
        entries = load_changes(skill_dir if skill_dir is not None else product_guide_dir())
    except NoticeError as exc:
        logger.warning("library_notice: %s", exc)
        return {"wakeAgent": not dry, "status": "changes_unreadable"}
    p = plan(cfg, entries)
    verdict: dict[str, Any] = {"status": p.status, **p.counts()}
    if dry:
        return {"wakeAgent": False, **verdict, "dry_run": True}
    reset_failed = sum(1 for person, _ in p.reset if not write_mark(person, p.newest, cfg))
    if reset_failed:
        verdict["reset_failed"] = reset_failed
    if p.status != "notice_ready":
        return {"wakeAgent": False, **verdict}
    marked = [d for d in p.chunk if write_mark(d.person, p.newest, cfg)]
    stamp = moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    built = envelope(marked, stamp) if marked else {}
    if not marked or not write_envelope(built):
        for d in marked:
            write_mark(d.person, d.previous_mark, cfg)
        # Wake the turn with nothing to send: the run's EMITTED_WAKE row then
        # opens an obligation nothing closes, which the triage reports.
        return {"wakeAgent": True, **verdict, "status": "envelope_write_failed", "chunk": 0}
    # The bodies carry no dates (the entries are authored without them, and the
    # test runs the reply checks over every rendered entry); the handoff still
    # records the run so the out-of-turn gate reads the same shape as the digest.
    pre_run_handoff.write_handoff(SKILL, moment, [])
    person_digest._fill_wake(wake, built)
    return {
        "wakeAgent": True,
        "dispatch_expected": True,
        **verdict,
        "chunk": len(marked),
        "mark_failed": len(p.chunk) - len(marked),
    }


def pre_run(*, dry: bool = False) -> dict[str, Any]:
    """What the product-guide pre_run does: read the seat's config, run, write
    the wake row (``person_digest.wake_row``, the same broker verb; not on a
    dry run). A crash still wakes the turn, which sends nothing, so the triage
    reports it."""
    from shared.customer_config import CustomerConfig

    slug = ""
    stamps: list[dict] = []
    try:
        cfg = CustomerConfig.from_volume()
        slug = cfg.slug
        verdict = run(cfg, dry=dry, wake=stamps)
    except Exception as exc:  # noqa: BLE001 - the verdict says it crashed; never raised
        logger.warning("library_notice: the run crashed (%s)", type(exc).__name__)
        verdict = {"wakeAgent": not dry, "status": "pre_run_crashed"}
    if not dry:
        _wake_row(slug, verdict, stamps)
    return verdict


def _wake_row(slug: str, verdict: dict[str, Any], stamps: list[dict] | None = None) -> bool:
    """``person_digest.wake_row`` with this skill's name on the row, the
    bodies' hashes included."""
    from shared import workspace_broker

    woke = verdict.get("wakeAgent") is True
    counts = {k: v for k, v in verdict.items() if isinstance(v, int) and not isinstance(v, bool)}
    metadata = {
        "decision_basis": str(verdict.get("status") or ""),
        "platform": "cron-pre-run",
        "customer": slug,
        **counts,
        **({"render_mode": "templated"} if verdict.get("dispatch_expected") else {}),
        **({person_digest.WAKE_STAMPS: list(stamps)} if woke and stamps else {}),
    }
    row = {
        "action_type": "EMITTED_WAKE" if woke else "SUPPRESSED_WAKE",
        "actor": "agent",
        "actor_role": "agent",
        "skill_name": SKILL,
        "metadata": json.dumps(metadata, sort_keys=True, separators=(",", ":")),
    }
    verb = "emitted_wake_append" if woke else "suppressed_wake_append"
    try:
        return workspace_broker.request({"action": verb, "row": row}, timeout=10).get("ok") is True
    except Exception as exc:  # noqa: BLE001 - a heartbeat failure is reported, never raised
        logger.warning("library_notice: wake row not written (%s)", type(exc).__name__)
        return False


# ---------------------------------------------------------------------------
# The regeneration command (repo side, never run on a seat)
# ---------------------------------------------------------------------------

#: The repo's product-guide skill, from this file's place in the overlay tree.
REPO_SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / SKILL
_HASH_LINE = re.compile(r"(library_sha256:\s*['\"]?)([0-9a-f]{64})(['\"]?)")


def rehash(skill_dir: Path = REPO_SKILL_DIR) -> tuple[str, str]:
    """Rewrite the NEWEST entry's ``library_sha256`` in ``changes.yaml`` to the
    library as it is now. Returns ``(old, new)``. Only the last hash line is
    touched, so every comment and older entry stays byte for byte."""
    path = skill_dir / CHANGES_FILE
    text = path.read_text(encoding="utf-8")
    hits = list(_HASH_LINE.finditer(text))
    if not hits:
        raise NoticeError(f"{CHANGES_FILE} has no library_sha256 line")
    last = hits[-1]
    new = library_sha256(skill_dir)
    path.write_text(text[: last.start(2)] + new + text[last.end(2) :], encoding="utf-8")
    parse_changes(path.read_text(encoding="utf-8"))
    return last.group(2), new


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m shared.library_notice")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("rehash", help="set the newest entry's library_sha256 to the library now")
    sub.add_parser("check", help="exit 1 when the newest entry's hash is not the library's")
    args = parser.parse_args(argv)
    if args.cmd == "rehash":
        old, new = rehash()
        print(f"library_sha256: {old} -> {new}" if old != new else f"unchanged: {new}")
        return 0
    entries = load_changes(REPO_SKILL_DIR)
    now = library_sha256(REPO_SKILL_DIR)
    if entries[-1].library_sha256 != now:
        print(f"stale: entry {entries[-1].id} says {entries[-1].library_sha256}, library is {now}")
        return 1
    print(f"ok: entry {entries[-1].id}")
    return 0


__all__ = [
    "CHUNK",
    "MARK_NAME",
    "MISSING",
    "NoticeError",
    "SKELETON",
    "STORE",
    "SUBJECT",
    "UNREADABLE",
    "due_entries",
    "envelope",
    "library_sha256",
    "load_changes",
    "mark_newest",
    "parse_changes",
    "plan",
    "pre_run",
    "read_mark",
    "rehash",
    "render",
    "run",
    "write_mark",
]


if __name__ == "__main__":
    sys.exit(main())
