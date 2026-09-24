"""Build + write the verification tracker's pre-rendered dispatch envelope.

WS-RENDER sibling (module-size ratchet). The internal escalations the chase
raises — hold surfaces, ceiling hand-offs, the config-missing note, and the
degraded chase-due note — are rendered here from the pre_run's own plans into
a consume-once envelope the overlay dispatches OUT OF TURN through the full
gate, writing the ledger appends post-dispatch. The model composes nothing.

THE DEGRADED-CHASE THROTTLE. While ``settings.return_link`` is unauthored
(the live state on every seat — build fork 2), a due client chase cannot
render and degrades to ONE seat-level surface line naming the held matters
and their count (``render._held_chase_line``). Like the config-missing surface
(#1899), it is remembered under a seat-level sentinel and re-fires on the
refire window, never daily, never silent, and never a ``chased`` row (no
client was nudged; the ledger stays honest). The sentinel is keyed on the SET
of held matters (``return_link_key``), so a changed set fires at once.

Failure direction: any fault here degrades to "no envelope written" — the
wake fires undecorated, ``dispatch_expected`` stays absent, and SKILL.md's
plans-without-dispatch_expected branch has the turn send the one-line failure
note. This skill's OBSERVERS are the terminal-state reconcile (the wake row
with no terminal outcome) and the ledger's own re-fire property — NOT the
heartbeat's degraded pager, which reads ``digest_degraded`` bases this
pre_run never stamps. Nothing may suppress or delay a wake.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_NAME = "client-verification-tracker"

#: Seat-level sentinel for the unauthored-return-link surface. Same shape as
#: the pre_run's config sentinel: fire once, re-fire on the refire window.
RETURN_LINK_SOURCE_ID = "__return_link__"
_RETURN_LINK_LABEL = "chase-return-link-missing"

_MAX_DISPATCHES = 10
_MAX_APPENDS_PER_DISPATCH = 200


def _load_sibling(filename: str, module_name: str):
    candidates = [Path(__file__).resolve().parent]
    for base in ("/opt/data/skills", "/app/skills"):
        candidates.append(Path(base) / SKILL_NAME)
    for cand in candidates:
        module_path = cand / filename
        if module_path.is_file():
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module
    return None


def _load_yaml(customer_yaml_path: str | None) -> dict:
    path = customer_yaml_path or os.environ.get("SMD_CUSTOMER_YAML_PATH")
    if not path:
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except Exception:  # noqa: BLE001 — unreadable config = unauthored routing
        return {}
    return data if isinstance(data, dict) else {}


def _write_envelope(payload: dict) -> bool:
    try:
        directory = Path(os.environ.get("HERMES_HOME") or "/opt/data") / ".smd" / "pre_run"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = directory / ("." + SKILL_NAME + ".dispatch.json.tmp")
        tmp.unlink(missing_ok=True)
        handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, directory / (SKILL_NAME + ".dispatch.json"))
        return True
    except Exception as exc:  # noqa: BLE001 — never change the wake
        sys.stderr.write("[pre_run] dispatch envelope write failed (" + str(exc) + ")\n")
        return False


def _numbers_by_matter(items) -> dict[str, tuple[str | None, str | None]]:
    out: dict[str, tuple[str | None, str | None]] = {}
    for item in items:
        number = getattr(item, "matter_number", None)
        absent = getattr(item, "matter_number_absent", None)
        out.setdefault(item.matter_id, (number, absent))
    return out


def _held_chases(plans, numbers: dict) -> dict:
    """The due client chases this run holds back while ``return_link`` is
    unauthored: how many, which matters, and their resolved numbers.

    ``count`` counts every chase plan, so a chase whose matter number did not
    resolve is still counted; it is only left out of ``numbers`` (the rendered
    list names what resolved and the count tells the reader there is more).
    Numbers come off the plan's own code-projected stamp, else the pulled
    items map, the same resolution every other entry uses, and they reach the
    provenance handoff through the plans in the wake payload
    (``handoff_writer._records``: each chase plan carries ``matter_number``
    beside ``next_chase_due``), so the send gate reads them as read, not
    composed."""
    count = 0
    matter_ids: set[str] = set()
    resolved: set[str] = set()
    for plan in plans:
        if plan.action != "chase":
            continue
        count += 1
        matter_ids.add(plan.matter_id)
        number = getattr(plan, "matter_number", None) or numbers.get(plan.matter_id, (None, None))[0]
        if isinstance(number, str) and number:
            resolved.add(number)
    return {"count": count, "matter_ids": sorted(matter_ids), "numbers": sorted(resolved)}


def return_link_key(ledger, held_matter_ids) -> str:
    """The throttle sentinel's ledger key, keyed on the SET of held matters.

    Before 2026-09-24 the key was one constant, so a chase that became due
    inside the refire window of an earlier, different set was silently
    absorbed by that set's raise. Keying on the set means a changed set is a
    new item that fires at once, while an unchanged set still waits out the
    refire window. Matter ids, not numbers: a number lookup that fails one
    morning must not look like a new set. The label is ignored by
    ``item_key`` (ss #2151), so the set rides in the source-id slot. Each
    set's attempts count on their own key; an old set's key is simply never
    consulted again, which costs nothing (the sentinel has no ceiling and
    writes no ``chased`` row)."""
    source_id = RETURN_LINK_SOURCE_ID + ":" + ",".join(sorted(held_matter_ids))
    return ledger.item_key("", source_id, _RETURN_LINK_LABEL, "")


def _return_link_entry(held: dict, ledger, states: dict, today, refire_days: int) -> dict | None:
    """The one seat-level line every due chase collapses to while
    ``return_link`` is unauthored, or None when there is no chase or the set
    already fired inside its refire window (never daily, never silent: #1899).
    A full Shape B render needs the authored link AND a signer lookup, and
    neither exists yet."""
    if not held["count"]:
        return None
    key = return_link_key(ledger, held["matter_ids"])
    state = states.get(key)
    if not ledger.should_fire(state, today, refire_days=refire_days, ack_snooze_days=refire_days):
        return None
    return {
        "matter_id": "",
        "action": "chase",
        "attempt": ledger.next_attempt(state),
        "ceiling": None,
        "reason": "return_link_unauthored",
        "item_key": key,
        "matter_number": None,
        "matter_number_absent": None,
        "held_matter_numbers": held["numbers"],
        "held_count": held["count"],
        "event": "fired",
    }


def write_failure_note_envelope(
    *,
    reason: str,
    customer_yaml_path: str | None = None,
) -> dict:
    """Write an envelope carrying ONLY the authored one-line failure note.

    WHY THIS EXISTS (2026-09-02, pilot-smokeball). When the Smokeball
    credential expired, ``build_and_write`` degraded to "no envelope" exactly
    as designed, and the two things meant to carry the miss both failed: the
    SKILL.md failure-note instruction is a sentence the model may or may not
    follow, and it did not -- it composed a verification alert instead and sent
    it; and the ``no_send_attempted`` pager cannot fire on a run that DID
    send. An instruction to the model is not a control. So the note is now
    RENDERED and dispatched by the gate on the same out-of-turn path as a real
    digest, and the model composes nothing either way.

    Deliberately connector-free: recipients come from the authored
    ``escalation.red_flag_recipients`` (else ``case_alert_routing.
    fallback_recipients``), which is the same central-triage leg routing.py
    resolves without touching staff data. A run that cannot read matters can
    still read its own customer.yaml.

    Returns {} and writes nothing when there is nobody authored to tell, or
    when render.py itself will not load. Both are honest fail-closed floors:
    the first has no delivery address, the second cannot produce the authored
    text and must NOT invent a substitute. In both cases the EMITTED_WAKE row
    pre_run now always writes is what makes the slot visible.
    """
    try:
        render = _load_sibling("render.py", "cvt_render")
        if render is None:
            return {}
        customer_yaml = _load_yaml(customer_yaml_path)
        esc = customer_yaml.get("escalation") or {}
        if not isinstance(esc, dict):
            return {}
        routing_block = esc.get("case_alert_routing") or {}
        recipients = [
            str(r).strip() for r in (esc.get("red_flag_recipients") or []) if isinstance(r, str) and str(r).strip()
        ]
        leg = "central"
        if not recipients and isinstance(routing_block, dict):
            recipients = [
                str(r).strip()
                for r in (routing_block.get("fallback_recipients") or [])
                if isinstance(r, str) and str(r).strip()
            ]
            leg = "fallback"
        if not recipients:
            return {}

        body = render.FAILURE_NOTE
        envelope = {
            "skill": SKILL_NAME,
            "render_mode": "slot-templated",
            "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "dispatches": [
                {
                    "recipients": recipients,
                    "cc": [],
                    "routing_leg": leg,
                    "subject": render.FAILURE_NOTE_SUBJECT,
                    "full_body": body,
                    # No degraded rung below a one-line note: skeleton IS the
                    # note. The overlay's full -> skeleton ladder therefore
                    # cannot turn this into something shorter and vaguer.
                    "skeleton_body": body,
                    "body_sha256_full": render.canonical_body_sha256(body),
                    "body_sha256_skeleton": render.canonical_body_sha256(body),
                    # Nothing was raised, so nothing is appended. A `fired`
                    # append here would record an escalation that never
                    # happened.
                    "appends": [],
                }
            ],
            "unroutable": [],
            "memo_matters": [],
            "in_turn": [],
            "failure_note_reason": reason,
        }
        if not _write_envelope(envelope):
            return write_failure_note_envelope(reason="envelope_write_failed", customer_yaml_path=customer_yaml_path)
        return {
            "render_mode": "slot-templated",
            "dispatch_expected": True,
            "dispatch_count": 1,
            "dispatch_variant": "failure_note",
            "failure_note_reason": reason,
            "routing_legs": {leg: 1},
        }
    except Exception as exc:  # noqa: BLE001 — the envelope is optional; the wake is not
        sys.stderr.write("[pre_run] failure-note envelope write failed (" + str(exc) + ")\n")
        return {}


def build_and_write(
    *,
    plans,
    items,
    ledger,
    ledger_events,
    today,
    refire_days: int,
    ceiling: int | None,
    customer_yaml_path: str | None = None,
    staff_pull=None,
) -> dict:
    """Render + write the envelope; return the EMITTED_WAKE metadata additions
    ({} on any failure — the wake proceeds undecorated).

    ``staff_pull`` is the test seam; production uses the vendored routing
    module's connector-venv pull — the SAME pull the escalator runs, so a
    pilot authored ``mode: matter_staff`` routes CVT alerts to the matter's
    responsible attorney instead of dumping every alert on the fallback leg
    (the WS-RENDER review's finding 2)."""
    try:
        render = _load_sibling("render.py", "cvt_render")
        routing = _load_sibling("routing.py", "cvt_routing")
        if render is None or routing is None:
            return write_failure_note_envelope(
                reason="sibling_module_unavailable", customer_yaml_path=customer_yaml_path
            )
        if staff_pull is None:
            staff_pull = routing.pull_matter_staff
        customer_yaml = _load_yaml(customer_yaml_path)
        states = ledger.derive_state(ledger_events)
        numbers = _numbers_by_matter(items)
        held = _held_chases(plans, numbers)

        # Enrich plans into render entries. Chase plans collapse into ONE
        # seat-level degraded line (or nothing, inside the refire window), so
        # they fall to the unknown-action branch here and are added below.
        entries: list[dict] = []
        for plan in plans:
            base = {
                "matter_id": plan.matter_id,
                "action": plan.action,
                "attempt": plan.attempt,
                "ceiling": ceiling,
                "reason": getattr(plan, "reason", ""),
                "item_key": plan.item_key,
            }
            # The plan carries the pull's code-projected number (decide()'s
            # stamp); the items map is the fallback for older plan shapes.
            number = getattr(plan, "matter_number", None)
            absent = getattr(plan, "matter_number_absent", None)
            if number is None and absent is None:
                number, absent = numbers.get(plan.matter_id, (None, None))
            base["matter_number"] = number
            base["matter_number_absent"] = absent
            if plan.action == "surface_hold":
                base["event"] = "fired"
            elif plan.action == "handoff":
                base["event"] = "handed_off"
            elif plan.action == "surface_config_missing":
                base["event"] = "fired"
            else:
                continue  # chase (collapsed below) or unknown: renders nothing here
            entries.append(base)
        link_entry = _return_link_entry(held, ledger, states, today, refire_days)
        if link_entry:
            entries.append(link_entry)

        if not entries:
            # Genuinely nothing to say: the plans rendered fine and produced no
            # entry. NOT a failure, so no failure note -- paging "the run
            # failed" on a quiet day is how alerts get ignored. This is the
            # branch a clean empty run actually takes on this skill.
            return {}

        # Routing: seat-level entries (matter_id "") route like an unstaffed
        # matter — central under central mode, fallback under matter_staff.
        # Staffed matters resolve through the SAME connector-venv staff pull
        # the escalator uses (vendored routing.py).
        matter_ids = []
        for entry in entries:
            if entry["matter_id"] not in matter_ids:
                matter_ids.append(entry["matter_id"])
        esc = customer_yaml.get("escalation") if isinstance(customer_yaml.get("escalation"), dict) else {}
        routing_block = esc.get("case_alert_routing")
        mode = routing_block.get("mode") if isinstance(routing_block, dict) else None
        matter_staff: dict[str, dict] = {}
        if mode == "matter_staff":
            matter_staff = staff_pull(matter_ids, routing.staff_lookup_budget(customer_yaml))
        result = routing.resolve_case_alert_routing(customer_yaml, matter_staff, matter_ids)

        by_recipients: dict[tuple, list[dict]] = {}
        unroutable_ids = set(result.unroutable)
        for entry in entries:
            routed = result.routed.get(entry["matter_id"])
            if routed is None:
                continue  # fail-closed floor; listed unroutable below
            by_recipients.setdefault((routed.emails, routed.routing_leg), []).append(entry)

        today_iso = today.isoformat()
        dispatches: list[dict] = []
        wake_hashes: list[dict] = []
        wake_items: list[dict] = []
        legs: dict[str, int] = {}
        overflow_matters: set[str] = set()
        for (emails, leg), group in sorted(by_recipients.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            if len(dispatches) >= _MAX_DISPATCHES:
                # Never a silent drop: an over-cap group's matters land in the
                # unroutable + memo lists so a person learns the alert did not
                # go (the review's finding 8).
                overflow_matters |= {e["matter_id"] for e in group if e["matter_id"]}
                continue
            subject, full_body = render.render_alert(group, today_iso=today_iso)
            skeleton_body = render.render_skeleton(len(group))
            appends = [
                {
                    "item_key": entry["item_key"],
                    "matter_id": entry["matter_id"] or None,
                    "event": entry["event"],
                    "attempt": int(entry["attempt"] or 1),
                    "token": None,
                }
                for entry in group[:_MAX_APPENDS_PER_DISPATCH]
            ]
            dispatches.append(
                {
                    "recipients": list(emails),
                    "cc": [],
                    "routing_leg": leg,
                    "subject": subject,
                    "full_body": full_body,
                    "skeleton_body": skeleton_body,
                    "body_sha256_full": render.canonical_body_sha256(full_body),
                    "body_sha256_skeleton": render.canonical_body_sha256(skeleton_body),
                    "appends": appends,
                }
            )
            wake_hashes.append(
                {
                    "body_sha256_full": dispatches[-1]["body_sha256_full"],
                    "body_sha256_skeleton": dispatches[-1]["body_sha256_skeleton"],
                }
            )
            legs[leg] = legs.get(leg, 0) + 1
            for entry in group:
                wake_items.append({"item_key": entry["item_key"], "ack_code": None})

        # The memo duty covers fallback-routed, floor, and over-cap matters.
        # The unknown-matter sentinel and the seat-level "" id are EXCLUDED:
        # neither names a real matter a person could open (the review's
        # finding 5).
        undelivered = (unroutable_ids | overflow_matters) - {"", routing.UNKNOWN_MATTER}
        memo_matters = sorted(
            {
                entry["matter_id"]
                for entry in entries
                if entry["matter_id"]
                and entry["matter_id"] != routing.UNKNOWN_MATTER
                and (
                    entry["matter_id"] in undelivered
                    or (
                        result.routed.get(entry["matter_id"]) is not None
                        and result.routed[entry["matter_id"]].routing_leg == routing.LEG_FALLBACK
                    )
                )
            }
        )
        if not dispatches and not memo_matters:
            # Entries existed but nothing routed and nothing needs a memo.
            # Also not a failure -- the same no-alarm-on-success reasoning as
            # the empty-entries branch above.
            return {}
        envelope = {
            "skill": SKILL_NAME,
            "render_mode": "slot-templated",
            "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "dispatches": dispatches,
            "unroutable": [
                {
                    "matter_id": m,
                    "matter_number": numbers.get(m, (None, None))[0],
                    "reason": ("dispatch_cap_exceeded" if m in overflow_matters else "no_usable_recipient"),
                }
                for m in sorted(undelivered)
            ],
            "memo_matters": memo_matters,
            # The failure note is DECLARED but not slot-enforced for this
            # skill (enforce: false): the turn's Shape A approve-and-send is a
            # legitimate in-turn send whose template pre_run cannot pre-key
            # (signer resolution is a turn judgment), and a gate armed with
            # only the failure note would block it.
            "in_turn": [{"name": "failure_note", "template": render.FAILURE_NOTE, "slots": {}}],
            "in_turn_enforce": False,
        }
        if not _write_envelope(envelope):
            return write_failure_note_envelope(reason="envelope_write_failed", customer_yaml_path=customer_yaml_path)
        return {
            "render_mode": "slot-templated",
            "body_sha256": wake_hashes,
            "items": wake_items,
            "dispatch_expected": True,
            "dispatch_count": len(dispatches),
            "routing_legs": legs,
            **({"chase_degraded_return_link_unauthored": held["count"]} if held["count"] else {}),
        }
    except Exception as exc:  # noqa: BLE001 — the envelope is optional; the wake is not
        sys.stderr.write("[pre_run] dispatch envelope build failed (" + str(exc) + ")\n")
        # The 2026-09-02 case: the turn wakes with work it cannot dispatch and,
        # left undecorated, composes it. Give it a rendered note instead.
        return write_failure_note_envelope(reason="envelope_build_failed", customer_yaml_path=customer_yaml_path)
