"""The broker's half of the establishment spool.

Split out of ``establishment.py`` (2026-08-24). This is the stateful half: it
owns the staging/runs/results directories, the audit-ledger writes, and the
propose / read-back / confirm lifecycle for rules, acts and operations requests.

It composes :class:`~workspace_broker.pending_rule_store.PendingRuleStore` for
the proposals table.

Split again 2026-09-11 (code review 2026-09-10, Architecture 3): the four
proposal lifecycles each live in their own module as a collaborator that
acts on this store's state (:mod:`.establishment_lifecycle` names them).
What stays here is the state, the spool (sweep, staging, submit, status),
and one delegation per lifecycle verb, so the broker's dispatch and the
seat's plugin keep addressing one object.
"""

from __future__ import annotations

import json
import logging
import secrets
import shutil
import sqlite3
import time
from pathlib import Path
from hashlib import sha256
from typing import Any

from .audit_ledger import _iso_utc
from .establishment_constants import (
    ESTABLISHMENT_SUBMITTED_ACTION_TYPE,
    MAX_DOCS_PER_SET,
    MAX_DOC_TEXT_BYTES,
    MAX_SET_BYTES,
    MAX_SPEC_BODY_BYTES,
    RESULT_TTL_SECONDS,
    STAGING_TTL_SECONDS,
    STATUS_INSTALLED,
    SUBMIT_PHASES,
    _MAX_ASSERTIONS,
    _MAX_ASSERTIONS_BYTES,
    _MAX_SHORT_TEXT,
)
from .establishment_acts import ActProposals
from .establishment_lifecycle import refuse_restated
from .establishment_notify import OutcomeNotifications
from .establishment_ops import OpsRequests
from .establishment_rules import RuleProposals
from .establishment_validation import (
    EstablishmentValidationError,
    _hash_text,
    _optional_text,
    _require_class_slug,
    _require_id,
    _require_property,
    _require_text,
    build_result_row,
    normalize_lf,
    require_address,
    safe_slug,
)
from .pending_rule_store import PendingRuleStore

logger = logging.getLogger(__name__)


class EstablishmentStore:
    """The broker's half of the establishment spool.

    Layout (created and moded by the entrypoint, never here — the spool root is
    root-owned and the broker uid cannot create it):

        <root>/staging/<staging_id>/meta.json      broker-written
        <root>/staging/<staging_id>/docs/<id>.json broker-written (holds text)
        <root>/staging/<staging_id>/analysis/      ROOT-written (intake), 0700
        <root>/runs/<run_id>/submission.json       broker-written
        <root>/runs/<run_id>/docs/<id>.json        broker-moved from staging
        <root>/results/<run_id>.json               ROOT-written 0640, one-shot

    Runs are assembled complete (INCLUDING submission.json) in a dot-prefixed
    temp dir and atomically renamed into place; the intake skips dot-prefixed
    entries and run dirs without a submission.json, so it never observes a
    half-written submission (overlay establish_intake/intake.py, the other
    half of this contract).

    Lifecycle split with the root intake: the intake purges each RUN dir after
    writing its result, purges the whole staging set after an install run, and
    backstop-sweeps staging at its own longer TTL — because the broker cannot
    remove the root-owned ``analysis/`` subdir the analyze phase leaves in a
    staging set. The broker's sweep therefore only removes staging sets it
    fully owns, and enforces expiry on the rest by refusal.
    """

    def __init__(
        self,
        spool_root: str | Path,
        ledger: Any,
        pending_db_path: str | Path | None = None,
        customer_path: str | Path | None = None,
    ) -> None:
        self.root = Path(spool_root)
        self.staging_dir = self.root / "staging"
        self.runs_dir = self.root / "runs"
        self.results_dir = self.root / "results"
        self._ledger = ledger
        # ss-console#2529. Absent on a broker with no audit DB configured, in
        # which case the propose/confirm verbs refuse by name rather than
        # half-working: a rule the firm cannot be shown back is a rule it cannot
        # confirm, and committing one without the readback is the thing this
        # whole path exists to avoid.
        self.pending = PendingRuleStore(pending_db_path) if pending_db_path else None
        # ss-console#2536. The seat's own authored config, read by THIS uid and
        # never taken off the wire: an act may only be proposed with the values
        # the firm authored, so the broker has to be able to read them itself.
        # Absent (a broker built with no config handle) means no act can be
        # proposed, which is the fail-closed direction.
        self.customer_path = Path(customer_path) if customer_path else None
        self._rules = RuleProposals(self)
        self._notify = OutcomeNotifications(self)
        self._ops = OpsRequests(self)
        self._acts = ActProposals(self)

    # ------------------------------------------------------------------
    # TTL sweep
    # ------------------------------------------------------------------

    def sweep(self, now: float | None = None) -> None:
        """Remove expired staging sets and unread results.

        Best-effort by design: a sweep failure must not refuse the verb that
        triggered it. Run dirs are NOT swept here — their lifecycle belongs to
        the root intake, which purges each run after writing its result.
        """
        now = time.time() if now is None else now
        if self.staging_dir.is_dir():
            for entry in self.staging_dir.iterdir():
                if not entry.is_dir():
                    continue
                if (entry / "analysis").is_dir():
                    # Root-owned analyze artifacts the broker cannot remove.
                    # Don't try — a partial rmtree leaves a zombie set. The
                    # intake's backstop sweep purges these as root; expiry is
                    # still ENFORCED broker-side by _require_staging's age
                    # check, so the lingering dir grants nothing.
                    continue
                created = self._staging_created_at(entry)
                if now - created > STAGING_TTL_SECONDS:
                    shutil.rmtree(entry, ignore_errors=True)
        if self.results_dir.is_dir():
            for entry in self.results_dir.iterdir():
                if not entry.is_file():
                    continue
                try:
                    if now - entry.stat().st_mtime > RESULT_TTL_SECONDS:
                        entry.unlink(missing_ok=True)
                except OSError:
                    continue
        if self.pending is not None:
            try:
                self.pending.sweep(now)
            except sqlite3.Error:
                # Best-effort, same as the rest of this sweep: a table that
                # cannot be swept must not refuse the verb that triggered it.
                # Expiry is still ENFORCED at read and at submit, so a lingering
                # row grants nothing.
                pass

    def _staging_created_at(self, staging_path: Path) -> float:
        meta_path = staging_path / "meta.json"
        try:
            meta = json.loads(meta_path.read_text("utf-8"))
            created = meta.get("created_at")
            if isinstance(created, (int, float)):
                return float(created)
        except (OSError, ValueError):
            pass
        try:
            return staging_path.stat().st_mtime
        except OSError:
            return 0.0

    # ------------------------------------------------------------------
    # establish_stage_document
    # ------------------------------------------------------------------

    def stage_document(self, request: dict[str, Any]) -> dict[str, Any]:
        """Validate one corpus document and write it into a staging set.

        The stored file is rebuilt from the bounded field set below; the
        sha256 is computed here from the bytes being stored (a wire-supplied
        hash is never read).
        """
        name = safe_slug(request.get("name"))

        text = request.get("text")
        if not isinstance(text, str):
            raise EstablishmentValidationError("text must be a string")
        if not text.strip():
            raise EstablishmentValidationError("text must not be empty")
        text_bytes = text.encode("utf-8")
        if len(text_bytes) > MAX_DOC_TEXT_BYTES:
            raise EstablishmentValidationError(f"text is {len(text_bytes)} bytes; the ceiling is {MAX_DOC_TEXT_BYTES}")

        source_raw = request.get("source")
        if not isinstance(source_raw, dict):
            raise EstablishmentValidationError("source must be an object with connector and document_id")
        source = {
            "connector": _require_text(source_raw.get("connector"), "source.connector", _MAX_SHORT_TEXT),
            "document_id": _require_text(source_raw.get("document_id"), "source.document_id", _MAX_SHORT_TEXT),
            "matter_id": _optional_text(source_raw.get("matter_id"), "source.matter_id", _MAX_SHORT_TEXT),
        }

        staging_id_raw = request.get("staging_id")
        if staging_id_raw is None:
            # Lowercase hex so the id always matches the intake's _SAFE_SEGMENT.
            staging_id = secrets.token_hex(12)
            staging_path = self.staging_dir / staging_id
            (staging_path / "docs").mkdir(parents=True)
            (staging_path / "meta.json").write_text(json.dumps({"created_at": time.time()}), "utf-8")
        else:
            staging_id, staging_path = self._require_staging(staging_id_raw)

        existing = self._load_staged_docs(staging_path)
        if len(existing) + 1 > MAX_DOCS_PER_SET:
            raise EstablishmentValidationError(
                f"staging set already holds {len(existing)} documents; the ceiling is {MAX_DOCS_PER_SET}"
            )
        set_bytes = sum(int(doc.get("size_bytes", 0)) for doc in existing)
        if set_bytes + len(text_bytes) > MAX_SET_BYTES:
            raise EstablishmentValidationError(
                f"staging set would grow to {set_bytes + len(text_bytes)} bytes; the ceiling is {MAX_SET_BYTES}"
            )

        doc_id = f"doc-{len(existing) + 1:03d}"
        while (staging_path / "docs" / f"{doc_id}.json").exists():
            doc_id = f"doc-{secrets.token_hex(4)}"
        digest = _hash_text(text)
        record = {
            "doc_id": doc_id,
            "name": name,
            "sha256": digest,
            "size_bytes": len(text_bytes),
            "source": source,
            "staged_at": time.time(),
            "text": text,
        }
        (staging_path / "docs" / f"{doc_id}.json").write_text(json.dumps(record, sort_keys=True), "utf-8")
        return {
            "ok": True,
            "staging_id": staging_id,
            "doc_id": doc_id,
            "name": name,
            "sha256": digest,
            "doc_count": len(existing) + 1,
            "set_bytes": set_bytes + len(text_bytes),
        }

    def _require_staging(self, value: Any) -> tuple[str, Path]:
        staging_id = _require_id(value, "staging_id")
        staging_path = self.staging_dir / staging_id
        if not staging_path.is_dir():
            raise EstablishmentValidationError("unknown or expired staging_id; stage the documents again")
        # Expiry is enforced here by refusal, not only by the sweep: a set the
        # broker cannot remove (root-owned analysis/ inside) lingers until the
        # intake's backstop purge, and lingering must not extend its life.
        if time.time() - self._staging_created_at(staging_path) > STAGING_TTL_SECONDS:
            raise EstablishmentValidationError(
                f"staging set expired ({STAGING_TTL_SECONDS // 60}-minute TTL); stage the documents again"
            )
        return staging_id, staging_path

    def _load_staged_docs(self, staging_path: Path) -> list[dict[str, Any]]:
        docs_dir = staging_path / "docs"
        docs: list[dict[str, Any]] = []
        if not docs_dir.is_dir():
            return docs
        for entry in sorted(docs_dir.glob("*.json")):
            try:
                record = json.loads(entry.read_text("utf-8"))
            except (OSError, ValueError) as exc:
                raise EstablishmentValidationError(
                    f"staged document {entry.name} is unreadable; stage the documents again"
                ) from exc
            record["_path"] = entry
            docs.append(record)
        return docs

    # ------------------------------------------------------------------
    # establish_propose  (ss-console#2529)
    # ------------------------------------------------------------------

    def _require_pending(self) -> PendingRuleStore:
        if self.pending is None:
            raise EstablishmentValidationError("this broker has no rule store configured; nothing was recorded")
        return self.pending

    # ------------------------------------------------------------------
    # The proposal lifecycles. One collaborator each (2026-09-11 split, code
    # review 2026-09-10, Architecture 3); the verbs keep their names here so
    # the broker's dispatch and the seat's plugin see one object.
    # ------------------------------------------------------------------

    def propose(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._rules.propose(request)

    def decline(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._rules.decline(request)

    def lapse_notified(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._rules.lapse_notified(request)

    def pending_rules(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._rules.pending_rules(request)

    def notify_claim(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._notify.notify_claim(request)

    def notify_release(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._notify.notify_release(request)

    def ops_propose(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._ops.ops_propose(request)

    def ops_resolve(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._ops.ops_resolve(request)

    def ops_ask_sent(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._ops.ops_ask_sent(request)

    def act_propose(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._acts.act_propose(request)

    def act_commit(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._acts.act_commit(request)

    def _claim_proposal(self, request: dict[str, Any], scope: str) -> dict[str, Any]:
        """The submit path's claim of a pending row; the same claim every lifecycle makes."""
        return self._rules.claim_proposal(request, scope)

    # ------------------------------------------------------------------
    # establish_submit
    # ------------------------------------------------------------------

    def submit(self, request: dict[str, Any]) -> dict[str, Any]:
        """Validate a submission, append its audit row, and materialize the run.

        The audit row is appended BEFORE the run dir is renamed into place: a
        run the root intake can see without a ledger row would be an unaudited
        install path, which is the worse failure than a row for a run that
        never materialized.

        Three scopes (ADR 0085 §2/§4/§6). ``firm`` (the default) is the staged-
        corpus path below — Operator admins only, gated seat-side. ``person``
        records the SPEAKER's own preferences: no staging, no corpus, no
        compiler gates; the seat-side predicate pins the subject to the
        attributed sender, and the intake re-validates shape + roster.
        ``firm_adjust`` commits one confirmed sentence against an output class,
        and REQUIRES a proposal id — there is no route by which a firm-wide rule
        installs without a person having been shown it and having said yes.
        """
        scope = request.get("scope") or "firm"
        if scope not in ("firm", "person", "firm_adjust"):
            raise EstablishmentValidationError(f"scope must be 'firm', 'person', or 'firm_adjust'; got {scope!r}")
        if scope == "firm_adjust":
            return self._submit_firm_adjust(request, secrets.token_hex(16))
        if scope == "person":
            return self._submit_person(request, secrets.token_hex(16))
        staging_id, staging_path = self._require_staging(request.get("staging_id"))
        phase = _require_text(request.get("phase"), "phase", _MAX_SHORT_TEXT)
        if phase not in SUBMIT_PHASES:
            raise EstablishmentValidationError(f"phase must be one of {sorted(SUBMIT_PHASES)}; got {phase!r}")

        staged = self._load_staged_docs(staging_path)
        if not staged:
            raise EstablishmentValidationError("staging set holds no documents; stage the corpus first")
        # Integrity re-check of the broker's own files (defense in depth — the
        # intake re-verifies too): every staged text must still hash to the
        # digest recorded when it was staged.
        for doc in staged:
            if _hash_text(doc.get("text", "")) != doc.get("sha256"):
                raise EstablishmentValidationError(
                    f"staged document {doc.get('doc_id')} failed its integrity re-hash; stage the documents again"
                )

        # Lowercase hex so the id always matches the intake's _SAFE_SEGMENT.
        run_id = secrets.token_hex(16)
        if phase == "analyze":
            return self._submit_analyze(staging_id, staging_path, staged, run_id)
        return self._submit_install(request, staging_id, staging_path, staged, run_id)

    def _submit_analyze(
        self,
        staging_id: str,
        staging_path: Path,
        staged: list[dict[str, Any]],
        run_id: str,
    ) -> dict[str, Any]:
        doc_summaries = [{"name": d["name"], "sha256": d["sha256"]} for d in staged]
        # The intake's submission contract (its module docstring): run_id,
        # staging_id, phase, created_at. The doc files in docs/ carry the rest.
        submission = {
            "phase": "analyze",
            "scope": "firm",
            "run_id": run_id,
            "staging_id": staging_id,
            "created_at": time.time(),
        }
        row = {
            "action_type": ESTABLISHMENT_SUBMITTED_ACTION_TYPE,
            "actor": "operator",
            "actor_role": "agent",
            "metadata": json.dumps(
                {
                    "phase": "analyze",
                    "run_id": run_id,
                    "staging_id": staging_id,
                    "docs": doc_summaries,
                    "doc_count": len(staged),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
        self._ledger.append(row)
        # Analyze COPIES the corpus into the run: the staging set must survive
        # so the later install submission can hash-bind against it.
        self._materialize_run(run_id, submission, staged, move=False)
        return {"ok": True, "run_id": run_id, "phase": "analyze", "status": "queued"}

    def _submit_install(
        self,
        request: dict[str, Any],
        staging_id: str,
        staging_path: Path,
        staged: list[dict[str, Any]],
        run_id: str,
    ) -> dict[str, Any]:
        output_class = _require_class_slug(request.get("output_class"))
        prop = _require_property(request.get("property"))

        body_raw = request.get("spec_body")
        if not isinstance(body_raw, str):
            raise EstablishmentValidationError("spec_body must be a string")
        # LF-normalize BEFORE the ceiling and the hash (portal precedent): the
        # byte count, the digest, and the installed file must agree, on LF.
        body = normalize_lf(body_raw).strip()
        if not body:
            raise EstablishmentValidationError("spec_body must not be empty")
        body_bytes = body.encode("utf-8")
        if len(body_bytes) > MAX_SPEC_BODY_BYTES:
            raise EstablishmentValidationError(
                f"spec_body is {len(body_bytes)} bytes after LF normalization; the ceiling is {MAX_SPEC_BODY_BYTES}"
            )
        spec_digest = sha256(body_bytes).hexdigest()

        assertions = self._validate_assertions(request.get("assertions"))

        manifest_raw = request.get("corpus_manifest")
        if not isinstance(manifest_raw, list) or not manifest_raw:
            raise EstablishmentValidationError("corpus_manifest must be a non-empty list of {doc_id, sha256}")
        if len(manifest_raw) > MAX_DOCS_PER_SET:
            raise EstablishmentValidationError(
                f"corpus_manifest holds {len(manifest_raw)} entries; the ceiling is {MAX_DOCS_PER_SET}"
            )
        staged_by_id = {d["doc_id"]: d for d in staged}
        seen: set[str] = set()
        selected: list[dict[str, Any]] = []
        for index, entry in enumerate(manifest_raw):
            if not isinstance(entry, dict):
                raise EstablishmentValidationError(f"corpus_manifest[{index}] must be an object with doc_id and sha256")
            doc_id = _require_text(entry.get("doc_id"), f"corpus_manifest[{index}].doc_id", 64)
            claimed = _require_text(entry.get("sha256"), f"corpus_manifest[{index}].sha256", 64)
            if doc_id in seen:
                raise EstablishmentValidationError(
                    f"corpus_manifest names {doc_id} twice; refusing an ambiguous corpus"
                )
            seen.add(doc_id)
            doc = staged_by_id.get(doc_id)
            if doc is None:
                raise EstablishmentValidationError(f"corpus_manifest names {doc_id}, which is not in this staging set")
            # The claim must match the broker's OWN hash of the staged bytes —
            # the spec is bound to exactly the corpus the agent staged, and a
            # manifest that disagrees is a refusal, never a repair.
            if claimed != doc["sha256"]:
                raise EstablishmentValidationError(
                    f"corpus_manifest hash for {doc_id} does not match the staged document"
                )
            selected.append(doc)

        instructed_by = _require_text(request.get("instructed_by"), "instructed_by", _MAX_SHORT_TEXT)
        source_ref = _require_text(request.get("source_ref"), "source_ref", _MAX_SHORT_TEXT)

        doc_summaries = [{"name": d["name"], "sha256": d["sha256"]} for d in selected]
        # The intake's submission contract (its module docstring). The manifest
        # is REBUILT from the broker-verified selection — the intake re-checks
        # that it maps 1:1 onto the run's docs with matching hashes.
        submission = {
            "phase": "install",
            "scope": "firm",
            "run_id": run_id,
            "staging_id": staging_id,
            "output_class": output_class,
            "property": prop,
            "spec_body": body,
            "spec_sha256": spec_digest,
            "assertions": assertions,
            "corpus_manifest": [{"doc_id": d["doc_id"], "sha256": d["sha256"]} for d in selected],
            # Provenance for the audit trail, never authorization — the broker
            # cannot verify a claimed instructor (same posture as corrections
            # ``stated_by``); the authorization gate is the admin hook seat-side.
            "instructed_by": instructed_by,
            "source_ref": source_ref,
            "created_at": time.time(),
        }
        row = {
            "action_type": ESTABLISHMENT_SUBMITTED_ACTION_TYPE,
            "actor": "operator",
            "actor_role": "agent",
            "metadata": json.dumps(
                {
                    "phase": "install",
                    "run_id": run_id,
                    "staging_id": staging_id,
                    "output_class": output_class,
                    "property": prop,
                    "spec_sha256": spec_digest,
                    "docs": doc_summaries,
                    "doc_count": len(selected),
                    "assertion_count": len((assertions or {}).get("rules") or []),
                    "instructed_by": instructed_by,
                    "source_ref": source_ref,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
        self._ledger.append(row)
        # Install MOVES the manifest docs into the run. The staging set itself
        # is deliberately NOT removed here: the intake's leak check reads the
        # root-owned analysis/approved_strings.json out of it DURING the run,
        # and the intake purges the whole set (analysis included, as root)
        # after the run completes — pass or fail.
        self._materialize_run(run_id, submission, selected, move=True)
        return {"ok": True, "run_id": run_id, "phase": "install", "status": "queued"}

    def _submit_firm_adjust(self, request: dict[str, Any], run_id: str) -> dict[str, Any]:
        """Commit one confirmed sentence as a standing adjustment.

        A PROPOSAL ID IS NOT OPTIONAL HERE. The whole control on this path is
        that a person was shown the rule and answered; a submit that carried its
        own text would be the agent writing a firm-wide rule off its own reading
        of an email, which is the witness-never-author line ADR 0085 §4 moved
        only as far as "an admin's confirmed sentence".

        WHO IS RECORDED AS WHAT. ``instructed_by`` on the ROW is whoever stated
        the rule — often a paralegal whose firm-level remark waits for an admin.
        ``applied_by`` is the sender confirming this submit. Both ride onto the
        installed adjustment and both render in the spec file, so the firm can
        read who asked for a rule and who put it in force.
        """
        row = self._claim_proposal(request, "firm_adjust")
        subject = row["subject"]
        output_class = _require_class_slug(subject.get("output_class"))
        prop = _require_property(subject.get("property"))
        refuse_restated(
            request,
            row,
            {
                "output_class": output_class,
                "property": prop,
                "text": row["text"],
                "spec_body": row["text"],
            },
        )
        applied_by = require_address(request.get("instructed_by"), "instructed_by")
        source_ref = _require_text(request.get("source_ref"), "source_ref", _MAX_SHORT_TEXT)

        if not self._require_pending().consume(row["proposal_id"], run_id):
            # Lost the race to a concurrent confirmation. Refuse rather than
            # install twice: the firm's sentence rendering twice in its own spec
            # file is a worse outcome than one redundant refusal.
            raise EstablishmentValidationError(f"rule {row['proposal_id']} was already committed; it is in effect")

        adjustment = {
            "id": row["proposal_id"],
            "text": row["text"],
            "sha256": row["text_sha256"],
            "instructed_by": row["instructed_by"],
            "applied_by": applied_by,
            "at": _iso_utc(),
        }
        submission = {
            "phase": "install",
            "scope": "firm_adjust",
            "run_id": run_id,
            "output_class": output_class,
            "property": prop,
            "adjustment": adjustment,
            "instructed_by": applied_by,
            "source_ref": source_ref,
            "created_at": time.time(),
        }
        self._ledger.append(
            {
                "action_type": ESTABLISHMENT_SUBMITTED_ACTION_TYPE,
                "actor": "operator",
                "actor_role": "agent",
                "metadata": json.dumps(
                    {
                        "phase": "install",
                        "scope": "firm_adjust",
                        "run_id": run_id,
                        "proposal_id": row["proposal_id"],
                        "output_class": output_class,
                        "property": prop,
                        # Digest, never the sentence (ADR 0083's retention
                        # posture). It is also what lets a later reader prove
                        # the committed rule is the proposed one, since the
                        # RULE_PROPOSED row carries the same digest.
                        "spec_sha256": row["text_sha256"],
                        "instructed_by": row["instructed_by"],
                        "applied_by": applied_by,
                        "for_admin": row["for_admin"],
                        "source_ref": source_ref,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )
        self._materialize_run(run_id, submission, [], move=False)
        return {
            "ok": True,
            "run_id": run_id,
            "phase": "install",
            "scope": "firm_adjust",
            "proposal_id": row["proposal_id"],
            "status": "queued",
        }

    def _submit_person(self, request: dict[str, Any], run_id: str) -> dict[str, Any]:
        """A person-scoped install: the speaker's own preferences, docs-less.

        Refuses every firm-path field a person submit must not carry —
        "present" means NON-NULL (the overlay sends ``staging_id: null``, key
        present). The subject was already pinned to the attributed sender by
        the seat-side predicate; the broker re-validates SHAPE only, and the
        intake re-validates shape + roster (defense in depth, same split as
        the firm path).

        WITH A ``proposal_id`` (ss-console#2529) the person and the body come
        out of the pending row rather than off the wire — the same readback
        discipline the firm path requires, offered here because a person who is
        asked "shall I remember that?" and says yes has told the Operator
        something an unconfirmed guess at their words has not. Without one the
        original direct path stands unchanged.

        ``append`` adds to the existing preference instead of replacing it.
        """
        for forbidden in ("staging_id", "corpus_manifest", "output_class", "property"):
            if request.get(forbidden) is not None:
                raise EstablishmentValidationError(f"{forbidden} must not be supplied on a person-scoped submit")
        append_raw = request.get("append", False)
        if not isinstance(append_raw, bool):
            raise EstablishmentValidationError("append must be a boolean")

        proposal_id: str | None = None
        if request.get("proposal_id") is not None:
            claimed = self._claim_proposal(request, "person")
            person = require_address(claimed["subject"].get("person"), "subject.person")
            refuse_restated(
                request,
                claimed,
                {"person": person, "spec_body": claimed["text"], "text": claimed["text"]},
            )
            confirmer = require_address(request.get("instructed_by"), "instructed_by")
            if confirmer != person:
                raise EstablishmentValidationError(
                    "a personal rule is confirmed by the person it belongs to; "
                    f"{confirmer} cannot confirm a preference for {person}"
                )
            if not self._require_pending().consume(claimed["proposal_id"], run_id):
                raise EstablishmentValidationError(
                    f"rule {claimed['proposal_id']} was already committed; it is in effect"
                )
            proposal_id = claimed["proposal_id"]
            body = claimed["text"]
            spec_digest = claimed["text_sha256"]
            instructed_by = confirmer
        else:
            person = require_address(request.get("person"), "person")
            body_raw = request.get("spec_body")
            if not isinstance(body_raw, str):
                raise EstablishmentValidationError("spec_body must be a string")
            body = normalize_lf(body_raw).strip()
            if not body:
                raise EstablishmentValidationError("spec_body must not be empty")
            body_bytes = body.encode("utf-8")
            if len(body_bytes) > MAX_SPEC_BODY_BYTES:
                raise EstablishmentValidationError(
                    f"spec_body is {len(body_bytes)} bytes after LF normalization; the ceiling is {MAX_SPEC_BODY_BYTES}"
                )
            spec_digest = sha256(body_bytes).hexdigest()
            instructed_by = _require_text(request.get("instructed_by"), "instructed_by", _MAX_SHORT_TEXT)

        assertions = self._validate_assertions(request.get("assertions"))
        source_ref = _require_text(request.get("source_ref"), "source_ref", _MAX_SHORT_TEXT)

        submission = {
            "phase": "install",
            "scope": "person",
            "run_id": run_id,
            "person": person,
            "spec_body": body,
            "spec_sha256": spec_digest,
            "assertions": assertions,
            "append": append_raw,
            # Provenance for the audit trail, never authorization (firm-path
            # posture; the authorization gate is the seat-side predicate).
            "instructed_by": instructed_by,
            "source_ref": source_ref,
            "created_at": time.time(),
        }
        metadata: dict[str, Any] = {
            "phase": "install",
            "scope": "person",
            "run_id": run_id,
            "person": person,
            "spec_sha256": spec_digest,
            "assertion_count": len((assertions or {}).get("rules") or []),
            "append": append_raw,
            "instructed_by": instructed_by,
            "source_ref": source_ref,
        }
        if proposal_id is not None:
            metadata["proposal_id"] = proposal_id
        row = {
            "action_type": ESTABLISHMENT_SUBMITTED_ACTION_TYPE,
            "actor": "operator",
            "actor_role": "agent",
            "metadata": json.dumps(metadata, sort_keys=True, separators=(",", ":")),
        }
        self._ledger.append(row)
        self._materialize_run(run_id, submission, [], move=False)
        result = {"ok": True, "run_id": run_id, "phase": "install", "status": "queued"}
        if proposal_id is not None:
            result["proposal_id"] = proposal_id
        return result

    def _validate_assertions(self, value: Any) -> dict[str, Any] | None:
        """Shape-and-bound check for assertions.

        The wire shape is an OBJECT carrying a ``rules`` list (the intake reads
        ``assertions.get("rules")`` and forwards the rules to the selftest
        compiler). Full rule-schema validation is deliberately NOT here: the
        selftest owns the rule schema and refuses malformed rules (exit 1,
        design §5). The broker guarantees the payload is a bounded JSON object
        whose rules are objects, and nothing else.
        """
        if value is None:
            return None
        if not isinstance(value, dict):
            raise EstablishmentValidationError("assertions must be an object (with an optional 'rules' list)")
        rules = value.get("rules")
        if rules is not None:
            if not isinstance(rules, list):
                raise EstablishmentValidationError("assertions.rules must be a list")
            if len(rules) > _MAX_ASSERTIONS:
                raise EstablishmentValidationError(
                    f"assertions.rules holds {len(rules)} rules; the ceiling is {_MAX_ASSERTIONS}"
                )
            for index, entry in enumerate(rules):
                if not isinstance(entry, dict):
                    raise EstablishmentValidationError(f"assertions.rules[{index}] must be an object")
        serialized = json.dumps(value, sort_keys=True, separators=(",", ":"))
        if len(serialized.encode("utf-8")) > _MAX_ASSERTIONS_BYTES:
            raise EstablishmentValidationError(
                f"assertions serialize to {len(serialized)} bytes; the ceiling is {_MAX_ASSERTIONS_BYTES}"
            )
        return json.loads(serialized)

    def _materialize_run(
        self,
        run_id: str,
        submission: dict[str, Any],
        docs: list[dict[str, Any]],
        move: bool,
    ) -> None:
        """Assemble the run in a dot-prefixed temp dir, then atomically rename.

        The root intake polls the runs dir and ignores dot-prefixed entries, so
        it can never observe a half-written submission (same-filesystem rename
        is atomic).
        """
        tmp_dir = self.runs_dir / f".tmp-{run_id}"
        try:
            (tmp_dir / "docs").mkdir(parents=True)
            for doc in docs:
                source_path: Path = doc["_path"]
                target = tmp_dir / "docs" / source_path.name
                if move:
                    source_path.rename(target)
                else:
                    shutil.copyfile(source_path, target)
            (tmp_dir / "submission.json").write_text(json.dumps(submission, sort_keys=True), "utf-8")
            tmp_dir.rename(self.runs_dir / run_id)
        except OSError:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

    # ------------------------------------------------------------------
    # establish_status
    # ------------------------------------------------------------------

    def _stamp_installed(self, run_id: str, result: dict[str, Any]) -> None:
        """Record ``installed`` durably, because the read that carries it is
        one-shot (ss-console#2546 follow-up).

        The result file is deleted after the first successful read, so the fact
        that a rule went into force lives for exactly one call and then only in
        an audit row nobody queries. That is why the requester was never told:
        every path that wanted to say "your rule is in effect" had to be the
        one call that read the result, and none of them reliably was.

        A column on the proposal instead. It is written from the root-authored
        result and from the broker's own commit record, never from a request
        field, and it is what the seat's outcome view keys on.

        Best-effort: a stamping fault must not cost the caller the result they
        asked for, which is the same rule the ledger append one line up follows.
        """
        if self.pending is None or result.get("status") != STATUS_INSTALLED:
            return
        try:
            proposal_id = self.pending.mark_installed(run_id)
        except sqlite3.Error:
            logger.warning("run %s installed but the proposal could not be stamped", run_id)
            return
        if proposal_id:
            logger.info("rule %s observed installed on run %s", proposal_id, run_id)

    def status(self, request: dict[str, Any]) -> dict[str, Any]:
        """Read a run's result. One-shot: the result file is deleted after the
        first successful read, and its retained trace is the bounded
        ESTABLISHMENT_RESULT audit row (appended before the delete, so a failed
        append leaves the result readable and retryable)."""
        run_id = _require_id(request.get("run_id"), "run_id")
        result_path = self.results_dir / f"{run_id}.json"
        if result_path.is_file():
            try:
                result = json.loads(result_path.read_text("utf-8"))
            except (OSError, ValueError) as exc:
                raise ValueError(f"result for run {run_id} is unreadable; the TTL sweep will clear it") from exc
            if not isinstance(result, dict):
                raise ValueError(f"result for run {run_id} is not an object; the TTL sweep will clear it")
            self._ledger.append(build_result_row(run_id, result))
            self._stamp_installed(run_id, result)
            # One-shot delete. The results dir is 0770 root:workspace-broker
            # (entrypoint-authored; the intake re-hardens to the same, its
            # overlay#221 fix), so this unlink succeeds in production. The
            # guard is resilience only: against a mis-hardened dir the read
            # must still succeed (the agent is owed the result it was
            # promised) and the intake's 30-min TTL sweep becomes the remover.
            try:
                result_path.unlink(missing_ok=True)
            except OSError:
                pass
            return {"ok": True, "run_id": run_id, "status": "complete", "result": result}
        if (self.runs_dir / run_id).is_dir():
            return {"ok": True, "run_id": run_id, "status": "pending"}
        raise EstablishmentValidationError(
            f"unknown run_id; results are one-shot reads and expire after {RESULT_TTL_SECONDS // 60} minutes"
        )
