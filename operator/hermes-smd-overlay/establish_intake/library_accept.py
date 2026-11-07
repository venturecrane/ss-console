"""Root-side writer of a firm's acceptance of a Word file for a kind of document.

WHAT IT DOES. Given an acceptance (the kind, the attorney or none, the library
file and version, the contract, the file's bytes, who said yes and when, and the
accepted file it says it replaces), it checks the acceptance, computes the bytes'
sha256, calls the pure :func:`shared.library_ledger.accept` for the new ledger,
and writes the vault so the spec applier installs both:

    vaults/<slug>/library/files/<sha256>.docx      the bytes, written FIRST
    vaults/<slug>/library/accepted.previous.json   the ledger before this write
    vaults/<slug>/library/accepted.json            the new ledger, written LAST

then waits for the applier's manifest to record the new ledger
(``library_source_digest``), the same honest ``installed`` versus
``accepted_pending_install`` answer the other intake scopes give.

REFUSED, AND NOTHING WRITTEN, when: the kind is not a slot of the document-kind
registry (:data:`DOCUMENT_CLASSES` and :data:`KIND_VARIANTS`, pinned to
``operator/contracts/document-kinds.yaml`` by ``tests/document-kinds-registry.test.ts``;
a kind with variants is accepted only as ``<kind>.<variant>``, checked by the
shared ``library_ledger.slot_problems``); the contract is not one the ledger
holds; the contract is ``prototypes`` (a demand reference file: the
no-client-text check reads a blanked form, and cannot yet judge a file that keeps
one sample paragraph of each kind, so accepting one is a later piece and is
refused, never passed unchecked); the bytes are not a Word package; the bytes fail the no-client-text
check (``establish_intake/blank_form.py``); the slot's accepted file is not the
one the acceptance says it replaces; or the ledger cannot hold the entry.

HOW IT IS REACHED (design 2026-11-06, piece 3b). A firm administrator chooses one
of the documents a "set up our ..." job listed; the broker checks the choice in
code (``workspace_broker/library_setup_choose.py``); the drafting lane's job
makes and files the library file; the broker spools a run in the shape of
:data:`SUBMISSION_FIELDS` (``library_setup_accept_submit``, root only), and
``EstablishIntake._process`` dispatches the ``library_accept`` scope here
(``_install_library``), which :func:`read_run` reads. All of it is behind the
seat's ``logic_in_code.library_setup`` switch; off, no run is ever spooled.

No model call. Claimed ``accepted_by`` is provenance, not authorization, as
``instructed_by`` is for the other scopes.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from establish_intake import blank_form
from establish_intake.intake import (
    STATUS_ACCEPTED_PENDING,
    STATUS_ERROR,
    STATUS_INSTALLED,
    STATUS_REJECTED,
)
from shared import library_ledger as L
from spec_applier import library as lib
from spec_applier.applier import SpecApplyError

SCOPE = "library_accept"
SUBMISSION = "submission.json"
COPY_NAME = "file.docx"

#: The document-kind registry's keys, in its order (``operator/contracts/document-kinds.yaml``).
DOCUMENT_CLASSES = (
    "discovery_set",
    "discovery_response",
    "demand_letter",
    "mediation_brief",
    "memo",
    "depo_outline",
    "letter",
)

#: The registry's variants, for the kinds that have them (the rest have none).
KIND_VARIANTS: dict[str, tuple[str, ...]] = {
    "demand_letter": ("pre_suit", "litigation"),
}
#: Every kind to its variants, the shape ``library_ledger.slot_problems`` reads.
REGISTRY: dict[str, tuple[str, ...]] = {k: KIND_VARIANTS.get(k, ()) for k in DOCUMENT_CLASSES}
#: Why a prototypes file is refused, in plain words (see the module docstring).
PROTOTYPES_REFUSED = (
    "a prototypes file (a demand reference) cannot be accepted yet: the no-client-text "
    "check cannot judge a file that keeps a sample paragraph of each kind"
)

MAX_FIXED_STRINGS = 200
MAX_FIXED_CHARS = 300

#: The run's ``submission.json``, rebuilt broker-side from a bounded set. The
#: file's bytes travel beside it as ``file.docx``, both broker-written (uid-checked).
SUBMISSION_FIELDS: dict[str, str] = {
    "scope": f'"{SCOPE}"',
    "phase": '"install"',
    "run_id": "the run's id",
    "created_at": "ISO 8601 UTC",
    "acceptance.kind": "a slot of operator/contracts/document-kinds.yaml: <kind> or <kind>.<variant>",
    "acceptance.attorney": "the attorney key, or null for the firm's own file",
    "acceptance.file_id": "the file in the library",
    "acceptance.version": "the accepted saved version",
    "acceptance.contract": '"styles" or "marked" ("prototypes" is refused for now)',
    "acceptance.accepted_by": "who said yes",
    "acceptance.accepted_at": "when, ISO 8601 UTC",
    "acceptance.sha256": "the broker's sha256 of file.docx",
    "acceptance.fixed_strings": "the form's fixed text, a list of strings",
    "acceptance.replaces": "the slot's accepted file this replaces {file_id, version, sha256}, or null",
}

_ACCEPTANCE_KEYS = frozenset(
    k.split(".", 1)[1] for k in SUBMISSION_FIELDS if k.startswith("acceptance.")
)


@dataclass(frozen=True)
class Acceptance:
    kind: str
    attorney: str | None
    file_id: str
    version: str
    contract: str
    accepted_by: str
    accepted_at: str
    data: bytes = field(repr=False)
    fixed_strings: tuple[str, ...] = ()
    replaces: L.Replaced | None = None


@dataclass(frozen=True)
class AcceptResult:
    status: str
    reasons: tuple[str, ...] = ()
    sha256: str = ""
    ledger_digest: str = ""
    unchanged: bool = False
    fixed_used: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "scope": SCOPE,
            "status": self.status,
            "reasons": list(self.reasons),
            "sha256": self.sha256,
            "ledger_digest": self.ledger_digest,
            "unchanged": self.unchanged,
            "fixed_used": list(self.fixed_used),
        }


# ---- the spool shape -----------------------------------------------------------------


def _replaced(raw: Any) -> tuple[L.Replaced | None, list[str]]:
    if raw is None:
        return None, []
    if not isinstance(raw, dict) or set(raw) != {"file_id", "version", "sha256"}:
        return None, ["acceptance.replaces must be null or {file_id, version, sha256}"]
    if not all(isinstance(raw[k], str) and raw[k] for k in raw):
        return None, ["acceptance.replaces has an empty field"]
    return L.Replaced(raw["file_id"], raw["version"], raw["sha256"]), []


def parse_submission(sub: Any, data: bytes) -> tuple[Acceptance | None, list[str]]:
    """The acceptance a submission and its file describe, or every reason not."""
    if not isinstance(sub, dict) or sub.get("scope") != SCOPE or sub.get("phase") != "install":
        return None, [f"not a {SCOPE} install submission"]
    raw = sub.get("acceptance")
    if not isinstance(raw, dict) or set(raw) != _ACCEPTANCE_KEYS:
        return None, [f"acceptance must carry exactly {sorted(_ACCEPTANCE_KEYS)}"]
    problems = [
        f"acceptance.{k} must be a string"
        for k in sorted(_ACCEPTANCE_KEYS - {"attorney", "fixed_strings", "replaces"})
        if not isinstance(raw[k], str)
    ]
    if raw["attorney"] is not None and not isinstance(raw["attorney"], str):
        problems.append("acceptance.attorney must be a string or null")
    fixed = raw["fixed_strings"]
    if not isinstance(fixed, list) or not all(isinstance(s, str) for s in fixed):
        problems.append("acceptance.fixed_strings must be a list of strings")
    replaces, rep_problems = _replaced(raw["replaces"])
    problems += rep_problems
    if not problems and hashlib.sha256(data).hexdigest() != raw["sha256"]:
        problems.append("file.docx does not hash to the submission's sha256")
    if problems:
        return None, problems
    return (
        Acceptance(
            kind=raw["kind"],
            attorney=raw["attorney"],
            file_id=raw["file_id"],
            version=raw["version"],
            contract=raw["contract"],
            accepted_by=raw["accepted_by"],
            accepted_at=raw["accepted_at"],
            data=data,
            fixed_strings=tuple(fixed),
            replaces=replaces,
        ),
        [],
    )


def read_run(run_dir: Path, broker_uid: int | None) -> tuple[Acceptance | None, list[str]]:
    """Read a ``library_accept`` run directory; both files must be the broker's."""
    problems: list[str] = []
    paths = (run_dir / SUBMISSION, run_dir / COPY_NAME)
    for path in paths:
        try:
            uid = path.stat().st_uid
        except OSError as exc:
            problems.append(f"{path.name}: unreadable ({type(exc).__name__})")
            continue
        if broker_uid is not None and uid != broker_uid:
            problems.append(f"{path.name}: not written by the workspace broker")
    if problems:
        return None, problems
    try:
        sub = json.loads(paths[0].read_text("utf-8"))
        data = paths[1].read_bytes()
    except (OSError, ValueError) as exc:
        return None, [f"the run cannot be read ({type(exc).__name__})"]
    return parse_submission(sub, data)


# ---- the checks ------------------------------------------------------------------------


def refusals(acc: Acceptance) -> tuple[list[str], tuple[str, ...]]:
    """Every reason this acceptance is refused before anything is written, and
    the fixed strings the no-client-text check found in use."""
    out: list[str] = []
    out += L.slot_problems(acc.kind, REGISTRY)
    if acc.contract not in L.CONTRACTS:
        out.append(f"contract {acc.contract!r} is not one of {list(L.CONTRACTS)}")
    elif acc.contract == L.PROTOTYPES:
        out.append(PROTOTYPES_REFUSED)
    if len(acc.fixed_strings) > MAX_FIXED_STRINGS or any(
        len(s) > MAX_FIXED_CHARS for s in acc.fixed_strings
    ):
        out.append(
            f"at most {MAX_FIXED_STRINGS} fixed strings of at most {MAX_FIXED_CHARS} characters"
        )
    if not blank_form.is_word_package(acc.data):
        return [*out, "the file is not a Word document package (.docx)"], ()
    checked = blank_form.check(acc.data, acc.fixed_strings)
    out += [f"no-client-text check: {p}" for p in checked.problems]
    return out, checked.fixed_used


def _slot_problems(ledger: L.Ledger, acc: Acceptance) -> list[str]:
    current = ledger.entry_for(acc.kind, acc.attorney)
    now = L.Replaced(current.file_id, current.version, current.sha256) if current else None
    if now == acc.replaces:
        return []
    return [
        "the slot's accepted file is not the one this acceptance says it replaces; nothing written"
    ]


# ---- the write ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Vault:
    """Where and how the writer reaches the vault and the seat's manifest."""

    s3_client: Any
    bucket: str
    slug: str
    spec_dir: Path
    sleep_fn: Callable[[float], None] = time.sleep
    now_fn: Callable[[], float] = time.time
    converge_timeout: float = 90.0
    converge_interval: float = 3.0


def _put(vault: Vault, key: str, body: bytes, content_type: str) -> None:
    """PUT then read back; raises SpecApplyError when the read-back differs."""
    vault.s3_client.put_object(Bucket=vault.bucket, Key=key, Body=body, ContentType=content_type)
    if lib.get_object(vault.s3_client, vault.bucket, key) != body:
        raise SpecApplyError(f"the read-back of {key} does not match what was written")


def _converged(vault: Vault, digest: str) -> bool:
    deadline = vault.now_fn() + vault.converge_timeout
    while True:
        try:
            manifest = lib.read_manifest(Path(vault.spec_dir))
        except SpecApplyError:
            manifest = None
        if manifest is not None and manifest.get(lib.SOURCE_KEY) == digest:
            return True
        if vault.now_fn() >= deadline:
            return False
        vault.sleep_fn(vault.converge_interval)


def _current_ledger(vault: Vault) -> tuple[bytes | None, L.Ledger | None, str]:
    try:
        current = lib.get_object(vault.s3_client, vault.bucket, lib.ledger_key(vault.slug))
    except SpecApplyError as exc:
        return None, None, f"the vault's ledger could not be read: {exc}"
    if current is None:
        return None, L.Ledger(), ""
    try:
        return current, L.parse_ledger(current), ""
    except L.LedgerInvalid as exc:
        return current, None, f"the vault's ledger is invalid, so nothing was written: {exc}"


def _entry(acc: Acceptance, digest: str) -> L.Entry:
    return L.Entry(
        kind=acc.kind,
        attorney=acc.attorney,
        file_id=acc.file_id,
        version=acc.version,
        sha256=digest,
        contract=acc.contract,
        accepted_by=acc.accepted_by,
        accepted_at=acc.accepted_at,
    )


def _write(
    vault: Vault, acc: Acceptance, digest: str, current: bytes | None, new_raw: bytes
) -> str:
    """The copy, the previous generation, the ledger, in that order; '' or the fault."""
    try:
        _put(vault, lib.copy_key(vault.slug, digest), acc.data, "application/octet-stream")
        if current is not None:
            _put(vault, lib.previous_ledger_key(vault.slug), current, "application/json")
        _put(vault, lib.ledger_key(vault.slug), new_raw, "application/json")
    except Exception as exc:  # noqa: BLE001 (any vault fault answers, never raises)
        return f"the vault write failed: {exc}"
    return ""


def accept_file(acc: Acceptance, vault: Vault) -> AcceptResult:
    """Check ``acc``, write the copy and the new ledger to the vault, wait for the seat."""
    problems, fixed_used = refusals(acc)
    digest = hashlib.sha256(acc.data).hexdigest()
    if problems:
        return AcceptResult(STATUS_REJECTED, tuple(problems), digest)
    current, ledger, fault = _current_ledger(vault)
    if ledger is None:
        return AcceptResult(STATUS_ERROR, (fault,), digest)
    slot = _slot_problems(ledger, acc)
    if slot:
        return AcceptResult(STATUS_REJECTED, tuple(slot), digest)
    try:
        new = L.accept(ledger, _entry(acc, digest))
    except L.AcceptanceInvalid as exc:
        return AcceptResult(STATUS_REJECTED, (str(exc),), digest)
    if new == ledger and current is not None:
        done = _converged(vault, hashlib.sha256(current).hexdigest())
        status = STATUS_INSTALLED if done else STATUS_ACCEPTED_PENDING
        return AcceptResult(
            status, ("this file was already accepted; nothing changed",), digest, unchanged=True
        )
    new_raw = L.dump_ledger(new)
    fault = _write(vault, acc, digest, current, new_raw)
    if fault:
        return AcceptResult(STATUS_ERROR, (fault,), digest)
    ledger_digest = hashlib.sha256(new_raw).hexdigest()
    done = _converged(vault, ledger_digest)
    reasons: Sequence[str] = (
        ()
        if done
        else ("the seat has not installed it yet; it applies on the applier's next poll",)
    )
    return AcceptResult(
        STATUS_INSTALLED if done else STATUS_ACCEPTED_PENDING,
        tuple(reasons),
        digest,
        ledger_digest,
        fixed_used=fixed_used,
    )


__all__ = [
    "COPY_NAME",
    "DOCUMENT_CLASSES",
    "SCOPE",
    "SUBMISSION",
    "SUBMISSION_FIELDS",
    "Acceptance",
    "AcceptResult",
    "Vault",
    "accept_file",
    "parse_submission",
    "read_run",
    "refusals",
]
