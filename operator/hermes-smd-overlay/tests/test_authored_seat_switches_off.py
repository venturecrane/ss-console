"""No authored seat changes behaviour until its config switches a capability on.

The companion to ``test_authored_seat_registration_unchanged.py`` for the
capabilities that do not show in what a seat registers but in what its code
does per call (ss-console #3946, the guide reply path): request cards without
words, the end-of-turn held line and redraft cap, the per-sender daily limit,
the walled stores, and the per-sender memory switch. Each is read here through
the SAME function the runtime reads it with, over every authored
``operator/customers/*/customer.yaml``: every one must resolve to today's
behaviour. The guide seat's switch-on is a separate config change; when it
lands, its expected row here changes in the same PR, which is the review.

FALSIFIER: author ``escalation.request_card_text: false`` (or any key below) on
any seat and its row fails.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from shared import record_store, request_cards
from shared.customer_config import CustomerConfig
from tests._registration_surface import authored_seats

OVERLAY = Path(__file__).resolve().parents[1]


def _module(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, OVERLAY / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


HELD = _module("plugins/hermes-smd-reply/held_notice.py", "_switch_held_notice")
DAILY = _module("plugins/hermes-smd-webhook-router/daily_limit.py", "_switch_daily_limit")


def switches(raw: dict) -> dict:
    """What each per-call capability resolves to for one seat's config."""
    policies = record_store.authored_policies(CustomerConfig(raw))
    memory = raw.get("memory") if isinstance(raw.get("memory"), dict) else {}
    return {
        "request_card_text": request_cards.cards_carry_text(raw),
        "held_notice": HELD.policy(raw).enabled,
        "daily_limit": DAILY.authored_limit(raw),
        "partitioned_stores": sorted(n for n, p in policies.items() if p.partitioned),
        "managed_stores": sorted(n for n, p in policies.items() if p.managed),
        "peer_memory_off": memory.get("peer_memory_enabled") is False,
    }


TODAY = {
    "request_card_text": True,
    "held_notice": False,
    "daily_limit": None,
    "partitioned_stores": [],
    "managed_stores": [],
    "peer_memory_off": False,
}


# The one seat that authors the switches (ss-console #3946): SMD's guide seat,
# switched on for representative training. Every other seat reads as TODAY.
AUTHORED_ON = {
    "smd-guide": {
        "request_card_text": False,
        "held_notice": True,
        "daily_limit": 60,
        "partitioned_stores": ["rep-progress"],
        "managed_stores": ["rep-progress"],
        "peer_memory_off": True,
    }
}


@pytest.mark.parametrize("seat", authored_seats(), ids=lambda p: p.parent.name)
def test_every_authored_seat_keeps_todays_behaviour(seat: Path) -> None:
    raw = yaml.safe_load(seat.read_text(encoding="utf-8"))
    assert switches(raw) == AUTHORED_ON.get(seat.parent.name, TODAY)


def test_the_switches_can_be_seen_on() -> None:
    """The falsifier: the guide seat with every key authored reads as on."""
    guide = next(p for p in authored_seats() if p.parent.name == "smd-guide")
    raw = yaml.safe_load(guide.read_text(encoding="utf-8"))
    raw["escalation"]["request_card_text"] = False
    raw["send_policy"]["reply"]["held_notice"] = True
    raw["sender_enrollment"]["daily_limit"] = 30
    raw["memory"]["peer_memory_enabled"] = False
    raw["record_stores"] = [
        {"name": "rep-notes", "path": "/opt/data/rep/notes", "partition": "sender"},
        {
            "name": "rep-progress",
            "path": "/opt/data/rep/progress",
            "partition": "sender",
            "managed": True,
        },
    ]
    assert switches(raw) == {
        "request_card_text": False,
        "held_notice": True,
        "daily_limit": 30,
        "partitioned_stores": ["rep-notes", "rep-progress"],
        "managed_stores": ["rep-progress"],
        "peer_memory_off": True,
    }
