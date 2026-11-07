"""Whether a logic-in-code mechanism records or refuses, read off the seat's own
customer.yaml on every call (ADR 0104).

    logic_in_code:
      printed_source_check: observe   # or enforce
      post_record_derivation: observe # or enforce
      conflict_fence: observe         # or enforce
      fixed_wording: observe          # or enforce
      wording_rules: observe          # or enforce
      library_setup: observe          # or enforce
      library_edit_notice: observe    # or enforce
      status_from_events: observe     # or enforce
      library_readback: observe       # or enforce

WHY THE SWITCH EXISTS. A merge reaches a client seat without a person, in the
ring before the pilot. Anything that can refuse an act or change what a firm
sees therefore ships switched off, per seat, and is switched on only after it
has been proven.

THE MEANING OF A VALUE. Absent (no file, no block, no key, or a key with no
value) is ``observe``: the mechanism records exactly what it would have refused,
in its own audit row, and then behaves as it did before it existed. A present
value that is not one of the two is ``enforce``, and so is a config that cannot
be read, parsed or understood, because an unreadable switch must not hold a door
open. The validator refuses such a value at boot, so it is reachable only by a
hand edit on the volume.

READ FRESH ON EVERY CALL, cached nowhere, so the module holds no state between
calls. A switch reaches a seat the way ``matter_resolution_mode`` does: the
config applier treats the block as rebuild-class, so a change arrives with a
release, behind the busy gate, never as a live edit.

ONE FILE, THREE COPIES. The connector, the overlay and the broker run in separate
virtualenvs, and none of them imports another. (The jobs runner reads the
connector's copy: its venv carries that package, and the bundle ships the two
together.) So this module imports nothing
but the standard library and PyYAML (which all three already carry) and it lives
byte for byte in three places:

    operator/hermes-smd-overlay/shared/logic_in_code.py  (canonical)
    operator/connectors/smokeball/smokeball_connector/logic_in_code.py
    operator/workspace_broker/logic_in_code.py

``operator/tests/test_logic_in_code_sync.py`` fails on any difference. Edit the
canonical and copy it over the other two. The jobs runner installs the connector,
so it reads the connector's copy (``jobs/firm_wording.py``) and needs no fourth.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

BLOCK = "logic_in_code"
OBSERVE = "observe"
ENFORCE = "enforce"
MODES = (OBSERVE, ENFORCE)
DEFAULT_MODE = OBSERVE

PRINTED_SOURCE_CHECK = "printed_source_check"
POST_RECORD_DERIVATION = "post_record_derivation"
CONFLICT_FENCE = "conflict_fence"
FIXED_WORDING = "fixed_wording"
#: Whether the job lanes put a firm's confirmed wording rules into what they send
#: the model, and the install letter calls such a rule in effect
#: (``shared/wording_rules.py``). Observe: neither, exactly as before.
WORDING_RULES = "wording_rules"
#: Whether a firm's "set up our ..." request may tell the firm what its own
#: documents offer and, later, write a library file (``jobs/library_setup``).
#: Observe: the job reads and counts only; it sends nothing and writes nothing.
#: A value that cannot be read holds the job for SMD, never writes or sends.
LIBRARY_SETUP = "library_setup"
#: Whether a drafted document's reply tells the firm that its own save of its
#: library file was not used (``library_setup_wording.EDIT_NOT_USED``). Observe:
#: the firm is told nothing; SMD is paged either way. The Captain's call.
LIBRARY_EDIT_NOTICE = "library_edit_notice"
#: Whether the litigation lane prints a defendant's status worked out from its
#: dated, cited events (``jobs/litigation/eventmode.py``). Observe: the lane
#: prints what it printed before and records what the events would have said.
STATUS_FROM_EVENTS = "status_from_events"
#: Whether a failed read-back of a filed firm document pages SMD
#: (``jobs/library_readback``). Observe: the score is written and nobody is
#: paged. Nothing is sent to the firm in either mode.
LIBRARY_READBACK = "library_readback"
KEYS = (
    PRINTED_SOURCE_CHECK,
    POST_RECORD_DERIVATION,
    CONFLICT_FENCE,
    FIXED_WORDING,
    WORDING_RULES,
    LIBRARY_SETUP,
    LIBRARY_EDIT_NOTICE,
    STATUS_FROM_EVENTS,
    LIBRARY_READBACK,
)

#: The same file by either name: the entrypoint exports both, the gateway and
#: its children read the first, the broker's clean environment carries the
#: second, and a connector process inherits neither, so it reads the fixed path
#: (``smokeball_connector/library.py``).
PATH_ENVS = ("SMD_CUSTOMER_YAML_PATH", "SMD_CUSTOMER_YAML")
DEFAULT_PATH = "/var/lib/smd-config/customer.yaml"


@dataclass(frozen=True)
class Switch:
    key: str
    mode: str
    error: str | None = None

    @property
    def enforcing(self) -> bool:
        return self.mode != OBSERVE


def live_path() -> str:
    for name in PATH_ENVS:
        value = os.environ.get(name)
        if value:
            return value
    return DEFAULT_PATH


def parse_switch(data: object, key: str) -> Switch:
    """The mode a parsed customer.yaml authors for one switch."""
    if key not in KEYS:
        return Switch(key, ENFORCE, error=f"{key!r} is not a {BLOCK} switch")
    if data is None:
        return Switch(key, DEFAULT_MODE)
    if not isinstance(data, dict):
        return Switch(key, ENFORCE, error="customer.yaml is not a mapping")
    block = data.get(BLOCK)
    if block is None:
        return Switch(key, DEFAULT_MODE)
    if not isinstance(block, dict):
        return Switch(key, ENFORCE, error=f"{BLOCK} is not a mapping")
    value = block.get(key)
    if value is None:
        return Switch(key, DEFAULT_MODE)
    if value not in MODES:
        return Switch(key, ENFORCE, error=f"{BLOCK}.{key} must be one of {list(MODES)}")
    return Switch(key, value)


def load_switch(key: str, path: str | None = None) -> Switch:
    """Read the switch from the seat's config. Never raises into the caller's
    act: a file that is not there is the default, one that is there and cannot
    be read or parsed enforces."""
    target = path or live_path()
    try:
        with open(target, encoding="utf-8") as fh:
            raw = fh.read()
    except (FileNotFoundError, NotADirectoryError):
        return parse_switch(None, key)
    except (OSError, ValueError) as exc:
        return Switch(key, ENFORCE, error=f"customer.yaml not readable: {exc.__class__.__name__}")
    try:
        import yaml

        data = yaml.safe_load(raw)
    except Exception as exc:  # noqa: BLE001 - an unparseable config enforces, never raises into a write
        return Switch(key, ENFORCE, error=f"customer.yaml not parseable: {exc.__class__.__name__}")
    return parse_switch(data, key)


__all__ = [
    "BLOCK",
    "CONFLICT_FENCE",
    "DEFAULT_MODE",
    "DEFAULT_PATH",
    "ENFORCE",
    "FIXED_WORDING",
    "KEYS",
    "LIBRARY_EDIT_NOTICE",
    "LIBRARY_READBACK",
    "LIBRARY_SETUP",
    "MODES",
    "OBSERVE",
    "PATH_ENVS",
    "POST_RECORD_DERIVATION",
    "PRINTED_SOURCE_CHECK",
    "STATUS_FROM_EVENTS",
    "Switch",
    "WORDING_RULES",
    "live_path",
    "load_switch",
    "parse_switch",
]
