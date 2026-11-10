"""The staff-mailbox read rule exists twice, byte for byte (staff_mailbox_rule.py).

This connector runs it for ``list_staff_messages`` and ``read_staff_message``;
the gateway runs ``operator/hermes-smd-overlay/shared/staff_mailbox_rule.py`` for
``mail_spool_message``. If the two ever differ, one path can read a mailbox the
other refuses: the approver refusal (ADR 0089 amendment 5a) was once in this
copy alone.
"""

from __future__ import annotations

from pathlib import Path

_CONNECTOR = Path(__file__).resolve().parents[1] / "msgraph_mail_connector" / "staff_mailbox_rule.py"
_OVERLAY = Path(__file__).resolve().parents[3] / "hermes-smd-overlay" / "shared" / "staff_mailbox_rule.py"


def test_the_two_rules_are_byte_identical():
    assert _OVERLAY.is_file(), f"the overlay-side twin is not at {_OVERLAY}"
    assert _CONNECTOR.read_bytes() == _OVERLAY.read_bytes(), (
        "edit operator/hermes-smd-overlay/shared/staff_mailbox_rule.py and copy it over "
        "operator/connectors/msgraph-mail/msgraph_mail_connector/staff_mailbox_rule.py byte for byte"
    )
