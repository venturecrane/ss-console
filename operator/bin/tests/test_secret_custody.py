"""Secret custody classification — schema, completeness, and guard behavior.

The load-bearing test is `test_every_staged_secret_is_classified`: it parses the
NAMES `provision-customer.sh` stages and asserts each classifies without raising.
That is the fail-closed drift guard — a customer secret added to the provisioner
without being classified turns red here, which is what keeps the keyless-build
placeholder allowlist complete.

Run::

    cd operator && python -m pytest bin/tests/test_secret_custody.py -v
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import secret_custody as sc

_OP = Path(__file__).resolve().parents[2]
_PROVISION = _OP / "bin" / "provision-customer.sh"

# Secrets a reader can see are unambiguously a customer's own credential — the
# set that MUST classify as customer or the keyless/staging isolation leaks.
_KNOWN_CUSTOMER = {
    "ANTHROPIC_API_KEY",
    "CLIO_CLIENT_ID",
    "CLIO_CLIENT_SECRET",
    "CLIO_ENCRYPTION_KEY",
    "CLIO_TOKENS_ENC_B64",
    "AGENTMAIL_API_KEY",
    "WEBHOOK_SECRET_AGENTMAIL",
    "SMD_WEBHOOK_SIGNING_SECRET",
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    "SMOKEBALL_CLIENT_ID",
    "SMOKEBALL_CLIENT_SECRET",
    "SMOKEBALL_API_KEY",
    "WEBHOOK_SECRET_SMOKEBALL",
    "WEBHOOK_SMOKEBALL_CLIENT_ID",
}


def _staged_secret_names() -> set[str]:
    """NAMES the provisioner stages: `stage_secret_from_env NAME`,
    `prompt_and_set NAME`, and literal `printf '%s=%s\\n' "NAME"` piped to
    `fly secrets import`. Dynamic (manifest-driven) names are not literals and
    are intentionally out of this static parse."""
    text = _PROVISION.read_text(encoding="utf-8")
    names: set[str] = set()
    for m in re.finditer(r"\bstage_secret_from_env\s+([A-Z_][A-Z0-9_]*)", text):
        names.add(m.group(1))
    for m in re.finditer(r"\bprompt_and_set\s+([A-Z_][A-Z0-9_]*)", text):
        names.add(m.group(1))
    # printf '%s=%s\n' "NAME" ... | fly secrets import  (the healthchecks ping url)
    for m in re.finditer(r'''printf\s+'%s=%s\\n'\s+"([A-Z_][A-Z0-9_]*)"''', text):
        names.add(m.group(1))
    return names


def test_contract_every_var_has_valid_custody() -> None:
    # load_contract_custody raises on any missing/invalid custody class.
    custody = sc.load_contract_custody()
    assert custody, "env-consumption contract produced no custody map"
    assert set(custody.values()) <= sc.VALID_CUSTODY


def test_every_staged_secret_is_classified() -> None:
    """FAIL CLOSED: every secret NAME provision-customer.sh stages must classify
    (no UnclassifiedSecret). Adding a staged secret without classifying it here
    is the drift this guard exists to catch."""
    staged = _staged_secret_names()
    # Sanity: the parse found the real surface, not zero.
    assert "ANTHROPIC_API_KEY" in staged and "SMOKEBALL_CLIENT_ID" in staged, (
        "provision-customer.sh parse found too few staged names; the regex drifted"
    )
    unclassified: list[str] = []
    for name in sorted(staged):
        try:
            klass = sc.classify(name)
        except sc.UnclassifiedSecret:
            unclassified.append(name)
            continue
        assert klass in sc.VALID_CUSTODY
    assert not unclassified, f"staged but unclassified (classify these): {unclassified}"


def test_known_customer_secrets_classify_customer() -> None:
    for name in sorted(_KNOWN_CUSTOMER):
        assert sc.classify(name) == sc.CUSTOMER, f"{name} must be customer-owned"


def test_infra_secrets_classify_infra() -> None:
    for name in (
        "R2_ACCESS_KEY_ID",
        "SENTRY_DSN",
        "MACHINE_HEARTBEAT_KEY",
        "WEBHOOK_SECRET_MCP",
        "SMOKEBALL_OAUTH_STATE_KEY",
        "SMOKEBALL_ENVIRONMENT",
        "R2_BUCKET_CONFIG",
    ):
        assert sc.classify(name) == sc.INFRA, f"{name} must be infra"


def test_per_seat_prefix_is_customer() -> None:
    assert sc.classify("ANTHROPIC_API_KEY__ASHTON_PRICE") == sc.CUSTOMER
    assert sc.classify("WEBHOOK_SECRET_AGENTMAIL__PILOT_SMOKEBALL") == sc.CUSTOMER
    assert sc.classify("SMOKEBALL_PROD_CLIENT_SECRET") == sc.CUSTOMER


def test_unknown_secret_fails_closed() -> None:
    with pytest.raises(sc.UnclassifiedSecret):
        sc.classify("SOME_BRAND_NEW_CONNECTOR_TOKEN")


def test_seat_suffix_matches_provisioner_transform() -> None:
    # slug -> upper, '-' -> '_', drop the rest (tr '[:lower:]-' '[:upper:]_' | tr -cd 'A-Z0-9_')
    assert sc._seat_suffix("ashton-price") == "ASHTON_PRICE"
    assert sc._seat_suffix("smd-staging") == "SMD_STAGING"


def test_customer_owned_source_names_expands_per_seat() -> None:
    names = sc.customer_owned_source_names("ashton-price")
    assert "ANTHROPIC_API_KEY__ASHTON_PRICE" in names
    assert "WEBHOOK_SECRET_AGENTMAIL__ASHTON_PRICE" in names
    assert "GOOGLE_SERVICE_ACCOUNT_JSON" in names
    assert "CLIO_ENCRYPTION_KEY" in names
    # No infra names leak into the isolate list.
    assert "R2_ACCESS_KEY_ID" not in names
    assert "SENTRY_DSN" not in names


def test_guard_raises_on_real_customer_value() -> None:
    env = {"ANTHROPIC_API_KEY": "sk-ant-realvalue", "SENTRY_DSN": "https://real@sentry"}
    with pytest.raises(RuntimeError) as exc:
        sc.assert_no_real_customer_secret(env)
    assert "ANTHROPIC_API_KEY" in str(exc.value)
    # Infra value in scope is fine — not named as an offender.
    assert "SENTRY_DSN" not in str(exc.value)


def test_guard_passes_on_placeholder_or_empty() -> None:
    env = {
        "ANTHROPIC_API_KEY": sc.placeholder_for("ANTHROPIC_API_KEY"),
        "CLIO_CLIENT_SECRET": "",
        "SMOKEBALL_CLIENT_SECRET": sc.PLACEHOLDER_SENTINEL + "x",
        "R2_ACCESS_KEY_ID": "real-infra-key-ok",
    }
    sc.assert_no_real_customer_secret(env)  # must not raise


def test_looks_like_placeholder() -> None:
    assert sc.looks_like_placeholder("")
    assert sc.looks_like_placeholder(sc.placeholder_for("X"))
    assert not sc.looks_like_placeholder("sk-ant-real")
