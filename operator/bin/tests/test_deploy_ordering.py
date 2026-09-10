"""Guard the Operator deploy ORDERING in provision-customer.sh.

The 2026-06-11 ~18-min customer-zero outage was caused by `fly secrets deploy`
committing staged secret REMOVALS to the OLD, still-running image BEFORE the new
image rolled — the old image crash-looped on its now-missing required var, hit
max-restart, and STOPPED with no self-heal. The fix relies on `fly deploy`
applying staged secrets atomically with the new release, and on NEVER committing
secrets to the live machine first.

These tests encode that as a permanent invariant (failure -> permanent guard) so
the class cannot regress:

  1. No `fly secrets deploy` runs before the `fly deploy` image roll.
  2. A post-deploy guard fails loudly if any secret remains STAGED (the
     staged-but-unapplied path the atomic claim depends on).

Run::

    cd operator && python -m pytest bin/tests/test_deploy_ordering.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "bin" / "provision-customer.sh"
_BOOTSTRAP = Path(__file__).resolve().parents[2] / "templates" / "bootstrap.sh"
_ENTRYPOINT = Path(__file__).resolve().parents[2] / "templates" / "entrypoint.sh"


def _code_lines(script: Path = _SCRIPT) -> list[str]:
    """Script lines with comment-only lines blanked (so prose can't match)."""
    out = []
    for line in script.read_text(encoding="utf-8").splitlines():
        out.append("" if line.lstrip().startswith("#") else line)
    return out


def _first_index(lines: list[str], pattern: str) -> int:
    rx = re.compile(pattern)
    for i, line in enumerate(lines):
        if rx.search(line):
            return i
    return -1


def test_no_secrets_deploy_before_image_roll() -> None:
    """`fly secrets deploy` must never run before the `fly deploy` image roll.

    `fly secrets deploy` redeploys the CURRENT (old) image with the staged
    secrets; running it before the new image rolls is exactly the outage.
    """
    lines = _code_lines()
    deploy_idx = _first_index(lines, r"\bfly\s+deploy\s+--config\b")
    assert deploy_idx != -1, "could not find the `fly deploy --config` image roll"

    early = [
        i + 1
        for i, line in enumerate(lines)
        if i < deploy_idx and re.search(r"\bfly\s+secrets\s+deploy\b", line)
    ]
    assert not early, (
        f"`fly secrets deploy` appears before the image roll at line(s) {early} — "
        "this commits staged secrets to the OLD running image and caused the "
        "2026-06-11 outage. Let `fly deploy` apply staged secrets atomically."
    )


def test_post_deploy_no_staged_secrets_guard_exists() -> None:
    """After the deploy, the script must fail if any secret is left STAGED."""
    lines = _code_lines()
    deploy_idx = _first_index(lines, r"\bfly\s+deploy\s+--config\b")
    guard_idx = _first_index(lines, r"fly\s+secrets\s+list\b.*\|\s*grep\s+-qw\s+Staged")
    assert guard_idx != -1, (
        "missing the post-deploy staged-secret guard "
        "(`fly secrets list ... | grep -qw Staged`)"
    )
    assert guard_idx > deploy_idx, (
        "the staged-secret guard must run AFTER the `fly deploy` image roll, "
        f"not before (guard line {guard_idx + 1}, deploy line {deploy_idx + 1})"
    )
    # The guard must terminate provisioning (die) when staged secrets remain.
    window = "\n".join(lines[guard_idx : guard_idx + 4])
    assert "die " in window, (
        "the staged-secret guard must `die` when secrets remain staged"
    )


# ---------------------------------------------------------------------------
# bootstrap.sh: account-wide R2 key strip ordering (OP-P2-1)
# ---------------------------------------------------------------------------


def test_r2_account_key_strip_before_any_same_uid_child() -> None:
    """`unset R2_ACCESS_KEY_ID` must run BEFORE the webhook-gate launch.

    The webhook gate is a same-uid (hermes) background child. A child forked
    BEFORE the strip keeps the account-wide R2 key in its env, and a
    code-executing agent can read it from that sibling's /proc/<pid>/environ
    (verified on staging) and write the R2 config object — re-opening the
    self-loopback ceiling-raise (ADR 0044 Decision 8 / OP-P2-1). Stripping before
    any child is forked closes the leak. This guard fails loudly if a future edit
    moves the strip back after a child launch.
    """
    lines = _code_lines(_BOOTSTRAP)
    strip_idx = _first_index(lines, r"\bunset\b.*\bR2_ACCESS_KEY_ID\b")
    gate_idx = _first_index(lines, r"hermes-smd-webhook-gate")
    gateway_idx = _first_index(lines, r"\bexec\b.*\bhermes\b.*\bgateway\s+run\b")

    assert strip_idx != -1, "could not find the `unset R2_ACCESS_KEY_ID` strip in bootstrap.sh"
    assert gate_idx != -1, "could not find the webhook-gate launch in bootstrap.sh"
    assert gateway_idx != -1, "could not find the `exec ... hermes ... gateway run` line"

    assert strip_idx < gate_idx, (
        f"the account-wide R2 key strip (line {strip_idx + 1}) must run BEFORE the "
        f"webhook-gate launch (line {gate_idx + 1}) — a child forked before the strip "
        "retains the key, leaking it to a code-executing agent via /proc (OP-P2-1)."
    )
    assert strip_idx < gateway_idx, (
        "the R2 key strip must run before the gateway exec (it already did; keep it so)."
    )


def test_r2_account_key_strip_after_the_boot_time_fetches() -> None:
    """The strip must stay AFTER the last `aws s3 cp` that reads the key.

    The customer.yaml fetch (Step 2) and voice-vault sync (Step 2a) consume the
    account-wide key; moving the strip before them breaks the boot. This pins the
    lower bound so the strip can't be hoisted too far.
    """
    lines = _code_lines(_BOOTSTRAP)
    strip_idx = _first_index(lines, r"\bunset\b.*\bR2_ACCESS_KEY_ID\b")
    last_s3_cp = max(
        (i for i, line in enumerate(lines) if re.search(r"\baws\s+s3\s+cp\b", line)),
        default=-1,
    )
    assert last_s3_cp != -1, "could not find any `aws s3 cp` in bootstrap.sh"
    assert strip_idx > last_s3_cp, (
        f"the R2 key strip (line {strip_idx + 1}) must run AFTER the last `aws s3 cp` "
        f"(line {last_s3_cp + 1}) that reads the key, or the boot-time fetches break."
    )


def test_webhook_gate_launched_with_r2_key_scrubbed() -> None:
    """The webhook-gate respawn wrapper must be EXEC'd with the account-wide R2
    key scrubbed (``env -u R2_ACCESS_KEY_ID -u R2_SECRET_ACCESS_KEY``), not a
    forked ``( ) &`` subshell.

    A forked subshell is not exec'd, so even after the ``unset`` its
    /proc/<pid>/environ still exposes the exec-time snapshot of the key to a
    same-uid code-executing agent (OP-P2-1; verified on staging — the wrapper held
    the key while its children were clean). The ``env -u … bash -c`` exec rebuilds
    a fresh environ without the key. This guard fails if the launch reverts to a
    bare subshell.
    """
    lines = _code_lines(_BOOTSTRAP)
    gate_idx = _first_index(lines, r"hermes-smd-webhook-gate")
    assert gate_idx != -1, "could not find the webhook-gate launch in bootstrap.sh"
    window = "\n".join(lines[max(0, gate_idx - 8) : gate_idx + 1])
    assert "env -u R2_ACCESS_KEY_ID" in window and "R2_SECRET_ACCESS_KEY" in window, (
        "the webhook-gate respawn loop must be launched via "
        "`env -u R2_ACCESS_KEY_ID -u R2_SECRET_ACCESS_KEY bash -c …` so the "
        "persistent wrapper's /proc/environ does not expose the account-wide R2 "
        "key (OP-P2-1) — a forked `( ) &` subshell leaks it."
    )


def test_disabled_skills_reconciler_launched_with_r2_key_scrubbed() -> None:
    """SEC-23, corrected by ss#2420: the disabled-skills reconciler must be
    LAUNCHED via ``env -u R2_ACCESS_KEY_ID -u R2_SECRET_ACCESS_KEY bash -c``,
    never as a forked ``( ) &`` subshell with an inner ``unset``.

    The prior form (which THIS test used to pin) did not work: a fork is never
    exec'd, so its /proc/<pid>/environ stays the exec-time snapshot and the
    ``unset`` inside it scrubs only the shell's variable table. The lingering
    fork held the account-wide key for its whole 120-300s converge window at
    the agent uid — the actual producer of every ss#2420 first-smoke FAIL,
    proven live on pilot-smokeball 2026-08-19 (pid 1055, `bash /app/bootstrap.sh`,
    hermes uid, R2 names in environ; the exec'd gateway clean). Same mechanism,
    same fix shape as the webhook-gate launch, whose test sits above this one.
    This guard fails if the launch reverts to a bare fork."""
    lines = _code_lines(_BOOTSTRAP)
    loop_idx = _first_index(lines, r'while \[ "\$\{_ticks\}" -lt 60 \]')
    assert loop_idx != -1, "could not find the disabled-skills reconciler loop"
    window = "\n".join(lines[max(0, loop_idx - 6) : loop_idx])
    assert re.search(
        r"env -u R2_ACCESS_KEY_ID -u R2_SECRET_ACCESS_KEY bash -c", window
    ), (
        "the disabled-skills reconciler must be launched via "
        "`env -u R2_ACCESS_KEY_ID -u R2_SECRET_ACCESS_KEY bash -c …` — an exec "
        "that rebuilds a clean environ. A forked `( ) &` subshell keeps the "
        "account-wide key in /proc/environ for its whole life regardless of any "
        "inner `unset` (ss#2420)."
    )
    assert "(" not in window.replace("$((", "").replace("))", ""), (
        "a `(` immediately before the reconciler loop suggests the launch "
        "reverted to a forked subshell — the exact ss#2420 leak shape."
    )


def test_runtime_read_key_stripped_from_agent_before_gateway_exec() -> None:
    """SEC-28: OPERATOR_RUNTIME_READ_KEY must be stripped from the agent (hermes
    gateway) env — AFTER the webhook-gate launch (which serves + validates the seam
    and keeps its inherited copy) and BEFORE the gateway exec — so a code-executing
    agent cannot mint its own read-seam bearer."""
    lines = _code_lines(_BOOTSTRAP)
    strip_idx = _first_index(lines, r"\bunset\b.*\bOPERATOR_RUNTIME_READ_KEY\b")
    gate_idx = _first_index(lines, r"hermes-smd-webhook-gate")
    gateway_idx = _first_index(lines, r"\bexec\b.*\bhermes\b.*\bgateway\s+run\b")
    assert strip_idx != -1, "OPERATOR_RUNTIME_READ_KEY is never unset in bootstrap.sh (SEC-28)"
    assert gateway_idx != -1, "could not find the `exec ... hermes ... gateway run` line"
    assert strip_idx < gateway_idx, (
        "OPERATOR_RUNTIME_READ_KEY must be unset BEFORE the gateway exec so the agent "
        "env does not carry the read-seam bearer (SEC-28)."
    )
    assert gate_idx == -1 or strip_idx > gate_idx, (
        "the strip must run AFTER the webhook-gate launch — the gate validates the "
        "seam and needs the key; only the agent loses it (SEC-28)."
    )


def test_heartbeat_secrets_stripped_from_agent_before_gateway_exec() -> None:
    """ADR 0023: MACHINE_HEARTBEAT_KEY (this seat's own bearer since migration
    0114; the shared fleet bearer before it) and HEALTHCHECKS_PING_URL must be
    stripped from the agent (hermes gateway) env — AFTER the webhook-gate launch
    (whose emitter holds the inherited copies) and BEFORE the gateway exec. An
    agent holding the key could write a false "green" for its own seat (and,
    before 0114, forge another tenant's via X-Tenant-Slug, ADR 0023
    locked-decision #10); the ping URL would let it spoof liveness."""
    lines = _code_lines(_BOOTSTRAP)
    gate_idx = _first_index(lines, r"hermes-smd-webhook-gate")
    gateway_idx = _first_index(lines, r"\bexec\b.*\bhermes\b.*\bgateway\s+run\b")
    assert gateway_idx != -1, "could not find the `exec ... hermes ... gateway run` line"
    for var in ("MACHINE_HEARTBEAT_KEY", "HEALTHCHECKS_PING_URL"):
        strip_idx = _first_index(lines, rf"\bunset\b.*\b{var}\b")
        assert strip_idx != -1, f"{var} is never unset in bootstrap.sh (ADR 0023 agent strip)"
        assert strip_idx < gateway_idx, (
            f"{var} must be unset BEFORE the gateway exec so the agent env does not "
            "carry the heartbeat secret (ADR 0023)."
        )
        assert gate_idx == -1 or strip_idx > gate_idx, (
            f"{var} strip must run AFTER the webhook-gate launch — the gate's emitter "
            "needs it; only the agent loses it (ADR 0023)."
        )


# ---------------------------------------------------------------------------
# entrypoint.sh: root config-applier must launch BEFORE the gateway exec-drop
# ---------------------------------------------------------------------------


def test_config_applier_launches_as_root_before_exec_drop() -> None:
    """The `python -m config_applier` launch must come BEFORE the
    `exec setpriv --reuid=hermes` drop (ADR 0044 Decision 5 / OP-P2-1).

    Forked before the exec, the applier is a root background child that survives
    the exec and keeps uid 0 — the only uid that can write the hermes-owned
    /opt/data/customer.yaml, and a uid the hermes agent cannot read R2 creds from
    via /proc. If the launch moved AFTER the exec it would run as hermes: it could
    no longer write the config file, and the R2 pull credential would land in a
    hermes-readable process — re-opening the self-loopback. This guard fails
    loudly on that regression.
    """
    lines = _code_lines(_ENTRYPOINT)
    launch_idx = _first_index(lines, r"python\b.*-m\s+config_applier")
    # `exec setpriv` and `--reuid=hermes` sit on separate continuation lines, so
    # match the `exec setpriv` head only.
    exec_idx = _first_index(lines, r"\bexec\b\s+setpriv\b")

    assert launch_idx != -1, "could not find the `python -m config_applier` launch in entrypoint.sh"
    assert exec_idx != -1, "could not find the `exec setpriv --reuid=hermes` drop in entrypoint.sh"
    assert launch_idx < exec_idx, (
        f"the config-applier launch (line {launch_idx + 1}) must run BEFORE the "
        f"exec-drop to hermes (line {exec_idx + 1}) so it stays root — moving it after "
        "would run the applier as hermes and re-open the OP-P2-1 credential leak."
    )
