"""The casework ledger twin, and the rows the overlay writes against it.

``shared/casework_ledger.py`` is a byte-identical copy of ss-console
``operator/workspace_broker/casework_ledger.py``: the broker validates every row
with the canonical, and the overlay derives item keys and reads state with this
copy. If the two drift, a row the overlay composes is refused by the broker, or
a key it derives names nothing. Same discipline as
``tests/test_escalation_ledger_sync.py``: restamp from the canonical, never
hand-edit, and paste the new digest below.

(The end-to-end half that drove a task review and a date-prep brief through the
real ``validate_append`` went with those tools, removed 2026-11-10.)
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_COPY = _REPO_ROOT / "shared" / "casework_ledger.py"

# sha256 of venturecrane/ss-console::operator/workspace_broker/casework_ledger.py
# at the commit this copy was stamped from.
#
# 2026-09-25 (case-manager deadline work): first stamp, from ss-console
# feat/casework-broker 2e511a11 (formatted at the overlay's 100-column line
# length so the byte-identical copy passes this repo's ruff format check).
#
# 2026-09-25b (case-manager done-since): restamped from ss-console
# feat/casework-done-since. Adds the ``step_ran`` event (a date-prep step the
# Operator ran itself, witnessed by its create_memo call) and lets ``mentioned``
# close a date item's untold steps.
#
# 2026-09-25c: restamped. _validate_payload splits its two kind-shape checks
# into _check_kind_shape (ss-console's function-complexity ceiling); rules and
# refusal text unchanged.
# 2026-10-01 (completion by reply): restamped from ss-console. Adds the
# ``complete`` action (a person's word to the deadline digest), accepted on an
# at_stake task from the escalator only, approved only under a named person.
CANONICAL_SHA256 = "31e306d81c4f06e47ba10023c55122789ec37cee167cc55f4348d334bc53e9b3"


def test_copy_matches_the_pinned_canonical_digest() -> None:
    actual = hashlib.sha256(_COPY.read_bytes()).hexdigest()
    assert actual == CANONICAL_SHA256, (
        "shared/casework_ledger.py no longer matches its pinned canonical digest. "
        "It is a byte-identical copy of venturecrane/ss-console::"
        "operator/workspace_broker/casework_ledger.py: restamp it from there and "
        "update CANONICAL_SHA256, never hand-edit it."
    )
