"""Secret custody classification for the keyless-build / inject-at-handoff discipline.

WHY THIS EXISTS. `provision-customer.sh` is driven by `reprovision.sh` =
`infisical run --env=prod --path=/ss -- provision-customer.sh <slug>`, which
injects the ENTIRE /ss vault as env vars and stages the customer-owned ones onto
the Machine in one uninterrupted pass. There is no phase boundary between
"build the overlay against placeholders" and "inject the real customer's
credentials at handoff." The embryonic version of the right pattern —
`reprovision-staging.sh` — is a hand-maintained DENYLIST that fails OPEN: any
customer-owned secret it does not explicitly blank (Smokeball, AgentMail,
per-seat Anthropic, webhook secrets) leaks straight onto the box.

This module is the single classification API. Every provisioning secret is
`infra` (operator/SMD-owned; safe to be real or emulated during a keyless build)
or `customer` (carries or grants access to a specific customer's world; must
enter only at handoff through a door we never see). `classify()` FAILS CLOSED:
an unrecognized secret raises `UnclassifiedSecret`, and the completeness test
(operator/bin/tests/test_secret_custody.py) asserts nothing `provision-customer.sh`
stages raises — so a newly added customer secret cannot silently slip past the
keyless-build placeholder allowlist.

Sources of truth, in order:
  1. operator/contracts/env-consumption.yaml `custody:` field (the ~22 vars it
     already tracks — the boot/agent/broker core).
  2. The connector + provisioning secrets NOT in that contract (it explicitly
     scopes out mcp-subprocess connector creds; see operator/bin/lib/env_arrays.py).
     Those live in the explicit tables below, cited to their provision-customer.sh
     staging sites so the two cannot drift silently.
"""

from __future__ import annotations

from pathlib import Path

import yaml

INFRA = "infra"
CUSTOMER = "customer"
VALID_CUSTODY = frozenset({INFRA, CUSTOMER})

# Placeholder sentinel a keyless build stamps into every customer-owned slot.
# Deliberately unmistakable so `looks_like_placeholder` can never false-positive
# a real credential (no real Anthropic/Clio/Smokeball/AgentMail secret is shaped
# like this).
PLACEHOLDER_SENTINEL = "SMD_KEYLESS_PLACEHOLDER::"


class UnclassifiedSecret(KeyError):
    """A provisioning secret whose custody class is unknown. Fail closed — a new
    customer secret must be classified before it can reach the build path."""


# ---------------------------------------------------------------------------
# Connector + provisioning secrets NOT carried by env-consumption.yaml.
# Each is cited to its provision-customer.sh staging site. Adding a connector
# secret to the provisioner without adding it here fails the completeness test.
# ---------------------------------------------------------------------------

# Customer-owned by EXACT name (staged, or read as a source, by the provisioner).
_CUSTOMER_EXACT: frozenset[str] = frozenset(
    {
        # Clio connector (provision-customer.sh:471-474). CLIO_CLIENT_ID/SECRET
        # are also in env-consumption; the encryption key + seed token are not.
        "CLIO_ENCRYPTION_KEY",
        "CLIO_TOKENS_ENC_B64",
        # AgentMail inbound (provision-customer.sh:494-502). The Svix signing
        # secret + the router forward-verify secret are per-customer.
        "WEBHOOK_SECRET_AGENTMAIL",
        "SMD_WEBHOOK_SIGNING_SECRET",
        # Microsoft Graph delta-poller loopback signing secret (ADR 0078;
        # provision-customer.sh msgraph branch). Per-seat, generated when no
        # override is supplied — the poller signs its loopback with it and the
        # in-Machine Hermes adapter re-verifies. Customer custody (per-seat).
        "WEBHOOK_SECRET_MSGRAPH",
        # Smokeball connector (provision-customer.sh:560-575). Both the
        # environment-specific SOURCE names in /ss and the env-agnostic runtime
        # names the connector reads.
        "SMOKEBALL_STAGING_CLIENT_ID",
        "SMOKEBALL_STAGING_CLIENT_SECRET",
        "SMOKEBALL_STAGING_API_KEY",
        "SMOKEBALL_PROD_CLIENT_ID",
        "SMOKEBALL_PROD_CLIENT_SECRET",
        "SMOKEBALL_PROD_API_KEY",
        "SMOKEBALL_CLIENT_ID",
        "SMOKEBALL_CLIENT_SECRET",
        "SMOKEBALL_API_KEY",
        "SMOKEBALL_REFRESH_TOKEN",
        # The connector's scanned-document transcription (ss#2464) adds NO
        # credential name here: it reuses the seat's own ANTHROPIC_API_KEY,
        # already classified via env-consumption.yaml, delivered to the
        # subprocess by the overlay registry. Its SMOKEBALL_VISION_* tuning vars
        # are non-secret and classified in that same contract.
        # Smokeball webhook ingress (provision-customer.sh:611-616).
        "WEBHOOK_SECRET_SMOKEBALL",
        "WEBHOOK_SMOKEBALL_CLIENT_ID",
        # Microsoft Graph mail connector, app-only (provision-customer.sh:554-557;
        # email-channel-seam spec D5, ADR 0078). All four are per-seat values for
        # the CLIENT's tenant: their tenant id, the app id consented into their
        # tenant, the client secret to that consent, and the pinned operator
        # mailbox. Only CLIENT_SECRET is sensitive; the identifiers ride along
        # so the completeness check classifies every staged name.
        "MSGRAPH_TENANT_ID",
        "MSGRAPH_CLIENT_ID",
        "MSGRAPH_CLIENT_SECRET",
        "MSGRAPH_MAILBOX",
    }
)

# Customer-owned by PREFIX: per-seat and environment-specific names. The
# provisioner reads per-seat overrides like ANTHROPIC_API_KEY__<CID> (:348) and
# WEBHOOK_SECRET_AGENTMAIL__<CID> (:495); a `build:` connector may declare its
# own SMOKEBALL_-family credentials.
_CUSTOMER_PREFIX: tuple[str, ...] = (
    "ANTHROPIC_API_KEY__",
    # Per-seat override forms AGENTMAIL_API_KEY__<CID> / AGENTMAIL_SEND_API_KEY__<CID>
    # (ss#2258). Both keys are inbox-scoped at the vendor, so one shared value
    # across seats would hand a seat a credential for someone else's mailbox.
    "AGENTMAIL_API_KEY__",
    "AGENTMAIL_SEND_API_KEY__",
    "WEBHOOK_SECRET_AGENTMAIL__",
    "WEBHOOK_SECRET_SMOKEBALL__",
    "WEBHOOK_SMOKEBALL_CLIENT_ID__",
    "CLIO_",
    "SMOKEBALL_STAGING_",
    "SMOKEBALL_PROD_",
    # Per-seat override form MSGRAPH_CLIENT_SECRET__<CID> (provision-customer.sh
    # msgraph branch) + any future family member.
    "MSGRAPH_",
    # Per-seat override form WEBHOOK_SECRET_MSGRAPH__<CID>.
    "WEBHOOK_SECRET_MSGRAPH__",
)

# Infra-owned staged/derived names NOT in env-consumption. These are SMD-owned
# masters, values DERIVED from an infra master, or non-secret config strings the
# provisioner happens to stage. Enumerated so the completeness check classifies
# every staged name without falling through to CUSTOMER by accident.
_INFRA_EXACT: frozenset[str] = frozenset(
    {
        # Shared observability (provision-customer.sh, step 6b).
        "SENTRY_DSN",
        # Per-seat since migration 0114: minted by lib/machine_credential.py at
        # provisioning, SMD-owned, never a customer's credential. Infra because
        # the console holds the hash and the seat holds the plaintext; nothing
        # about it is handed to the customer.
        "MACHINE_HEARTBEAT_KEY",
        # Brave Search web-search connector (provision-customer.sh,
        # native:brave-free block; ADR 0070). BRAVE_SEARCH_API_KEY is SMD's
        # SHARED, account-wide key — one key across every seat, SMD-owned, not any
        # customer's credential — so it is infra, safe to be real/emulated in a
        # keyless build and never gated behind the customer-handoff door.
        "BRAVE_SEARCH_API_KEY",
        # Per-customer keys DERIVED from an infra HMAC master (never the master).
        "OPERATOR_RUNTIME_READ_KEY",  # from OPERATOR_RUNTIME_READ_SECRET (:439)
        "WEBHOOK_SECRET_MCP",  # from OPERATOR_MCP_WEBHOOK_SECRET (:454)
        "WEBHOOK_SECRET_HANDOFF",  # == WEBHOOK_SECRET_MCP (:455)
        "SMOKEBALL_OAUTH_STATE_KEY",  # from OPERATOR_OAUTH_STATE_MASTER (:588)
        # Infra HMAC masters / infra tokens read on the provisioning host.
        "OPERATOR_MCP_WEBHOOK_SECRET",
        "OPERATOR_OAUTH_STATE_MASTER",
        "HEALTHCHECKS_API_KEY",
        "HEALTHCHECKS_WEBHOOK_SECRET",
        "HEALTHCHECKS_PING_URL",  # derived ping URL, staged (:720)
        # Non-secret Smokeball config strings staged as secrets for uniformity.
        "SMOKEBALL_ENVIRONMENT",  # 'staging' | 'production' (:577)
        "SMOKEBALL_AUTH_MODE",  # grant mode literal (:581)
        "SMOKEBALL_ACCOUNT_ID",  # multi-account URL prefix (:595)
    }
)


def repo_root() -> Path:
    """ss-console repo root, anchored to THIS module's own location.
    operator/bin/lib/secret_custody.py -> parents[3] == repo root."""
    return Path(__file__).resolve().parents[3]


def contract_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / "operator" / "contracts" / "env-consumption.yaml"


def load_contract_custody(path: Path | None = None) -> dict[str, str]:
    """Return {var: custody} from env-consumption.yaml. Raises if any tracked
    var is missing or carries an invalid custody class (fail closed)."""
    p = path or contract_path()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    vars_ = data.get("vars")
    if not isinstance(vars_, dict) or not vars_:
        raise ValueError(f"env-consumption contract {p} has no `vars` map")
    out: dict[str, str] = {}
    for name, spec in vars_.items():
        if not isinstance(spec, dict):
            raise ValueError(f"{name}: contract entry is not a mapping")
        custody = spec.get("custody")
        if custody not in VALID_CUSTODY:
            raise ValueError(f"{name}: missing or invalid custody {custody!r} (must be one of {sorted(VALID_CUSTODY)})")
        out[name] = custody
    return out


def classify(name: str, contract_custody: dict[str, str] | None = None) -> str:
    """Classify a provisioning secret NAME as INFRA or CUSTOMER. Fails closed
    (`UnclassifiedSecret`) on an unrecognized name so a new customer secret
    cannot silently reach the build path unclassified."""
    contract = load_contract_custody() if contract_custody is None else contract_custody
    if name in contract:
        return contract[name]
    if name in _INFRA_EXACT:
        return INFRA
    if name in _CUSTOMER_EXACT:
        return CUSTOMER
    if any(name.startswith(p) for p in _CUSTOMER_PREFIX):
        return CUSTOMER
    raise UnclassifiedSecret(
        f"{name!r} is not classified. Add it to operator/contracts/env-consumption.yaml "
        f"(custody:) or to operator/bin/lib/secret_custody.py. Default customer-owned "
        f"secrets to `customer` — fail closed."
    )


def is_customer_owned(name: str, contract_custody: dict[str, str] | None = None) -> bool:
    return classify(name, contract_custody) == CUSTOMER


def customer_owned_names(contract_custody: dict[str, str] | None = None) -> set[str]:
    """The exact customer-owned secret NAMES (no per-seat expansion). Prefix
    families are represented by their base entries in the explicit tables plus
    any env-consumption customer vars."""
    contract = load_contract_custody() if contract_custody is None else contract_custody
    names = {n for n, c in contract.items() if c == CUSTOMER}
    names |= set(_CUSTOMER_EXACT)
    return names


def customer_owned_source_names(customer_id: str) -> list[str]:
    """Concrete env var names to ISOLATE (blank or substitute) for a seat before
    running the provisioner in a keyless/staging build. Expands the per-seat
    `__<CID>` suffix used by the Anthropic + webhook source vars
    (provision-customer.sh:348,495,611,614)."""
    suffix = _seat_suffix(customer_id)
    names = set(customer_owned_names())
    # Per-seat source overrides the provisioner reads.
    names.add(f"ANTHROPIC_API_KEY__{suffix}")
    names.add(f"WEBHOOK_SECRET_AGENTMAIL__{suffix}")
    names.add(f"WEBHOOK_SECRET_SMOKEBALL__{suffix}")
    names.add(f"WEBHOOK_SMOKEBALL_CLIENT_ID__{suffix}")
    return sorted(names)


def _seat_suffix(customer_id: str) -> str:
    """Mirror the provisioner's slug->env-suffix transform
    (`tr '[:lower:]-' '[:upper:]_' | tr -cd 'A-Z0-9_'`, provision-customer.sh:348)."""
    out = []
    for ch in customer_id:
        if ch.islower():
            out.append(ch.upper())
        elif ch == "-":
            out.append("_")
        elif ch.isupper() or ch.isdigit() or ch == "_":
            out.append(ch)
        # else drop (matches tr -cd 'A-Z0-9_')
    return "".join(out)


def looks_like_placeholder(value: str) -> bool:
    """True iff the value is an explicit keyless placeholder (or empty). Only the
    sentinel counts — a real credential is never treated as a placeholder."""
    return value == "" or value.startswith(PLACEHOLDER_SENTINEL)


def placeholder_for(name: str) -> str:
    """The placeholder a keyless build stamps into a customer-owned slot."""
    return f"{PLACEHOLDER_SENTINEL}{name}"


def assert_no_real_customer_secret(env: dict[str, str], contract_custody: dict[str, str] | None = None) -> None:
    """KEYLESS-BUILD GUARD. Raise if any env var that classifies as customer-owned
    holds a value that is NOT an explicit placeholder. This is the fail-closed
    backstop against a keyless build accidentally running with a real customer
    credential in scope. Unclassified names are ignored here (the completeness
    test is what forces classification); this guard only judges values."""
    contract = load_contract_custody() if contract_custody is None else contract_custody
    offenders: list[str] = []
    for name, value in env.items():
        try:
            owned = classify(name, contract) == CUSTOMER
        except UnclassifiedSecret:
            continue
        if owned and value and not looks_like_placeholder(value):
            offenders.append(name)
    if offenders:
        raise RuntimeError(
            "keyless build has REAL customer-owned secret(s) in scope: "
            + ", ".join(sorted(offenders))
            + ". Blank/placeholder them before building; real values enter only at handoff."
        )


def _main(argv: list[str]) -> int:
    """Small CLI so shell wrappers consume the classification without re-encoding
    it. `isolate-names <slug>` prints (newline-separated) the customer-owned
    source names a keyless/staging build must blank or substitute for that seat;
    `classify <name>` prints the custody class (exit 2 if unclassified)."""
    if len(argv) >= 3 and argv[1] == "isolate-names":
        for name in customer_owned_source_names(argv[2]):
            print(name)
        return 0
    if len(argv) >= 3 and argv[1] == "classify":
        try:
            print(classify(argv[2]))
        except UnclassifiedSecret as exc:
            print(str(exc), file=__import__("sys").stderr)
            return 2
        return 0
    print("usage: secret_custody.py {isolate-names <slug>|classify <name>}", file=__import__("sys").stderr)
    return 1


if __name__ == "__main__":
    import sys as _sys

    raise SystemExit(_main(_sys.argv))
