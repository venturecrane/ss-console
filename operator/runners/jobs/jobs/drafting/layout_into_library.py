"""A firm's layout rule applied into its accepted library Word file, as a new
version the ledger records as written by code (design 2026-11-06, piece 5,
"How it is built", Unified/Layout).

DARK BEHIND ``logic_in_code.layout_into_library`` (observe on every seat).

* OBSERVE (absent, or ``observe``): the job computes what it WOULD apply, the
  rule values the order of precedence gives to a rule that the accepted file
  does not yet carry (``layout_precedence``, ``in_file`` False), and records it
  in the record beside the document (``into_library``). It writes nothing: the
  rules are laid onto the document as they were before this module existed.
* ENFORCE: those values are edited into a copy of the accepted file with the
  edit the rules already use on a firm base (``firm_rules.onto_base``, proven to
  touch only the leaves each key names), the result is checked as a firm's own
  save is (``library_edit.check``: the fixed words and marked places intact),
  filed in the library as a new version of the same file, and left for the root
  daemon, which hands it to the root writer through the same ``library_accept``
  spool a firm's own save uses, ``accepted_by`` ``layout_rule:<ids>``. The
  ledger then names the version code wrote, which is how a later firm edit to
  the same property is placed against the rule.

An unreadable switch is not "on": this writes into the firm's library, so the
door that fails closed is the one that writes nothing. Nothing is written for a
lane proof run, for a job that is itself accepting a firm's save (one new
version per job), or for a file that is not a marked file.

THE DAEMON'S HALF (after the child): :func:`alert_unplaced` pages SMD with
:data:`UNPLACED` for every rule the ledger could not place against a firm's
edit (the rule stood), and :func:`hand_over` hands a written version over.
No model call.
"""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from operator_connector_sdk.library import MARKED, LibraryUnavailable
from operator_connector_sdk.library.caller import RECORD_NAME
from operator_connector_sdk.library.resolver import ATTORNEY_FILE

from . import layout_precedence as LP

logger = logging.getLogger(__name__)

SWITCH = "layout_into_library"
#: What the job leaves for the root daemon when it wrote a rule version.
RULE_JSON = "library-rule.json"
RULE_DOCX = "library-rule.docx"
#: The proof code: a rule the ledger could not place against a firm's edit, or
#: a rule version the root writer did not accept. SMD only; the firm is told nothing.
UNPLACED = "layout_rule_unplaced"


def _mode() -> tuple[str, str]:
    from smokeball_connector import logic_in_code as L

    sw = L.load_switch(SWITCH)
    return sw.mode, sw.error or ""


def would_apply(prec: LP.Precedence) -> list[dict[str, Any]]:
    """Each rule value the order gives to a rule and the file does not carry yet."""
    return [
        {"rule_id": w["rule_id"], "key": key, "value": w["value"]}
        for key, w in sorted(prec.winners.items())
        if w.get("winner") == LP.FIRM_RULE and w.get("in_file") is False
    ]


def _laid(data: bytes, layout: dict[str, Any]) -> tuple[bytes, list[str]]:
    from .firm_rules import onto_base

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "library.docx"
        path.write_bytes(data)
        problems = onto_base(path, layout)
        return path.read_bytes(), problems


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _acceptance(res: Any, version: str, data: bytes, ids: list[str]) -> dict[str, Any]:
    from ..library_edit import check

    f, attorney = res.file, None
    if f.scope == ATTORNEY_FILE and res.signer is not None:
        attorney = res.signer.key
    return {
        "kind": res.kind,
        "attorney": attorney,
        "file_id": f.file_id,
        "version": version,
        "contract": f.contract,
        "accepted_by": LP.LAYOUT_RULE + ",".join(ids),
        "accepted_at": _now(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "fixed_strings": sorted(check.fixed_of(res.data or b"")),
        "replaces": {"file_id": f.file_id, "version": f.version, "sha256": f.sha256},
        "chart_source": f.chart_source,
    }


def _why_not(run: Any, made_in: Any, mode: str, error: str) -> str:
    from .. import proof_mode

    if error:
        return "the switch could not be read; nothing is written"
    if mode != "enforce":
        return "observe"
    if proof_mode.active():
        return "a lane proof run writes nothing"
    if made_in.edit is not None:
        return "this job is accepting the firm's own save; one new version per job"
    f = made_in.resolution.file
    if f is None or f.contract != MARKED:
        return "the accepted file is not a marked file"
    return ""


def _write(run: Any, made_in: Any, items: list[dict[str, Any]], store: Any) -> tuple[str, str]:
    """``(version, why not)``: the rule version filed and left for the daemon."""
    from ..library_edit import check

    res = made_in.resolution
    new, problems = _laid(res.data, {i["key"]: i["value"] for i in items})
    if problems or new == res.data:
        return "", "the rules could not be laid into the file: " + "; ".join(problems[:2])
    codes = check.problems(run.cls, MARKED, res.data, check.clean(new))
    if codes:
        return "", "the file with the rules laid in fails the library file's own check: " + ",".join(codes)
    new = check.clean(new)
    try:
        filed = store.file_new_version(res.file.file_id, new, expected_version=res.file.version)
    except LibraryUnavailable as exc:
        return "", f"the library did not take the new version ({exc})"
    if not filed.version_after:
        return "", "the library took the new version and did not name it"
    acc = _acceptance(res, filed.version_after, new, sorted({i["rule_id"] for i in items}))
    folder = Path(run.data)
    (folder / RULE_DOCX).write_bytes(new)
    doc = {"note": {"status": "used", "version": filed.version_after, "code": ""}, "acceptance": acc}
    (folder / RULE_JSON).write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return filed.version_after, ""


def settle(run: Any, made_in: Any, prec: LP.Precedence, store: Any) -> dict[str, Any]:
    """The record's ``into_library`` block, writing the version only on enforce."""
    mode, error = _mode()
    items = would_apply(prec)
    block: dict[str, Any] = {"mode": mode, "would_apply": items, "written": False, "version": "", "reason": ""}
    if not items:
        block["reason"] = "the file already carries every rule value it can be read for"
        return block
    block["reason"] = _why_not(run, made_in, mode, error)
    if block["reason"]:
        return block
    version, why = _write(run, made_in, items, store)
    block.update(written=bool(version), version=version, reason=why)
    return block


# ---- the daemon's half ---------------------------------------------------------------------
def _record(d: Any, job_id: str) -> dict[str, Any]:
    try:
        doc = json.loads((d.job_dir(job_id) / "data" / RECORD_NAME).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def alert_unplaced(d: Any, job_id: str) -> None:
    """After the child: each rule the ledger could not place, by rule and key, ids only."""
    from ..proof import watch

    block = _record(d, job_id).get(LP.RECORD_KEY)
    unplaced = (block or {}).get("unplaced") if isinstance(block, dict) else None
    ids = [f"{u.get('rule_id')}:{u.get('key')}" for u in unplaced or () if isinstance(u, dict)]
    if ids and not watch.in_proof_run(d):
        watch.alert(d, job_id, UNPLACED, "the rule stood: " + ",".join(ids)[:280])


def hand_over(d: Any, job_id: str) -> str:
    """After the child: a written rule version, handed to the root writer."""
    from ..library_edit import handoff

    return handoff.hand_over(d, job_id, names=(RULE_JSON, RULE_DOCX), code=UNPLACED, what="the layout rules' version")


__all__ = ["RULE_DOCX", "RULE_JSON", "SWITCH", "UNPLACED", "alert_unplaced", "hand_over", "settle", "would_apply"]
