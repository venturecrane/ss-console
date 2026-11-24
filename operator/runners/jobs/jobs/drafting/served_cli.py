"""SMD's command for a response job's served sets: never hand-written JSON as root.

    python -m jobs.drafting.served template <job_id>
    python -m jobs.drafting.served write <job_id> --from <file>
    python -m jobs.drafting.served replan <job_id> --from <file> --by <who> --why <what was wrong>
    python -m jobs.drafting.served show <job_id>
    python -m jobs.drafting.served resume <job_id> --why <what was named>

Root, on the seat, in the runner daemon's environment. Runbook:
``docs/runbooks/operator/drafting-served-sets.md``.

``write``, ``replan`` and ``resume`` refuse a job that is not a response job,
or whose ledger row is running or delivered. ``write`` and ``replan`` check
the file with the job's own validator, against our client's names in the job's
record, and write it into the runner-owned job dir without following a link:
a fresh file opened ``O_EXCL | O_NOFOLLOW``, then renamed over the target,
which is refused if it is itself a link. ``replan`` replaces a plan already
frozen from an earlier file, only while no document has been composed, and
records who and why.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import served_form
from .forms.checked import compact
from .outcome import DraftingFailed
from .served import FILE, OPERATOR, PLAN, STOP_NOTE, ServedSet, clients_of, parse, read, to_file

USAGE = (__doc__ or "").split("\n\n")[1]
from ..daemon import RUN_DIR_ENV, SOCKET_ENV  # noqa: E402 - the daemon's own names, one home

#: The ledger states a served-sets command may act on: the job is not running and not delivered.
ACTABLE = frozenset({"failed", "submitted"})
#: Who replaced a job's frozen served plan, and why (``replan``).
REPLANS = "served-replans.jsonl"


#: What the command needs from the runner daemon's environment; a bare root
#: shell (``fly ssh console``, ``env -i``) takes the daemon's own values, by name
#: only (``hand_delivery.daemon_env``), as ``deliver_by_smd`` does.
ENV = (SOCKET_ENV, RUN_DIR_ENV)


def load_daemon_env(proc: Path = Path("/proc")) -> None:
    from .. import hand_delivery

    for k, v in hand_delivery.daemon_env([n for n in ENV if not os.environ.get(n)], proc=proc).items():
        os.environ[k] = v


def job_dir_for(job_id: str) -> Path:
    """The drafting job's directory on the seat, from its id (a ULID only)."""
    from .. import job_lanes, seat_layout

    if not re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", job_id or ""):
        raise SystemExit(f"served: {job_id!r} is not a job id")
    run = Path(os.environ.get(RUN_DIR_ENV) or seat_layout.JOBS_RUN)
    return run / job_lanes.LANE_JOBS_DIR["drafting"] / job_id


def template() -> dict[str, Any]:
    """A served.json in its closed shape, every value to replace: a numbered
    set, then a form interrogatory set with its own keys (``served_form.py``)."""
    return {
        "sets": [
            {
                "set_type": "special_interrogatories",
                "set_number": 1,
                "propounding_party": "<the party that served it, as the set names it>",
                "responding_party": "<our client, as the set names it>",
                "request_count": 1,
                "request_text_path": "<a file beside this one holding the requests, verbatim>",
                "drafted_by": "operator",
            },
            served_form.template(),
        ]
    }


def _line(s: ServedSet) -> str:
    who = "the Operator drafts it" if s.drafted_by == OPERATOR else "SMD drafts it"
    if not s.checked:
        return f"{s.label}: {s.request_count} requests, {who}"
    boxes = ", ".join(s.definition_boxes) or "none"
    extra = "; extra interrogatories attached" if s.additions else ""
    return (
        f"{s.label}: {s.form} [{s.form_revision}], {s.request_count} numbers checked "
        f"({compact(s.checked)}), definition boxes {boxes}{extra}, {who}"
    )


def summary(found: list[ServedSet]) -> list[str]:
    return [_line(s) for s in found]


def _client(client: Any) -> Any:
    from ..broker_client import BrokerClient

    return client or BrokerClient(os.environ.get(SOCKET_ENV) or "")


def check_job(job_id: str, jd: Path, client: Any) -> None:
    """A response job, not running and not delivered, whose dir is a real directory."""
    if jd.is_symlink() or not jd.is_dir():
        raise SystemExit(f"served: no drafting job directory {jd}")
    try:
        cls = json.loads((jd / "job.json").read_text(encoding="utf-8")).get("document_class")
    except (OSError, ValueError):
        cls = None
    if cls != "discovery_response":
        raise SystemExit(f"served: job {job_id} is a {cls or 'unknown'} job, not discovery responses")
    row = (client._request({"action": "drafting_job_status", "job_id": job_id}) or {}).get("job") or {}
    if row.get("state") not in ACTABLE:
        raise SystemExit(
            f"served: job {job_id} is {row.get('state') or 'unknown'}; only a stopped job takes served sets"
        )


def _write_root(jd: Path, name: str, text: str) -> Path:
    """``text`` into ``jd/name`` without following a link the runner could plant."""
    target = jd / name
    if target.is_symlink():
        raise SystemExit(f"served: {target} is a link; not written")
    tmp = jd / f".{name}.{secrets.token_hex(6)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
    try:
        os.write(fd, text.encode("utf-8"))
        st = os.stat(jd)
        try:
            os.fchown(fd, st.st_uid, st.st_gid)
        except PermissionError:
            pass  # not root (a test or a laptop): the file keeps its writer as owner
        os.fchmod(fd, 0o644)
    finally:
        os.close(fd)
    os.replace(tmp, target)
    return target


def _validated(source: Path, jd: Path) -> list[ServedSet]:
    try:
        # An Operator form set is accepted here; the job re-checks it against the
        # firm's form policy when it freezes the plan (served.frozen).
        return parse(Path(source).read_bytes(), Path(source).resolve().parent, clients_of(jd / "data"), True)
    except DraftingFailed as exc:
        raise SystemExit(f"served: not written: {exc}") from None


def write(job_id: str, source: Path, jd: Path | None = None, client: Any = None) -> list[str]:
    """Validate SMD's file with the job's own validator and write it into the
    job dir as ``served.json`` (texts inlined), and say what was written and
    how to resume."""
    jd = jd or job_dir_for(job_id)
    check_job(job_id, jd, _client(client))
    found = _validated(source, jd)
    p = _write_root(jd, FILE, to_file(found))
    out = [f"wrote {p}", *summary(found)]
    if (jd / "data" / PLAN).is_file():
        out.append(f"note: this job already planned from an earlier file; replace that plan with: replan {job_id}")
    out.append(f'next: python -m jobs.drafting.served resume {job_id} --why "named the served sets"')
    return out


def _composed(jd: Path) -> bool:
    try:
        st = json.loads((jd / "data" / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return any(k == "compose" or k.endswith(":compose") for k in st)


def replan(job_id: str, source: Path, by: str, why: str, jd: Path | None = None, client: Any = None) -> list[str]:
    """Replace a job's frozen served plan with a corrected file, while no
    document has been composed: the hand file, the frozen plan and the unit
    plan are all replaced, and who and why are recorded."""
    jd = jd or job_dir_for(job_id)
    check_job(job_id, jd, _client(client))
    if _composed(jd):
        raise SystemExit(f"served: job {job_id} has composed a document; its plan stands. Draft the change by hand")
    data = jd / "data"
    if data.is_symlink() or not data.is_dir():
        raise SystemExit(f"served: {data} is not the job's own data directory; not written")
    found = _validated(source, jd)
    _write_root(jd, FILE, to_file(found))
    _write_root(data, PLAN, json.dumps({"sets": [asdict(s) for s in found]}, indent=1))
    state = data / "state.json"
    if state.is_file() and not state.is_symlink():
        st = json.loads(state.read_text(encoding="utf-8"))
        for k in ("units", "units_request", "units_caption_sourced", "plan"):
            st.pop(k, None)
        _write_root(data, "state.json", json.dumps(st, indent=1))
    log = data / REPLANS
    prior = log.read_text(encoding="utf-8") if log.is_file() and not log.is_symlink() else ""
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "by": by, "why": why, "sets": summary(found)}
    _write_root(data, REPLANS, prior + json.dumps(row) + "\n")
    return [f"replaced the served plan of {job_id}", *summary(found), f'next: resume {job_id} --why "{why}"']


def show(job_id: str, jd: Path | None = None) -> list[str]:
    jd = jd or job_dir_for(job_id)
    out = []
    note = jd / STOP_NOTE
    if note.is_file():
        n = json.loads(note.read_text(encoding="utf-8"))
        rules = (n.get("wording_rules") or {}).get("rules") or []
        out.append(f"stopped: {n.get('code')} ({len(rules)} frozen wording rules in {STOP_NOTE})")
    try:
        out += summary(read(jd))
    except DraftingFailed as exc:
        out.append(str(exc))
    return out


def main(argv: list[str] | None = None, client: Any = None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(prog="python -m jobs.drafting.served", usage=USAGE)
    ap.add_argument("verb", choices=("template", "write", "replan", "show", "resume"))
    ap.add_argument("job_id")
    ap.add_argument("--from", dest="source")
    ap.add_argument("--by", default="")
    ap.add_argument("--why", default="")
    a = ap.parse_args(argv)
    load_daemon_env()
    jd = job_dir_for(a.job_id)
    if a.verb == "template":
        print(json.dumps(template(), indent=1))
    elif a.verb == "write":
        if not a.source:
            ap.error("write needs --from <file>")
        print("\n".join(write(a.job_id, Path(a.source), jd, client)))
    elif a.verb == "replan":
        if not a.source or not a.by.strip() or not a.why.strip():
            ap.error("replan needs --from <file>, --by <who> and --why <what was wrong>")
        print("\n".join(replan(a.job_id, Path(a.source), a.by.strip(), a.why.strip(), jd, client)))
    elif a.verb == "show":
        print("\n".join(show(a.job_id, jd)))
    else:
        from ..broker_client import BrokerError

        if not a.why.strip():
            ap.error("resume needs --why: what was named")
        c = _client(client)
        check_job(a.job_id, jd, c)
        try:
            out = c._request({"action": "drafting_job_resume", "job_id": a.job_id, "reason": a.why.strip()})
        except BrokerError as exc:
            print(f"served: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(out, default=str))
    return 0
