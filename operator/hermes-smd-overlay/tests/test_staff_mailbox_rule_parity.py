"""The staff-mailbox read rule exists twice, byte for byte (shared/staff_mailbox_rule.py).

``mail_spool_message`` runs it here, in the gateway's venv; the msgraph-mail
connector runs ``msgraph_mail_connector/staff_mailbox_rule.py`` in its own venv
for ``list_staff_messages`` and ``read_staff_message``. If the two ever differ,
one path can read a mailbox the other refuses: the approver refusal (ADR 0089
amendment 5a) was once in the connector's copy alone.
"""

from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).resolve().parents[1] / "shared" / "staff_mailbox_rule.py"
_TWIN = (
    Path(__file__).resolve().parents[2]
    / "connectors"
    / "msgraph-mail"
    / "msgraph_mail_connector"
    / "staff_mailbox_rule.py"
)


def test_the_two_rules_are_byte_identical():
    assert _TWIN.is_file(), f"the connector-side twin is not at {_TWIN}"
    assert _HERE.read_bytes() == _TWIN.read_bytes(), (
        "edit operator/hermes-smd-overlay/shared/staff_mailbox_rule.py and copy it over "
        "operator/connectors/msgraph-mail/msgraph_mail_connector/staff_mailbox_rule.py "
        "byte for byte"
    )
