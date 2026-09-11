"""``scope.domain_blocks`` must fence in every spelling an author might use.

A deny control that silently no-ops is worse than no deny control, because the
config reads as protection and the validator agrees. That is what shipped:
``domain_blocks`` was parsed by ``split_authored``, which drops any entry that
neither starts with ``@`` nor parses as an address, so a bare ``firm.example``
resolved to nothing and fenced nobody — while ``authored_policy``'s docstring
and ``tests/customer-yaml-validator.test.ts`` both showed the bare form as the
way to write one.

Found 2026-08-18 while fencing the first production client seat away from its
own firm during bring-up. The existing coverage
(``test_domain_blocks_override_an_allow``) used the ``@``-prefixed form, which
worked, so the gap was invisible.

The asymmetry test at the bottom is the one that must never be "fixed" by making
both lists share a parser again: on an ALLOW list a bare domain must still be
dropped, or a typo becomes a whole-domain grant.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Only `authored_policy` is imported at module scope, deliberately. The spelling
# tests below must fail on the OLD code by asserting a firm address is fenced and
# finding it reachable — a module-level import of the new helper would turn that
# behavioural failure into a collection error, which proves only that a function
# was added, not that anything was ever broken.
from ..recipient_policy import authored_policy

# A whole-domain inbound grant plus principals in admins — the shape of a real
# law-firm seat, which is what makes the block the only way to fence the firm.
FIRM_YAML = """
scope:
  inbound_allow_from:
    - '@examplefirm.example'
    - scott@smd.services
  admins:
    - chris@examplefirm.example
  domain_blocks:
{blocks}
"""

FIRM_MEMBER = "chris@examplefirm.example"
OURS = "scott@smd.services"


def _policy(tmp_path: Path, blocks: str):
    path = tmp_path / "customer.yaml"
    path.write_text(FIRM_YAML.format(blocks=blocks), encoding="utf-8")
    return authored_policy(path)


def test_the_firm_is_reachable_with_no_block(tmp_path: Path) -> None:
    """Without this, every assertion below could pass on a broken fence."""
    policy = _policy(tmp_path, "    []")
    assert policy.allows_recipient(FIRM_MEMBER)
    assert policy.allows_reply_to(FIRM_MEMBER)


@pytest.mark.parametrize(
    "spelling",
    [
        "    - '@examplefirm.example'",  # the form that always worked
        "    - 'examplefirm.example'",  # bare — silently did nothing
        "    - 'someone@examplefirm.example'",  # full address blocks its domain
        "    - 'EXAMPLEFIRM.EXAMPLE'",  # case must not evade
        "    - '  examplefirm.example  '",  # nor whitespace
    ],
)
def test_every_spelling_fences_the_firm(tmp_path: Path, spelling: str) -> None:
    policy = _policy(tmp_path, spelling)
    assert not policy.allows_recipient(FIRM_MEMBER)
    assert not policy.allows_reply_to(FIRM_MEMBER)


def test_a_block_does_not_fence_everyone(tmp_path: Path) -> None:
    """Deny the firm, keep talking to us — a block that blocked all would be no
    use for a bring-up fence and would hide a total failure as a success."""
    policy = _policy(tmp_path, "    - 'examplefirm.example'")
    assert policy.allows_recipient(OURS)
    assert policy.allows_reply_to(OURS)


def test_deny_overrides_an_explicit_admin_entry(tmp_path: Path) -> None:
    """chris@ is named in admins, so he is in `exact`, not merely domain-granted.
    The block must still win, or fencing a firm would mean editing the roster and
    retracting what the engagement authored."""
    policy = _policy(tmp_path, "    - 'examplefirm.example'")
    assert FIRM_MEMBER in policy.exact
    assert not policy.allows_recipient(FIRM_MEMBER)


def test_a_bare_domain_on_an_ALLOW_list_still_grants_nothing() -> None:
    """The asymmetry, stated so a future refactor cannot quietly undo it.

    `split_blocks` reads a bare domain as a domain; `split_authored` must keep
    dropping it. Sharing one parser in the other direction would turn a typo in
    inbound_allow_from into a whole-domain grant.
    """
    from ..recipient_policy import split_authored, split_blocks

    exact, domains = split_authored(["examplefirm.example"])
    assert not exact and not domains

    assert split_blocks(["examplefirm.example"]) == {"examplefirm.example"}


def test_unusable_block_entries_are_dropped_not_guessed() -> None:
    from ..recipient_policy import split_blocks

    assert split_blocks([None, 42, "", "   ", "@"]) == set()
