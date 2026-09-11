"""Refuse a credential whose VALUE is the wrong kind of thing (ss#2423).

Every custody check in this tree classifies secret NAMES. `secret_custody.py`
decides who owns a name; `seat-readiness.py` reports whether a name is present
on the seat, and structurally cannot do more, because Fly hands back a list of
secret names and never their values.

Name-only checking has a blind spot with a real cost. The A&P production
Smokeball 403 (resolved 2026-08-18, vfy_01M0B7XCHR02P58D66Y725VD3V) was an OAuth
CLIENT SECRET sitting in `SMOKEBALL_PROD_API_KEY` -- twice, first the staging
secret and then the prod one (vfy_01M0B5QT1889RZHR5931VGDGVP). Presence checks
were green throughout: the name was there, the value was the wrong credential.
Seat-readiness correctly caught MISSING on 2026-08-12 and then passed a
structurally impossible value, because it had no way to look.

The only place the value exists is the staging seam in provision-customer.sh, on
its way from the vault to `fly secrets set`. So the check lives here and runs
there.

WHAT THIS IS NOT. It is not a validity check: a well-shaped key can still be
revoked, wrong-tenant, or for the other environment. It refuses values that
CANNOT be right, which is the class that produced the incident, and stays quiet
about everything else. A shape rule that guessed at more would start refusing
good credentials on a provisioning path, which is worse than the 403 it prevents.

NOTHING HERE EVER PRINTS, RETURNS, OR LOGS A SECRET VALUE. Errors name the
variable, the length, and the character class, which is enough to act on and
carries no key material. The tests assert that.
"""

from __future__ import annotations

import re

# Smokeball's API key is an AWS API Gateway key: 40 characters, mixed-case
# alphanumeric. Its Cognito client secrets are 51-52 characters, lowercase and
# digits only. The two are not confusable by length OR by character class, which
# is what makes a shape rule safe here rather than a guess.
_API_KEY_LEN = 40
_API_KEY_RE = re.compile(r"^[A-Za-z0-9]{40}$")

# Where to go when the shape is wrong. The Apps credentials card is the wrong
# place and is where both bad values came from.
_SMOKEBALL_SOURCE = (
    "console.smokeball.com left-nav API section, on the tab for this "
    "region+environment (NOT the Apps credentials card, which is where the "
    "client id/secret live)"
)


def check_api_key_shape(name: str, value: str) -> str | None:
    """Return an actionable error for a malformed API key, or None.

    Applies only to names ending `_API_KEY` whose shape this module knows.
    An unknown key family returns None rather than guessing: a false refusal on
    a provisioning path costs more than the check saves.
    """
    if not name.endswith("_API_KEY"):
        return None
    if "SMOKEBALL" not in name:
        return None
    if value == "":
        # Absence is somebody else's check (seat-readiness reports MISSING), and
        # reporting it here too would double-fail one condition.
        return None
    if _API_KEY_RE.match(value):
        return None

    charclass = []
    if any(c.islower() for c in value):
        charclass.append("lower")
    if any(c.isupper() for c in value):
        charclass.append("upper")
    if any(c.isdigit() for c in value):
        charclass.append("digit")
    if not value.isalnum():
        charclass.append("non-alphanumeric")
    shape = f"length={len(value)} charclass={'+'.join(charclass) or 'empty'}"

    hint = ""
    if len(value) > _API_KEY_LEN and not any(c.isupper() for c in value):
        # The exact shape of the incident: long, lowercase+digits only.
        hint = (
            " This looks like an OAuth client secret (Cognito secrets are 51-52 "
            "chars, lowercase+digits), not an API key."
        )

    return (
        f"{name}: expected a {_API_KEY_LEN}-character mixed-case alphanumeric "
        f"API key, got {shape}.{hint} Source: {_SMOKEBALL_SOURCE}."
    )


def check_not_reused_secret(name: str, value: str, others: dict[str, str]) -> str | None:
    """Return an error if an `*_API_KEY` is byte-equal to any other staged value.

    The cheap generalization the issue asked for, and it is the one rule here
    that needs no per-vendor knowledge: if the API key and the client secret are
    the same bytes, exactly one of them is right, and staging both guarantees a
    403 nobody can read off a presence check.
    """
    if not name.endswith("_API_KEY") or value == "":
        return None
    for other_name, other_value in others.items():
        if other_name == name or other_value == "":
            continue
        if other_value == value:
            return (
                f"{name} is byte-identical to {other_name}. An API key and an "
                "OAuth client secret are different credentials from different "
                "screens; staging one value as both means at least one is wrong."
            )
    return None


def check_staged_secret(name: str, value: str, others: dict[str, str] | None = None) -> str | None:
    """Both rules, reuse first. Returns the first error or None.

    Reuse is checked first on purpose: when a client secret has been pasted into
    the API-key slot, both rules fire, and "these two are the same value" points
    at the mistake more directly than "this is the wrong length".
    """
    reused = check_not_reused_secret(name, value, others or {})
    if reused is not None:
        return reused
    return check_api_key_shape(name, value)
