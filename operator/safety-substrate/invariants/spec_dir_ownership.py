"""Boot invariant — the authored-spec tree is NOT writable by the agent uid.

ss ADR 0083 / ss-console #2084. The structural half of the spec loader: a check
the Machine refuses to boot past.

WHY THIS IS THE PART NOT TO SHIP WITHOUT
----------------------------------------
An authored spec enters the drafting context by being READ. ``read_file`` is a
READ-class tool: enforcement always allows it, it is not fenced, and it does not
taint the session. So a spec file the hermes uid can WRITE is a persistent,
untainted, self-authored instruction channel that survives restarts — strictly
worse than a tainted inbound email, which at least fences the turn it arrives
on. The agent would be able to author the text that governs how it writes, and
nothing downstream could tell that from the customer having authored it.

This repository has already paid for the general form of that mistake once. The
keystone comment in ``operator/templates/entrypoint.sh`` records the
self-loopback hole proven live on hermes-smd-staging 2026-06-15: ``customer.yaml``
sat on the agent-writable ``/opt/data`` volume, and one ``sed`` flipped
``external_send`` from ``draft_for_review`` to ``autonomous``. The fix was not a
policy, a prompt, or a review step. It was root ownership. This module makes the
same fix load-bearing for the spec tree by refusing to serve when it does not
hold.

WHY IT CARRIES NO INVARIANT NUMBER
----------------------------------
The numbered invariants (1-7, ``safety-substrate/README.md``) are the platform's
irreducible behavioural promises, enumerated in the PRD. This is not one of
them; it is a structural precondition of the substrate, in the same family as
the keystone relocation, and inventing an eighth PRD invariant to house it would
misrepresent where it came from. It is named for what it checks.

WHAT IT CHECKS
--------------
Three conditions on ``SMD_SPEC_DIR`` and everything under it:

1. The directory itself is not writable by the agent uid — the dominant risk,
   because directory write permits CREATE, REPLACE, and RENAME even when every
   file inside is read-only. A file the agent cannot edit but can replace
   wholesale is not protected.
2. No file under it is writable by the agent uid.
3. Nothing under it is a symlink pointing outside the tree. A link is a write
   path to its target wearing the tree's name, so a root-owned link into an
   agent-writable directory reopens the hole while every mode bit reads clean.

An ABSENT directory PASSES. That is deliberate and is not a hole: absent means
no spec was installed, so there is nothing the agent could have authored and
nothing any consumer will read — ``shared.spec_manifest`` returns no entries and
every gate that consulted it stays closed. Refusing to boot a seat that has no
authored specs would brick every seat that has not adopted the feature.

FAIL-CLOSED ON A DEGRADED SUBSTRATE. A stat that raises is a violation, not a
skip: "cannot evaluate" must never read as "permitted".
"""

from __future__ import annotations

import logging
import os
import pwd
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

log = logging.getLogger(__name__)

#: Env var naming the installed-spec directory (exported by entrypoint.sh).
SPEC_DIR_ENV = "SMD_SPEC_DIR"

#: The unprivileged principal the gateway runs as after the privilege drop.
AGENT_USER = "hermes"

#: Cap on how many paths are walked. A spec tree is a handful of small markdown
#: files; anything past this is a mistake or an attempt to make the boot check
#: expensive, and either way the tree is not one we should adopt silently.
_MAX_PATHS = 2000


@dataclass(frozen=True)
class OwnershipViolation:
    """One path that fails the check, and why."""

    path: str
    reason: str


@dataclass
class OwnershipResult:
    """Outcome of the check."""

    checked: int = 0
    violations: list[OwnershipViolation] = field(default_factory=list)
    skipped_reason: str = ""

    @property
    def passed(self) -> bool:
        return not self.violations

    def refusal_message(self) -> str:
        lines = [
            "SPEC_DIR_OWNERSHIP_CHECK_FAILED — the authored-spec tree is writable by "
            f"the {AGENT_USER!r} uid, or reaches outside itself.",
            "",
            "An agent-writable spec is a persistent, untainted, self-authored "
            "prompt-injection channel: read_file is READ-class, unfenced, and does not "
            "taint the session, so the agent could write the text that governs how it "
            "writes and nothing downstream could tell that from the customer having "
            "authored it. Refusing to serve.",
            "",
        ]
        lines += [f"  - {v.path}: {v.reason}" for v in self.violations]
        return "\n".join(lines)


def _agent_ids(user: str = AGENT_USER) -> tuple[int, int] | None:
    """``(uid, gid)`` of the agent principal, or ``None`` if it does not exist.

    ``None`` off-box (a dev machine with no ``hermes`` user) means the check has
    nothing to evaluate against and reports SKIPPED. On the Machine the user
    always exists — the Dockerfile creates it — so a skip there would itself be
    a broken image, which the caller logs.
    """
    try:
        record = pwd.getpwnam(user)
    except KeyError:
        return None
    return record.pw_uid, record.pw_gid


def _writable_by(st: os.stat_result, uid: int, gid: int) -> str:
    """Return a reason string if ``uid``/``gid`` can write, else ``""``.

    Owner, group, and other are all considered. Group matters as much as owner
    here: a root-owned 0664 file in a group the agent belongs to is exactly as
    writable as one it owns.
    """
    mode = st.st_mode
    if st.st_uid == uid and mode & stat.S_IWUSR:
        return f"owned by the agent uid ({uid}) and user-writable (mode {mode & 0o7777:04o})"
    if st.st_gid == gid and mode & stat.S_IWGRP:
        return f"group-writable by the agent gid ({gid}) (mode {mode & 0o7777:04o})"
    if mode & stat.S_IWOTH:
        return f"world-writable (mode {mode & 0o7777:04o})"
    return ""


def verify_spec_dir(
    spec_dir: Optional[str],
    *,
    agent_user: str = AGENT_USER,
) -> OwnershipResult:
    """Pure check over a path. No env reads, so it is exhaustively testable."""
    result = OwnershipResult()
    if not spec_dir:
        result.skipped_reason = f"{SPEC_DIR_ENV} unset — no spec tree to check"
        return result

    root = Path(spec_dir)
    if not root.exists():
        result.skipped_reason = f"{spec_dir} does not exist — no spec installed"
        return result

    ids = _agent_ids(agent_user)
    if ids is None:
        # A spec tree EXISTS and we cannot say who may write it. Fail closed:
        # "cannot evaluate" must never read as "permitted", and on the Machine
        # this state is impossible by construction — the Dockerfile creates the
        # user and the exec-drop `setpriv --reuid=hermes` would fail without it —
        # so reaching here means the image is broken, not that the check is
        # inapplicable. (An ABSENT tree returns above, before this point, so a
        # seat with no installed specs is unaffected.)
        result.violations.append(
            OwnershipViolation(
                str(root),
                f"no {agent_user!r} user on this host, so writability cannot be evaluated "
                "against the agent principal — an unverifiable spec tree is not one to serve",
            )
        )
        return result
    uid, gid = ids

    try:
        root_real = root.resolve()
    except OSError as exc:
        result.violations.append(OwnershipViolation(str(root), f"cannot resolve ({exc})"))
        return result

    paths = [root] + sorted(root.rglob("*"))[:_MAX_PATHS]
    for path in paths:
        result.checked += 1
        # lstat, not stat: a symlink's own mode must be read without following
        # it, and following would also let a link to a missing target read as an
        # absent file rather than as the escape it is.
        try:
            st = path.lstat()
        except OSError as exc:
            # Fail-closed: an unstattable path inside the tree is a substrate we
            # cannot check, and an unverifiable spec tree is not one to serve.
            result.violations.append(OwnershipViolation(str(path), f"cannot stat ({exc})"))
            continue

        if stat.S_ISLNK(st.st_mode):
            try:
                target = path.resolve()
                target.relative_to(root_real)
            except (OSError, ValueError):
                result.violations.append(
                    OwnershipViolation(
                        str(path),
                        "symlink pointing outside the spec tree — a link is a write path "
                        "to its target wearing the tree's name",
                    )
                )
            continue

        reason = _writable_by(st, uid, gid)
        if reason:
            kind = "directory" if stat.S_ISDIR(st.st_mode) else "file"
            extra = (
                " — directory write permits create, replace, and rename even when every file inside is read-only"
                if kind == "directory"
                else ""
            )
            result.violations.append(OwnershipViolation(str(path), f"{kind} is {reason}{extra}"))

    return result


# ---------------------------------------------------------------------------
# Boot entry (pytest-free)
# ---------------------------------------------------------------------------


def verify_at_boot(
    env: Optional[Mapping[str, str]] = None,
    *,
    agent_user: str = AGENT_USER,
) -> int:
    """Boot-time entry. ``0`` to proceed, ``3`` to refuse.

    Mirrors ``invariant_7.verify_at_boot``: the exit code is the load-bearing
    refusal, stderr carries the offending paths. Importable without pytest —
    this is the substrate boot path.

    ``agent_user`` is a keyword argument and deliberately NOT read from the
    environment. Tests pass the current user so the whole shim runs off-box,
    where no ``hermes`` account exists; an env knob would instead hand anyone who
    can set env a way to point the check at a user that owns nothing and turn the
    gate into a no-op.
    """
    e = os.environ if env is None else env
    result = verify_spec_dir(e.get(SPEC_DIR_ENV), agent_user=agent_user)
    if result.passed:
        if result.skipped_reason:
            log.info("spec_dir_ownership: %s", result.skipped_reason)
        else:
            log.info(
                "spec_dir_ownership: %d path(s) verified root-owned and not agent-writable",
                result.checked,
            )
        return 0
    print(result.refusal_message(), file=sys.stderr)
    return 3


# ---------------------------------------------------------------------------
# Substrate-runner entrypoint (run_invariants.py compatibility)
# ---------------------------------------------------------------------------


def _self_check_fixtures() -> tuple[bool, str]:
    """Boot-time smoke fixtures. Full coverage lives in the tests dir.

    Builds a real temp tree rather than mocking stat: the check's whole subject
    is what the filesystem says, and a fixture that faked that would prove
    nothing about the thing being defended.
    """
    import tempfile

    ids = _agent_ids()
    if ids is None:
        return True, "SKIP: no hermes user on this host — writability is unevaluable"

    with tempfile.TemporaryDirectory(prefix="spec-own-") as tmp:
        root = Path(tmp) / "specs"
        (root / "classes" / "staff").mkdir(parents=True)
        body = root / "classes" / "staff" / "voice.md"
        body.write_text("spec\n")

        # No chmod on the clean fixture. mkdir/write_text under a 0700 temp dir
        # already produce a tree nothing named `hermes` can write, which is the
        # condition being asserted; setting the real 0755/0644 here would only
        # widen it. The live tree's permissions are entrypoint.sh's to set.
        ok = verify_spec_dir(str(root))
        if not ok.passed:
            return False, f"FAIL: clean tree reported violations: {ok.refusal_message()}"

        # The negative fixture. World-writable is the one agent-writable state
        # reachable without root, so it is what the smoke check uses to prove
        # the loop actually refuses rather than merely running.
        # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
        os.chmod(body, 0o666)
        bad = verify_spec_dir(str(root))
        if bad.passed:
            return False, "FAIL: a world-writable spec body should have been refused"
        if "world-writable" not in bad.violations[0].reason:
            return False, f"FAIL: unexpected reason {bad.violations[0].reason!r}"

    return True, "PASS: spec-dir ownership check refuses an agent-writable spec tree (2 of 2)"


def run() -> tuple[bool, str]:
    """Substrate-runner shape — boot-time smoke check.

    The LIVE check is :func:`verify_at_boot`, called from entrypoint.sh against
    the real env.
    """
    try:
        return _self_check_fixtures()
    except Exception as e:  # noqa: BLE001 - boot self-check: any raise is a FAIL line for the substrate runner, never a crash at boot
        return False, f"FAIL: spec-dir ownership self-check raised {type(e).__name__}: {e}"


__all__ = [
    "AGENT_USER",
    "SPEC_DIR_ENV",
    "OwnershipResult",
    "OwnershipViolation",
    "run",
    "verify_at_boot",
    "verify_spec_dir",
]


if __name__ == "__main__":
    sys.exit(0 if verify_at_boot() == 0 else 3)
