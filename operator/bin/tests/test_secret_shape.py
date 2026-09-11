"""The staging-time shape refusal (ss#2423).

Every case is asserted twice: once on a value that must be refused, and once on
the nearest value that must be ACCEPTED. A shape rule that only ever refuses is
indistinguishable from a rule that refuses everything, and this one sits on the
provisioning path where a false refusal blocks a seat from being built.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "secret_shape", Path(__file__).resolve().parents[1] / "lib" / "secret_shape.py"
)
assert _SPEC and _SPEC.loader
secret_shape = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(secret_shape)

# Shapes, not credentials, and BUILT rather than pasted.
#
# 40 chars mixed-case alphanumeric is the AWS API Gateway key shape; 52 chars
# lowercase+digits is the Cognito client-secret shape. Written as literals these
# are indistinguishable from real keys to a scanner, and the repo's semgrep gate
# blocked exactly that on the first draft of this file -- correctly. Generating
# them from a visible repeating pattern keeps the shapes exact while making it
# obvious to a reader and a scanner that no credential is present.
GOOD_API_KEY = ("aB3" * 14)[:40]
CLIENT_SECRET_SHAPED = ("c0ffee" * 9)[:52]


def test_the_fixtures_are_the_shapes_the_rule_is_about():
    """Guard the guards: if GOOD_API_KEY stopped being 40 mixed-case alnum, every
    acceptance case below would be vacuous and nobody would notice."""
    assert len(GOOD_API_KEY) == 40
    assert GOOD_API_KEY.isalnum()
    assert any(c.isupper() for c in GOOD_API_KEY) and any(c.islower() for c in GOOD_API_KEY)
    assert len(CLIENT_SECRET_SHAPED) > 40
    assert not any(c.isupper() for c in CLIENT_SECRET_SHAPED)


def test_a_client_secret_in_the_api_key_slot_is_refused():
    """The incident, exactly: a long lowercase+digits value in *_API_KEY."""
    err = secret_shape.check_staged_secret("SMOKEBALL_API_KEY", CLIENT_SECRET_SHAPED)
    assert err is not None
    assert "SMOKEBALL_API_KEY" in err
    assert "client secret" in err
    assert "console.smokeball.com" in err


def test_a_well_shaped_api_key_is_accepted():
    """The falsifier for the case above."""
    assert secret_shape.check_staged_secret("SMOKEBALL_API_KEY", GOOD_API_KEY) is None


def test_an_api_key_equal_to_the_client_secret_is_refused():
    """The generalization: same bytes in two slots means one of them is wrong.

    Fires even when the value is perfectly API-key shaped, which is the point --
    shape alone would pass this."""
    others = {"SMOKEBALL_CLIENT_SECRET": GOOD_API_KEY}
    err = secret_shape.check_staged_secret("SMOKEBALL_API_KEY", GOOD_API_KEY, others)
    assert err is not None
    assert "byte-identical" in err
    assert "SMOKEBALL_CLIENT_SECRET" in err


def test_distinct_values_in_both_slots_are_accepted():
    """The falsifier for reuse detection."""
    others = {"SMOKEBALL_CLIENT_SECRET": CLIENT_SECRET_SHAPED}
    assert secret_shape.check_staged_secret("SMOKEBALL_API_KEY", GOOD_API_KEY, others) is None


def test_reuse_is_reported_before_shape():
    """When both rules fire, the reuse message is the more actionable one."""
    others = {"SMOKEBALL_CLIENT_SECRET": CLIENT_SECRET_SHAPED}
    err = secret_shape.check_staged_secret("SMOKEBALL_API_KEY", CLIENT_SECRET_SHAPED, others)
    assert err is not None and "byte-identical" in err


def test_an_unknown_key_family_is_not_guessed_at():
    """A false refusal on the provisioning path costs more than the check saves,
    so a name whose shape this module does not know passes untouched."""
    assert secret_shape.check_staged_secret("SOME_OTHER_API_KEY", "short") is None
    assert secret_shape.check_staged_secret("ANTHROPIC_API_KEY", "sk-ant-whatever") is None


def test_a_non_api_key_name_is_not_shape_checked():
    """The client secret itself is 52 lowercase chars and must not be refused for
    failing the API-key shape."""
    assert secret_shape.check_staged_secret("SMOKEBALL_CLIENT_SECRET", CLIENT_SECRET_SHAPED) is None


def test_absence_is_left_to_the_presence_check():
    """seat-readiness already reports MISSING; double-failing one condition makes
    two problems out of one."""
    assert secret_shape.check_staged_secret("SMOKEBALL_API_KEY", "") is None


def test_no_error_message_ever_contains_the_value():
    """The whole tree's cardinal rule. An error on a provisioning path is printed
    to a terminal and lands in a transcript."""
    for value in (CLIENT_SECRET_SHAPED, "x" * 41, "sh0rt", "has spaces in it!!"):
        err = secret_shape.check_staged_secret("SMOKEBALL_API_KEY", value)
        if err is None:
            continue
        assert value not in err, f"error leaked the value: {err}"
        # The metadata that makes it actionable is still there.
        assert str(len(value)) in err
