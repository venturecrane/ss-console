#!/usr/bin/env python3
"""Boot-smoke probe: the matter-mixing read fence is installed and DISCRIMINATES
(ss#2167).

Run at boot with the Hermes venv python, where the overlay is pip-installed so
``shared.matter_gate`` is importable.

WHY THIS EXISTS, and why it asserts both directions
---------------------------------------------------
The fence refuses a session that already holds one matter's substance from
reading a second matter's. It is the control that stops a draft containing two
clients' facts from ever being COMPOSED — every other matter control on the seat
fires when a send is attempted, by which point that draft exists and is sitting
in a paralegal's queue, and the firm finding it there is the event the engagement
does not survive whether or not it was ever sent.

The capture path it depends on (``matter_binding.record_from_read``) swallows
every exception by contract, because a hook must never raise into the tool path.
That is correct and it means a broken fence is INDISTINGUISHABLE FROM A CLEAN
FLEET: no error, no alert, every send simply stops being checked. So the fence
needs a probe that runs the real code and fails the boot when it stops working.

Two assertions, and the second matters as much as the first:

  1. A second matter's memo read is REFUSED.
  2. Re-reading the SAME matter is ALLOWED.

A probe asserting only (1) would pass against a fence that refused every content
read — which is not a safe fence, it is a bricked Operator that the firm would
switch off within a day. Asserting only (2) would pass against a fence that was
entirely open. Both, or this has measured nothing.

WHAT THIS PROBE DOES NOT COVER — the boundary, stated so nobody mistakes it
--------------------------------------------------------------------------
The fence lives at the MCP TOOL SEAM (``enforce.evaluate_tool_call``). It sees a
matter-content read only when that read arrives as a tool call. Anything that
reaches matter content by another route is outside it:

* a script or probe on the seat calling the connector client directly
  (``client.get()`` / ``download_file``) rather than through the tool layer;
* any future in-process path that fetches document or memo content without a
  tool call.

That is not a defect in the fence, and widening it to a process-level control is
not proposed — the AGENT acts through the tool layer, and the direct-client paths
that exist today are Captain-driven and read-only (``execute_code`` is
taint-gated, so the agent cannot open one). But "every matter-content read is
fenced" is a **tool-layer** claim, and writing it without that qualifier is how a
boundary gets forgotten and later mistaken for coverage.

Exit 0 when the fence behaves. Non-zero, loudly, otherwise.
"""

from __future__ import annotations

import sys

SESSION = "boot-smoke-matter-mixing"
MATTER_A = "aaaaaaaa-0000-0000-0000-boot0000000a"
MATTER_B = "bbbbbbbb-0000-0000-0000-boot0000000b"
MEMOS = "mcp_smokeball_get_memos_on_matter"


def fail(message: str) -> None:
    print(f"FAIL: matter-mixing fence: {message}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    try:
        from shared import matter_binding, matter_gate
    except Exception as exc:  # noqa: BLE001 - an import failure of any kind means the fence is absent; fail() reports it and stops the boot
        fail(f"cannot import the fence at all ({exc!r}) — the overlay pin predates ss#2167")

    mode = matter_gate.multi_matter_mode()
    if mode != "block":
        # Not a failure: `report` and `off` are authorable operator states. But it
        # must be VISIBLE in the boot log, because in either state a mixed draft
        # can be composed and nothing downstream will say so.
        print(f"SKIP: SMD_MULTI_MATTER_MODE={mode} — the read fence is open by configuration")
        return

    # Clean any residue so the probe measures itself, not a previous run.
    matter_binding.drop(SESSION)

    # The session reads matter A's memos.
    matter_binding.record_from_read(SESSION, "{}", tool_name=MEMOS, args={"matter_id": MATTER_A})

    held = matter_binding.membership_for(SESSION).content_read_matters()
    if MATTER_A not in held:
        fail(
            "the content read was not captured, so the fence has nothing to compare "
            "against and would never refuse anything (record_from_read is failing "
            "silently — its exception handler is by design, which is exactly why "
            "this probe exists)"
        )

    # 1. The second matter must be refused.
    refusal = matter_gate.content_read_refusal(SESSION, MEMOS, {"matter_id": MATTER_B})
    if refusal is None:
        fail(
            "a SECOND matter's memo read was permitted — a draft mixing two clients' "
            "matters can be composed on this seat"
        )

    # 2. The same matter must still be readable, or the Operator cannot work.
    same = matter_gate.content_read_refusal(SESSION, MEMOS, {"matter_id": MATTER_A})
    if same is not None:
        fail(
            "re-reading the SAME matter was refused — the fence is not discriminating, "
            f"it is refusing everything ({same})"
        )

    # 3. The WIRE spelling must reach the fence too.
    #
    # This assertion exists because the probe above could not have caught the
    # ss#2444 regression. Hermes v0.19 renamed MCP tools
    # ``mcp_<server>_<tool>`` -> ``mcp__<server>__<tool>``. The fence's tool set
    # is keyed on the legacy spelling, so on a v0.20 seat without the boundary
    # translation ``is_content_read()`` returns False and the fence silently
    # ISN'T THERE — no refusal, no error. Every assertion above uses the
    # canonical spelling directly, so all of them would have passed while the
    # real read path was unfenced. The instrument could not observe its own
    # layer.
    #
    # So drive the wire form through the same canonicalizer the fan-out uses.
    # If a future Hermes rename escapes that translation, this fails the boot
    # instead of leaving the fence quietly inert.
    matter_binding.drop(SESSION)
    try:
        from shared.mcp_tool_names import canonical_tool_name
    except Exception as exc:  # noqa: BLE001 - an import failure of any kind means the canonicalizer is absent; fail() reports it and stops the boot
        fail(f"cannot import the tool-name canonicalizer ({exc!r}) — overlay predates ss#2444")

    wire_memos = "mcp__smokeball__get_memos_on_matter"
    if not matter_binding.is_content_read(canonical_tool_name(wire_memos)):
        fail(
            f"the wire tool name {wire_memos} does not canonicalize into the content-read "
            "set — the fence is inert for every real read on this seat (ss#2444 class)"
        )

    matter_binding.record_from_read(
        SESSION, "{}", tool_name=canonical_tool_name(wire_memos), args={"matter_id": MATTER_A}
    )
    if matter_gate.content_read_refusal(SESSION, canonical_tool_name(wire_memos), {"matter_id": MATTER_B}) is None:
        fail(
            "a second matter read under the WIRE tool spelling was permitted — the fence "
            "does not cover the names this Hermes version actually sends"
        )

    matter_binding.drop(SESSION)
    print(
        "PASS: matter-mixing fence refuses a second matter, allows the same one, "
        "and covers the v0.19+ wire tool spelling"
    )


if __name__ == "__main__":
    main()
