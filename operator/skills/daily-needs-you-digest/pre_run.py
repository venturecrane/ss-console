#!/usr/bin/env python3
"""Shared cron pre-run gate — the empty-seat wake suppressor (ADR 0021 Stream B).

CANONICAL SOURCE: ``operator/templates/pre_run_gate.py``. The copies stamped
into skill directories as ``pre_run.py`` MUST be byte-identical to this file —
``operator/tests/test_pre_run_gate.py`` enforces the sync. Edit here, restamp.

What this gate decides
----------------------
Scheduled tracker/watch/chase skills re-derive their work from the matter set
in the practice-management system (they keep no local state — the matter is
the system of record). On a seat with ZERO open matters there is provably no
work for any of them, so waking the model is pure token burn. This gate:

  1. Probes Smokeball for open matters (one ``GET /matters?Status=Open&Limit=1``
     via the connector's own venv — the connector package is not importable
     from the Hermes venv this script runs in).
  2. Any open matter, any probe failure, any unknown response envelope, any
     heartbeat-write failure → ``{"wakeAgent": true}`` (fail-open; the agent
     wakes exactly as it would with no gate).
  3. Zero open matters → writes a SUPPRESSED_WAKE heartbeat row through the
     broker's uid-gated ``suppressed_wake_append`` verb, THEN emits
     ``{"wakeAgent": false}``. Suppress-without-heartbeat never happens
     (mirror-don't-gate): if the broker write fails, the agent wakes.

What this gate deliberately does NOT decide
-------------------------------------------
It is NOT a per-skill "is there work" check. A hydrated seat (any open
matter) always wakes — deeper UpdatedSince/state-delta gates are follow-on
work once the Smokeball ``updated_since`` wire format is verified at connect.
Lead-stage and inbound-driven work is unaffected either way: webhook and
inbound wakes are ungated by design.

Derived memo facts, for the skills in ``_FACT_SKILLS``
------------------------------------------------------
A scheduled routine runs ONE model session across EVERY open matter, and the
overlay's matter-mixing fence (``shared/matter_gate.py``) refuses a second
matter's memo read inside one session — correctly, because one session must
not hold two matters' content. On 2026-09-22 pilot-smokeball logged 16 such
refusals in a morning (motion-calendar-tracker 8, service-confirmation-watcher
7, discovery-response-tracker 1), each one feeding the seat's refusal-cascade
brake, which hard-stops the seat at 20 in 30 minutes.

The fix is not a looser fence. It is that the model never needs a second
matter's content on a scheduled scan: CODE reads across matters and hands the
model DERIVED FACTS. Code does not compose, so it carries no mixing risk.

Two properties make that a structure rather than a promise:

* **The prose never enters this process.** The cross-matter read and the
  extraction BOTH run inside the connector venv (:data:`_FACTS_SNIPPET` loads
  this very file and calls :func:`pull_facts_payload`), so what crosses the
  process boundary is already atoms. :func:`_atoms_only` re-checks at the
  boundary, because a writer's guarantee the reader does not verify is the
  reader trusting a stranger.
* **Every failure degrades to a plain wake.** No facts, no suppression, no
  raise. A seat with a broken facts pull behaves exactly as a seat with no
  facts table at all.

Dates a model will cite must also pass the overlay's identifier gate, so the
date-bearing skills get the provenance handoff at
``$HERMES_HOME/.smd/pre_run/<skill>.json`` (``shared/pre_run_handoff.py``
accepts dates and ``(matterNumber, dates)`` records, and nothing else).

Skill identity
--------------
The scheduler stages this file to ``<profile>/scripts/<skill>/pre_run.py`` and
runs it with cwd = that directory, so the skill name is the cwd basename. That
keeps every stamped copy byte-identical.

Verification hook
-----------------
``pre_run.py --assume-empty`` skips the Smokeball probe and behaves as if the
seat were empty — it exercises the heartbeat write + suppress path end-to-end
on a live Machine without needing an actually-empty tenant. The cron daemon
never passes arguments, so this path cannot trigger on a scheduled fire.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Timeout budget: the Hermes scheduler kills the whole script at 120s
# (cron/scheduler.py _DEFAULT_SCRIPT_TIMEOUT); the probe gets well under that.
_PROBE_TIMEOUT_SECONDS = 45
_HEARTBEAT_TIMEOUT_SECONDS = 10

_CONNECTOR_PYTHON_DEFAULT = "/opt/connectors/smokeball/.venv/bin/python"

# Runs inside the connector venv, where smokeball_connector IS importable and
# build_client_from_env() owns auth (token mint + refresh-token self-heal).
# Envelope parsing is deliberately defensive: the live /matters list shape is
# connect-time-unverified, so an unrecognized shape reports null → wake.
_PROBE_SNIPPET = """\
import json
from smokeball_connector.client import build_client_from_env

r = build_client_from_env().get("/matters", Status="Open", Limit=1)
items = None
if isinstance(r, list):
    items = r
elif isinstance(r, dict):
    for key in ("items", "value", "results", "matters", "data"):
        v = r.get(key)
        if isinstance(v, list):
            items = v
            break
print(json.dumps({"openMatterCount": len(items) if items is not None else None}))
"""


def _emit(wake: bool, facts: dict | None = None) -> int:
    """Print the gate line. ``wakeAgent`` stays the first key of the LAST line.

    The overlay's ``bootstrap/cron_materialize.py`` reads only ``wakeAgent``
    from the last stdout line and then injects the whole of stdout into the
    woken turn verbatim as its Script Output, so facts ride the same line the
    boolean does. Anything but ``{"wakeAgent": false}`` is a wake.
    """
    payload: dict = {"wakeAgent": wake}
    if wake and facts:
        payload["memo_facts"] = facts
    print(json.dumps(payload))
    return 0


def _skill_name() -> str:
    return Path.cwd().name or "unknown-skill"


def probe_open_matter_count() -> int | None:
    """Return the open-matter count, or None when it cannot be determined."""
    connector_python = os.environ.get("SMD_CONNECTOR_VENV_PYTHON", _CONNECTOR_PYTHON_DEFAULT)
    if not Path(connector_python).exists():
        sys.stderr.write(f"[pre_run] connector python missing: {connector_python}\n")
        return None
    try:
        result = subprocess.run(  # noqa: S603 - connector-venv interpreter and a module-constant snippet; no shell
            # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args — argv[0] is the module-constant connector-venv interpreter, overridable only via SMD_CONNECTOR_VENV_PYTHON, which comes from the Machine's own boot env (same trust domain as this file; the test seam). The snippet argument is a module constant; no request/agent-controlled data reaches argv.
            [connector_python, "-c", _PROBE_SNIPPET],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        sys.stderr.write("[pre_run] smokeball probe timed out\n")
        return None
    except Exception as exc:  # noqa: BLE001 — any probe failure → unknown → wake
        sys.stderr.write(f"[pre_run] smokeball probe failed: {exc}\n")
        return None
    if result.returncode != 0:
        sys.stderr.write(f"[pre_run] smokeball probe exit {result.returncode}: {(result.stderr or '').strip()[:500]}\n")
        return None
    try:
        payload = json.loads((result.stdout or "").strip().splitlines()[-1])
        count = payload.get("openMatterCount")
    except Exception:  # noqa: BLE001 — malformed probe output → unknown → wake
        sys.stderr.write("[pre_run] smokeball probe output unparseable\n")
        return None
    if isinstance(count, bool) or not isinstance(count, int):
        return None
    return count


# ---------------------------------------------------------------------------
# Derived memo facts (ss-console #2873). See the module docstring for why.
# ---------------------------------------------------------------------------

# The facts pull replaces the probe for these skills rather than running beside
# it: it returns the open-matter count as a by-product, and the scheduler kills
# the whole script at 120s, so a second subprocess would buy nothing and spend
# half the budget.
_FACTS_TIMEOUT_SECONDS = 90

#: Matters whose memos one pull reads. Beyond it the model is told the view is
#: truncated rather than handed a silent subset (pilot runs single digits).
_FACTS_MATTER_CAP = 40

#: Facts per matter. A capture list this long is already a review question.
_FACTS_PER_MATTER_CAP = 12

_MEMO_PAGE_LIMIT = 200

#: Which skill gets which fact. A skill absent here emits a bare wake, which is
#: the whole of its behaviour before this section existed.
_FACT_SKILLS = {
    "service-confirmation-watcher": "captured_file_ids",
    "motion-calendar-tracker": "last_surface",
    "discovery-response-tracker": "extension_candidates",
}

#: The skills whose facts are DATES the model will render, and which therefore
#: need the provenance handoff. ``captured_file_ids`` are ids, never rendered
#: as a date, so the service watcher writes no handoff.
_HANDOFF_SKILLS = ("motion-calendar-tracker", "discovery-response-tracker")

_PROVENANCE_MARK = "[Operator]"

#: The service watcher's own capture line: "... fileId <id> recorded."
#: (``skills/service-confirmation-watcher/references/output-format.md``).
_FILE_ID_RE = re.compile(r"\bfileId\s+([A-Za-z0-9][A-Za-z0-9._:-]{0,127})")

#: The motion tracker's own surface memo opens "Motion calendar assembled for
#: ..." (``skills/motion-calendar-tracker/references/output-format.md``).
_MOTION_SURFACE_MARKER = "motion calendar assembled"

#: An extension is usually papered by a PERSON, not by the Operator, so these
#: are matched across every memo on the matter rather than only stamped ones.
_EXTENSION_MARKERS = ("extension", "extend", "stipulation", "stipulated")

_ISO_DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

#: An atom: an id, a number, a date. No whitespace, so no sentence. This is the
#: shape check that keeps memo prose on the far side of the process boundary.
_ATOM_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:@#/-]{0,127}\Z")

#: Stricter still, for the values that reach a URL path or name a record.
_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")

_MEMO_ENVELOPE_KEYS = ("value", "items", "results", "memos", "data")
_MATTER_ENVELOPE_KEYS = ("items", "value", "results", "matters", "data")

# Loads THIS file in the connector venv and calls one function, so the code the
# tests exercise in-process is the code that runs against the live tenant. The
# alternative — extraction written twice, once here and once as snippet text —
# is two implementations of one rule, and the one nothing runs is the one that
# drifts. argv[1] is this file's path, argv[2] the skill name.
_FACTS_SNIPPET = """\
import importlib.util
import json
import sys

spec = importlib.util.spec_from_file_location("pre_run_gate_facts", sys.argv[1])
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

from smokeball_connector.client import build_client_from_env

print(json.dumps(gate.pull_facts_payload(build_client_from_env(), sys.argv[2])))
"""


def _is_iso_day(value: object) -> bool:
    """True for a real calendar day written ``YYYY-MM-DD``. 2026-13-45 is not one."""
    if not isinstance(value, str) or not _ISO_DAY_RE.fullmatch(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _iso_days(text: str) -> list[str]:
    """Every ISO day in ``text``, first-seen order, deduped. Dates, never the sentence."""
    return [day for day in dict.fromkeys(_ISO_DAY_RE.findall(text)) if _is_iso_day(day)]


def _atoms_only(value: object) -> bool:
    """True when ``value`` holds nothing but atoms, numbers, booleans and nulls.

    The falsifier for "no memo prose crosses the boundary", enforced rather
    than asserted. A sentence carries whitespace and fails :data:`_ATOM_RE`, so
    a row carrying one is dropped WHOLE rather than trimmed — a trimmed row
    would be a value nobody chose.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return True
    if isinstance(value, str):
        return bool(_ATOM_RE.match(value))
    if isinstance(value, list):
        return all(_atoms_only(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _atoms_only(item) for key, item in value.items())
    return False


def _listed(payload: object, keys: tuple) -> list | None:
    """The list inside a HATEOAS envelope, a bare list, or None for a shape we do not know."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return None


def _memo_body(memo: dict) -> str:
    """The memo's text. ``plainText`` when the connector kept it, the RTF rendering
    otherwise — markers survive either, and neither is ever emitted."""
    for key in ("plainText", "PlainText", "text", "Text"):
        value = memo.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _memo_id(memo: dict) -> str | None:
    for key in ("id", "Id", "memoId"):
        value = memo.get(key)
        if isinstance(value, str) and _ID_RE.match(value):
            return value
    return None


def _memo_day(memo: dict) -> str | None:
    """The memo's creation DAY. The day is what a surface renders and what the
    identifier gate reads as a date; a timestamp would be neither."""
    for key in ("createdDate", "CreatedDate", "dateCreated", "created"):
        value = memo.get(key)
        if isinstance(value, str) and _is_iso_day(value[:10]):
            return value[:10]
    return None


def _is_operator_memo(body: str) -> bool:
    return body.lstrip().startswith(_PROVENANCE_MARK)


def _captured_file_ids(memos: list) -> list[str]:
    """Every fileId a prior ``[Operator]`` capture memo already recorded, so the
    scan can dedup on ``(matter, defendant, fileId)`` without reading a memo."""
    found: list[str] = []
    for memo in memos:
        body = _memo_body(memo)
        if not _is_operator_memo(body):
            continue
        for raw in _FILE_ID_RE.findall(body):
            value = raw.rstrip(".,;:)")
            if _ID_RE.match(value) and value not in found:
                found.append(value)
    return found


def _latest_surface_day(memos: list) -> str | None:
    """The day of the most recent ``[Operator]`` memo carrying the motion
    tracker's own surface marker, or None. A day, never the surface."""
    latest: str | None = None
    for memo in memos:
        body = _memo_body(memo)
        if not _is_operator_memo(body) or _MOTION_SURFACE_MARKER not in body.lower():
            continue
        day = _memo_day(memo)
        if day is not None and (latest is None or day > latest):
            latest = day
    return latest


def _extension_candidates(memos: list) -> list[dict]:
    """Memos that MENTION an extension or stipulation: the memo id and the ISO
    days in it. A candidate, never a conclusion — the skill cites the id and
    asks, because a memo that says the word is not a memo that grants one."""
    candidates: list[dict] = []
    for memo in memos:
        body = _memo_body(memo)
        lowered = body.lower()
        if not any(marker in lowered for marker in _EXTENSION_MARKERS):
            continue
        memo_id = _memo_id(memo)
        if memo_id is None:
            continue
        candidates.append({"memoId": memo_id, "dates": _iso_days(body)})
    return candidates


def _capped(fact: str, values: list) -> dict:
    kept = values[:_FACTS_PER_MATTER_CAP]
    row: dict = {fact: kept}
    if len(values) > len(kept):
        row["truncated"] = True
    return row


def derive_matter_facts(skill: str, payload: object) -> dict:
    """One matter's derived facts for ``skill``. Atoms only, by construction.

    A skill with no entry in :data:`_FACT_SKILLS` gets ``{}`` and therefore a
    bare wake. An envelope this code does not recognise is reported as
    ``unreadable`` rather than as an empty result, because "no prior capture"
    and "could not read the memos" are opposite instructions to the model.
    """
    fact = _FACT_SKILLS.get(skill)
    if fact is None:
        return {}
    memos = _listed(payload, _MEMO_ENVELOPE_KEYS)
    if memos is None:
        return {"unreadable": True}
    memos = [memo for memo in memos if isinstance(memo, dict)]
    if fact == "captured_file_ids":
        return _capped(fact, _captured_file_ids(memos))
    if fact == "extension_candidates":
        return _capped(fact, _extension_candidates(memos))
    return {"last_surface": _latest_surface_day(memos)}


def _matter_row(client, matter: object, skill: str) -> dict | None:
    """One matter's row, or None when it cannot be read or is not atoms."""
    if not isinstance(matter, dict):
        return None
    matter_id = matter.get("id") or matter.get("Id")
    if not isinstance(matter_id, str) or not _ID_RE.match(matter_id):
        return None
    try:
        memos = client.get("/matters/" + matter_id + "/memos", Limit=_MEMO_PAGE_LIMIT)
    except Exception as exc:  # noqa: BLE001 - one matter failing degrades that row, never the run
        # The exception TYPE only: a message can quote the payload it failed on.
        sys.stderr.write("[pre_run] memo read failed for one matter: " + type(exc).__name__ + "\n")
        return None
    row = derive_matter_facts(skill, memos)
    row["matterId"] = matter_id
    number = matter.get("number") or matter.get("Number")
    if isinstance(number, str) and _ATOM_RE.match(number):
        row["matterNumber"] = number
    return row if _atoms_only(row) else None


def pull_facts_payload(client, skill: str) -> dict:
    """Read every open matter's memos and return DERIVED FACTS only.

    Runs in the connector venv (:data:`_FACTS_SNIPPET`), which is what keeps
    memo prose out of the pre_run process entirely. Tests drive it in-process
    with a stub client: same function, so the tested behaviour is the shipped
    behaviour.
    """
    matters = _listed(client.get("/matters", Status="Open", Limit=_FACTS_MATTER_CAP + 1), _MATTER_ENVELOPE_KEYS)
    if matters is None:
        return {"envelopeUnknown": True}
    payload: dict = {
        "skill": skill,
        "openMatterCount": len(matters),
        "matters": [],
        "mattersTruncated": len(matters) > _FACTS_MATTER_CAP,
        "unreadableMatters": 0,
    }
    for matter in matters[:_FACTS_MATTER_CAP]:
        row = _matter_row(client, matter, skill)
        if row is None:
            payload["unreadableMatters"] += 1
        else:
            payload["matters"].append(row)
    return payload


def fetch_memo_facts(skill_name: str) -> tuple[int | None, dict | None]:
    """``(open-matter count, derived facts)`` from ONE connector-venv subprocess.

    Any failure at all returns ``(None, None)``: the seat wakes, fact-free,
    exactly as it would with no facts table. It never suppresses and never
    raises — a routine taken down to protect a convenience is a worse outcome
    than the refusals this whole section exists to remove.
    """
    connector_python = os.environ.get("SMD_CONNECTOR_VENV_PYTHON", _CONNECTOR_PYTHON_DEFAULT)
    if not Path(connector_python).exists():
        sys.stderr.write(f"[pre_run] connector python missing: {connector_python}\n")
        return None, None
    try:
        result = subprocess.run(  # noqa: S603 - connector-venv interpreter, a module-constant snippet and two scheduler-derived argv values; no shell
            # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args — argv[0] is the module-constant connector-venv interpreter, overridable only via SMD_CONNECTOR_VENV_PYTHON from the Machine's own boot env (same trust domain as this file; the test seam). argv[1] is a module constant, argv[2] this file's own resolved path, argv[3] the cwd basename the scheduler staged. No request/agent-controlled data reaches argv, and there is no shell.
            [connector_python, "-c", _FACTS_SNIPPET, str(Path(__file__).resolve()), skill_name],
            capture_output=True,
            text=True,
            timeout=_FACTS_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 — any pull failure → no facts → plain wake
        sys.stderr.write(f"[pre_run] memo-facts pull failed: {exc}\n")
        return None, None
    if result.returncode != 0:
        sys.stderr.write(f"[pre_run] memo-facts pull exit {result.returncode}: {(result.stderr or '').strip()[:500]}\n")
        return None, None
    try:
        payload = json.loads((result.stdout or "").strip().splitlines()[-1])
    except Exception:  # noqa: BLE001 — malformed pull output → no facts → plain wake
        sys.stderr.write("[pre_run] memo-facts output unparseable\n")
        return None, None
    # Re-checked HERE, on the reading side: the writer ran in another process
    # and a guarantee the reader does not verify is not a guarantee.
    if not isinstance(payload, dict) or not _atoms_only(payload):
        sys.stderr.write("[pre_run] memo-facts payload is not atoms; dropping it\n")
        return None, None
    count = payload.get("openMatterCount")
    if isinstance(count, bool) or not isinstance(count, int):
        return None, None
    return count, payload


# ---------------------------------------------------------------------------
# Provenance handoff — the dates the woken session is allowed to render
# ---------------------------------------------------------------------------

_HANDOFF_STARTED_AT = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

#: The overlay reader's own caps (``shared/pre_run_handoff.py``). Held here too,
#: so an oversized file is never written rather than written and then refused.
_HANDOFF_MAX_DATES = 200
_HANDOFF_MAX_RECORDS = 100

#: Row keys that are identifiers, not facts: never scanned for dates.
_HANDOFF_SKIP_KEYS = ("matterId", "matterNumber")


def _iter_days(value: object) -> list[str]:
    """Every ISO day anywhere inside ``value``. The facts are atoms already, so
    this walks a handful of ids and dates, not a document."""
    if isinstance(value, str):
        return [value] if _is_iso_day(value) else []
    if isinstance(value, list):
        return [day for item in value for day in _iter_days(item)]
    if isinstance(value, dict):
        return [day for item in value.values() for day in _iter_days(item)]
    return []


def _row_days(row: dict) -> list[str]:
    days: list[str] = []
    for key, value in row.items():
        if key in _HANDOFF_SKIP_KEYS:
            continue
        for day in _iter_days(value):
            if day not in days:
                days.append(day)
    return days


def _handoff_projection(skill: str, facts: dict) -> dict:
    """Dates and ``(matterNumber, dates)`` records: what the overlay reader takes.

    Records seed as ASSOCIATIONS, so a seeded matter number cannot certify a
    date read off a different matter — the whole reason code, not the model,
    did the cross-matter read.
    """
    dates: list[str] = []
    records: list[dict] = []
    matter_ids: list[str] = []
    rows = facts.get("matters")
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        days = _row_days(row)
        matter_id = row.get("matterId")
        if isinstance(matter_id, str):
            matter_ids.append(matter_id)
        for day in days:
            if day not in dates:
                dates.append(day)
        number = row.get("matterNumber")
        if isinstance(number, str) and days and len(records) < _HANDOFF_MAX_RECORDS:
            records.append({"matterNumber": number, "dates": days})
    return {
        "skill": skill,
        "started_at": _HANDOFF_STARTED_AT,
        "dates": dates[:_HANDOFF_MAX_DATES],
        "matter_ids": matter_ids,
        "records": records,
    }


def write_pre_run_handoff(skill: str, facts: dict) -> None:
    """Hand the woken session the dates its facts carry, so the overlay's
    identifier gate accepts them when the surface renders one.

    Best-effort by contract, like its siblings in the bespoke gates: any
    failure writes a line to stderr and leaves stdout and the wake untouched.
    ``skill`` is checked against :data:`_HANDOFF_SKILLS` before it reaches a
    path, so it can only ever name a file inside the handoff directory.
    """
    if skill not in _HANDOFF_SKILLS:
        return
    try:
        record = _handoff_projection(skill, facts)
        if not record["dates"]:
            return  # nothing to certify; an empty handoff would consume itself for nothing
        directory = Path(os.environ.get("HERMES_HOME") or "/opt/data") / ".smd" / "pre_run"
        # Modes are set AT CREATION, never by a follow-up chmod: umask can only
        # remove bits, so the result is at most 0700/0600 and there is no window
        # in which the file is readable by anyone else. It names the matters the
        # firm is working on.
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = directory / ("." + skill + ".json.tmp")
        # O_EXCL so the open cannot follow a pre-planted symlink, preceded by an
        # unlink so a temp file left by a crashed run cannot wedge the writer.
        tmp.unlink(missing_ok=True)
        handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        os.replace(tmp, directory / (skill + ".json"))
    except Exception as exc:  # noqa: BLE001 — never change stdout or the wake
        sys.stderr.write("[pre_run] handoff write failed (" + str(exc) + ")\n")


def _append_wake_row(*, verb: str, action_type: str, skill_name: str, basis: str) -> bool:
    """Append one wake row through the broker's uid-gated verb. True only on ack.

    ONE writer for both halves of the gate, deliberately. The suppress row and
    the wake row must carry the same fields or a reader cannot diff them, and
    two near-identical functions is exactly how that drifts.
    """
    socket_path = os.environ.get("SMD_AUDIT_BROKER_SOCKET") or os.environ.get("SMD_WORKSPACE_BROKER_SOCKET")
    if not socket_path:
        sys.stderr.write("[pre_run] no broker socket in env; cannot heartbeat\n")
        return False
    request = {
        "action": verb,
        "row": {
            "action_type": action_type,
            "actor": "agent",
            "actor_role": "agent",
            "skill_name": skill_name,
            "metadata": json.dumps(
                {
                    "decision_basis": basis,
                    "platform": "cron-pre-run",
                    "customer": os.environ.get("CUSTOMER_SLUG", ""),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        },
    }
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(_HEARTBEAT_TIMEOUT_SECONDS)
            sock.connect(socket_path)
            sock.sendall(json.dumps(request).encode("utf-8") + b"\n")
            raw = b""
            while not raw.endswith(b"\n"):
                chunk = sock.recv(4096)
                if not chunk:
                    break
                raw += chunk
        response = json.loads(raw.decode("utf-8"))
        if response.get("ok") is True:
            return True
        sys.stderr.write(f"[pre_run] heartbeat rejected: {response}\n")
        return False
    except Exception as exc:  # noqa: BLE001 — any heartbeat failure → wake
        sys.stderr.write(f"[pre_run] heartbeat write failed: {exc}\n")
        return False


def write_suppressed_wake_heartbeat(skill_name: str) -> bool:
    """Append the SUPPRESSED_WAKE row via the broker. True only on ack."""
    return _append_wake_row(
        verb="suppressed_wake_append",
        action_type="SUPPRESSED_WAKE",
        skill_name=skill_name,
        basis="empty_seat:no_open_matters",
    )


def write_emitted_wake_heartbeat(skill_name: str, basis: str) -> bool:
    """Append the EMITTED_WAKE row via the broker (ss-console #2253, #2498).

    BEST-EFFORT, and its contract INVERTS its sibling's on purpose. A failed
    suppress heartbeat escalates to a wake, because a silent suppress cannot be
    told from a broken gate. Here the wake is already the decision, so every
    failure is swallowed — a wake that a failed audit write could delay would
    be a gate made of observability.

    Why the shared gate needed this at all (#2498): the bespoke gates got the
    wake half in #2253 and this template never did, so the eight routines on it
    left a row on every quiet tick and nothing on the ticks they fired. The one
    tick that mattered was the one tick with no row — the same shape that made a
    fabricated escalation on 2026-08-10 discoverable only by reading a mailbox.
    """
    return _append_wake_row(
        verb="emitted_wake_append",
        action_type="EMITTED_WAKE",
        skill_name=skill_name,
        basis=basis,
    )


def decide_and_emit(count: int | None, skill_name: str, facts: dict | None = None) -> int:
    """Pure-ish core: count → wake/suppress emission (heartbeat before either).

    ``facts`` ride the WAKE line only. A suppress has no session to hand them
    to, and the failed-suppress wake is a broken-gate wake: handing it facts
    would dress a failure as a normal morning.
    """
    if count is None or count > 0:
        # Mirrors the suppress path's field set so the two are diffable, and
        # keeps the two wake reasons apart: a hydrated seat and a seat we could
        # not probe both wake, and only one of them is healthy.
        write_emitted_wake_heartbeat(
            skill_name,
            "probe_unavailable:open_matter_count_unknown" if count is None else "hydrated_seat:open_matters_present",
        )
        return _emit(wake=True, facts=facts)
    if not write_suppressed_wake_heartbeat(skill_name):
        # Mirror-don't-gate: a silent suppress with no audit trail is
        # indistinguishable from a broken pre_run. Wake instead — the full
        # agent run is observable and the failure becomes visible.
        #
        # No EMITTED_WAKE attempt here, matching deadline-miss-escalator's
        # `suppress_heartbeat_failed_fail_open`: a write to this very writer
        # just failed, so a second one is a delay, not a record.
        return _emit(wake=True)
    return _emit(wake=False)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    skill_name = _skill_name()
    if "--assume-empty" in argv:
        sys.stderr.write("[pre_run] --assume-empty: skipping smokeball probe\n")
        return decide_and_emit(0, skill_name)
    if skill_name in _FACT_SKILLS:
        # One subprocess, both answers: the facts pull counts the open matters
        # on its way past them, so the probe would be a second round trip for a
        # number this already has.
        count, facts = fetch_memo_facts(skill_name)
        if facts is not None:
            write_pre_run_handoff(skill_name, facts)
        return decide_and_emit(count, skill_name, facts)
    return decide_and_emit(probe_open_matter_count(), skill_name)


if __name__ == "__main__":
    sys.exit(main())
