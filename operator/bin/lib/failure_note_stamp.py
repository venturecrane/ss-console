"""The authored failure note as a wake's counterpart when the wake row carries
no ``body_sha256`` stamp of its own (2026-10-12).

THE INCIDENT. pilot-smokeball, 2026-10-03..10-05: ``deadline-miss-escalator``'s
pre_run crashed three mornings running and took its designed fail-open path
(``blind_wake.py``): an ``EMITTED_WAKE`` row with ``blind_wake: true``,
``dispatch_variant: failure_note``, ``render_mode: templated``, and an envelope
carrying exactly the skill's authored ``render.FAILURE_NOTE``, which the overlay
dispatched out of turn. Every dispatch's ``rendered_body_sha256`` equals the
canonical hash of that constant. But ``write_failure_note_envelope`` returned
wake metadata WITHOUT the ``body_sha256`` list its decision-path twin returns,
so the wake carried no stamp, ``claim_wake`` found no hash-bearing wake, and
the body-verify phase printed ``no_wake_hash`` -- a HOLD that reddened every
scheduled run from 10-03 on and could never clear: audit rows are immutable,
the scheduled scan has no window, and no deploy can stamp a row already written.

The skill is fixed at the source (both skills' failure-note envelopes now
return the stamp). This module covers the rows written before that fix reached
a seat, and any seat still on a stale pre_run: a failure-note wake with no
stamp is given the hash of the note the skill AUTHORED, read from the
template file the send-render contract names. That is real verification, not
an exemption -- the dispatch is compared against authored text. The derived
hash is marked (``WakeStamp.derived_from``) so a mismatch HOLDS rather than
accuses: the note's wording may have been revised since the row was written,
and a revised constant is not evidence that the send diverged.

Parsed, never imported: the template is a skill module with sibling-loading
side effects, so the constant is read with ``ast`` and only a plain string
literal (or an implicit concatenation of them) is accepted. Anything else
yields no hash, and the row keeps its hold.
"""

from __future__ import annotations

import ast
import os
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .send_verify import RenderDecl, WakeStamp

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

#: The ``dispatch_variant`` a failure-note wake carries (both skills'
#: ``write_failure_note_envelope``).
FAILURE_NOTE_VARIANT = "failure_note"
DERIVED_FROM_FAILURE_NOTE = "authored_failure_note"


def authored_failure_note(template: Optional[str], repo_root: str = _REPO_ROOT) -> Optional[str]:
    """The ``FAILURE_NOTE`` string literal in ``template``, or None."""
    if not template:
        return None
    try:
        with open(os.path.join(repo_root, template), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
    except (OSError, SyntaxError, ValueError):
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == "FAILURE_NOTE":
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
            return None
    return None


def derive_failure_note_hashes(
    wakes: list["WakeStamp"],
    rows: list[dict],
    declares: dict[str, "RenderDecl"],
    *,
    repo_root: str = _REPO_ROOT,
) -> int:
    """Give each unstamped failure-note wake the authored note's hash, in place.

    Only a wake that (a) carries no ``body_sha256`` of its own, (b) says on its
    row that it dispatched the failure note, and (c) belongs to a hash-verified
    skill whose contract template authors a ``FAILURE_NOTE`` literal. Returns
    how many wakes were stamped this way.
    """
    from .send_verify import _metadata, canonical_body_sha256

    variant_by_id = {
        row.get("id"): _metadata(row).get("dispatch_variant")
        for row in rows
        if row.get("action_type") == "EMITTED_WAKE" and row.get("id") is not None
    }
    notes: dict[str, Optional[str]] = {}
    derived = 0
    for wake in wakes:
        if wake.hashes_full or variant_by_id.get(wake.row_id) != FAILURE_NOTE_VARIANT:
            continue
        decl = declares.get(wake.skill_name)
        if decl is None or not decl.hash_verified:
            continue
        if wake.skill_name not in notes:
            notes[wake.skill_name] = authored_failure_note(decl.template, repo_root)
        note = notes[wake.skill_name]
        if not note:
            continue
        digest = canonical_body_sha256(note)
        # The note IS its own skeleton (dispatch_envelope: "skeleton IS the note").
        wake.hashes_full = [digest]
        wake.hashes_skeleton = [digest]
        wake.derived_from = DERIVED_FROM_FAILURE_NOTE
        derived += 1
    return derived


__all__ = [
    "DERIVED_FROM_FAILURE_NOTE",
    "FAILURE_NOTE_VARIANT",
    "authored_failure_note",
    "derive_failure_note_hashes",
]
