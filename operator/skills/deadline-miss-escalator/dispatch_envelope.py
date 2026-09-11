"""Build + write the escalator's pre-rendered dispatch envelope (WS-RENDER).

The out-of-turn dispatch seam: ``pre_run.py`` renders EVERYTHING — recipients,
subject, full body, skeleton body, the ledger appends a successful send earns —
into a consume-once file next to the provenance handoff:

    ``$HERMES_HOME/.smd/pre_run/deadline-miss-escalator.dispatch.json``

The overlay's ``shared/prerendered_dispatch.py`` reads it at ``pre_llm_call``,
dispatches each entry ``templated=True`` through the FULL gate (ceiling, taint,
content floor, fabrication scan, identifier gate), writes the ``fired`` appends
post-dispatch, and injects one context note. The model composes nothing.

Same writer discipline as the provenance handoff (0600, atomic rename, .smd
fence): the envelope is provenance-adjacent state the model must not author.

FAILURE DIRECTION. Every failure here — sibling missing, yaml unreadable,
staff pull down, write fault — degrades to "no envelope written": the wake
still fires, ``dispatch_expected`` stays False, and the SKILL.md failure-note
instruction plus the heartbeat ``no_send_attempted`` pager carry the miss.
Nothing here may suppress or delay a wake.

Sibling module, path-loaded by ``pre_run.py`` (module-size ratchet:
``tests/operator-module-size.test.ts``); stdlib + PyYAML-optional, same import
set as ``pre_run``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_NAME = "deadline-miss-escalator"

#: Bounds the envelope so a pathological universe cannot write an unbounded
#: file for the overlay to trust. Recipient groups past the cap are NOT
#: dropped silently: their matters land in the unroutable + memo lists so a
#: person learns delivery did not happen.
_MAX_DISPATCHES = 10
_MAX_APPENDS_PER_DISPATCH = 200


def _load_sibling(filename: str, module_name: str):
    """Path-load a sibling module (the vendored-ledger loader pattern; the
    scheduler may stage pre_run.py alone, so /opt/data/skills is the seat
    fallback)."""
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


# The staff pull + its authored budget live in the vendored ``routing.py``
# (one pull, one resolution, shared with client-verification-tracker — the
# WS-RENDER review's finding 2).


# ---------------------------------------------------------------------------
# Digest splitting — one dispatch entry PER RECIPIENT SET (case-alert-routing
# "one alert per recipient per run"). Counts stay list lengths by construction:
# the split filters the projection's own items/groups and re-counts by len().
# ---------------------------------------------------------------------------


def _filter_grouped(band: dict, matter_ids: set[str]) -> dict | None:
    matters = [g for g in (band.get("matters") or []) if g.get("matter_id") in matter_ids]
    if not matters:
        return None
    return {
        "total": sum(int(g.get("count") or 0) for g in matters),
        "matter_count": len(matters),
        "matters": matters,
    }


def split_digest(digest: dict, matter_ids: set[str], today_iso: str) -> dict:
    """The sub-digest for one recipient set: only ``matter_ids``'s items, every
    count recomputed as a list length, subject counting ONLY this sub-digest's
    needs-you band (Law 11)."""
    out: dict = {}
    needs_you = [i for i in (digest.get("needs_you") or []) if i.get("matter_id") in matter_ids]
    out["subject"] = f"[Deadlines] {len(needs_you)} need you, {today_iso}"
    out["needs_you"] = needs_you
    admin = digest.get("admin_confirms")
    if isinstance(admin, dict):
        filtered = _filter_grouped(admin, matter_ids)
        if filtered:
            out["admin_confirms"] = filtered
    elsewhere = digest.get("under_active_escalation_elsewhere")
    if isinstance(elsewhere, dict):
        filtered = _filter_grouped(elsewhere, matter_ids)
        if filtered:
            out["under_active_escalation_elsewhere"] = filtered
    clearance = [i for i in (digest.get("awaiting_clearance") or []) if i.get("matter_id") in matter_ids]
    if clearance:
        out["awaiting_clearance"] = clearance
    blanket = [i for i in (digest.get("blanket_ack_only") or []) if i.get("matter_id") in matter_ids]
    if blanket:
        out["blanket_ack_only"] = blanket
    probe = digest.get("probe_artifacts")
    if isinstance(probe, dict):
        # Seat-level census, attached to every dispatch: ss#2403 wants the
        # leftover-probe fact loud daily, whoever the reader is.
        out["probe_artifacts"] = probe
    return out


def _firing_items(sub_digest: dict) -> list[dict]:
    """The items a successful send RAISES: needs-you + admin + blanket. The
    elsewhere and clearance bands are informational and append nothing."""
    items = list(sub_digest.get("needs_you") or [])
    admin = sub_digest.get("admin_confirms") or {}
    for group in admin.get("matters") or []:
        items.extend(group.get("items") or [])
    items.extend(sub_digest.get("blanket_ack_only") or [])
    return items


def legacy_rekey_count(deadlines, states, ledger) -> int:
    """How many now-sentinel items carry raise/ack history under their LEGACY
    key (the pre-fix ``_MATTER_ID_KEYS`` bare-``id`` fallback made the item's
    own id its matter id). Non-zero -> the digest carries the authored one-run
    identity notice; self-extinguishing once legacy state stops matching."""
    count = 0
    for d in deadlines:
        if d.matter_id != "unknown-matter" or not d.task_id:
            continue
        legacy_key = ledger.item_key(d.task_id, d.task_id, d.label, d.authored_date)
        state = states.get(legacy_key)
        if state is not None and (state.attempts > 0 or state.acked):
            count += 1
    return count


# ---------------------------------------------------------------------------
# Envelope assembly + write
# ---------------------------------------------------------------------------


def _write_envelope(payload: dict) -> bool:
    """Atomic 0600 write beside the provenance handoff. False on any failure."""
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
    follow, and it did not -- it composed a bare digest body instead and sent
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
        render = _load_sibling("render.py", "escalator_render")
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
            "render_mode": "templated",
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
            return {}
        return {
            "render_mode": "templated",
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
    digest: dict,
    deadlines,
    states: dict,
    ledger,
    today,
    ack_snooze_days: int,
    customer_yaml_path: str | None = None,
    staff_pull=None,
) -> dict:
    """Render everything, write the envelope, and return the EMITTED_WAKE
    metadata additions.

    {} on any failure: the wake proceeds undecorated, ``dispatch_expected``
    stays absent, and SKILL.md's plans-without-dispatch_expected branch has
    the turn send the one-line failure note (the heartbeat's no-send pager
    backstops). An envelope with ZERO dispatches is still written when it
    carries memo duties (fallback/floor matters), so the dispatcher's context
    note delivers them — the wake line itself carries no unroutable list.

    ``staff_pull`` is the test seam; production uses the vendored routing
    module's connector-venv pull (shared with client-verification-tracker).
    """
    try:
        render = _load_sibling("render.py", "escalator_render")
        routing = _load_sibling("routing.py", "escalator_routing")
        if render is None or routing is None:
            # routing.py missing still permits a note (it needs no routing);
            # render.py missing does not, and write_failure_note_envelope
            # returns {} for that case rather than inventing the text.
            return write_failure_note_envelope(
                reason="sibling_module_unavailable", customer_yaml_path=customer_yaml_path
            )
        if staff_pull is None:
            staff_pull = routing.pull_matter_staff
        customer_yaml = _load_yaml(customer_yaml_path)
        rekey = legacy_rekey_count(deadlines, states, ledger)

        # Every matter the digest names, routed once.
        matter_ids: list[str] = []
        for item in _firing_items(digest):
            if item.get("matter_id") not in matter_ids:
                matter_ids.append(item["matter_id"])
        for band in ("under_active_escalation_elsewhere", "admin_confirms"):
            grouped = digest.get(band) or {}
            for group in grouped.get("matters") or []:
                if group.get("matter_id") not in matter_ids:
                    matter_ids.append(group["matter_id"])
        for item in digest.get("awaiting_clearance") or []:
            if item.get("matter_id") not in matter_ids:
                matter_ids.append(item["matter_id"])

        esc = customer_yaml.get("escalation") if isinstance(customer_yaml.get("escalation"), dict) else {}
        routing_block = esc.get("case_alert_routing")
        mode = routing_block.get("mode") if isinstance(routing_block, dict) else None
        matter_staff: dict[str, dict] = {}
        if mode == "matter_staff":
            matter_staff = staff_pull(matter_ids, routing.staff_lookup_budget(customer_yaml))
        result = routing.resolve_case_alert_routing(customer_yaml, matter_staff, matter_ids)

        # Group matters by resolved recipient set -> one dispatch per set.
        by_recipients: dict[tuple, dict] = {}
        for matter_id, routed in result.routed.items():
            key = (routed.emails, routed.routing_leg)
            group = by_recipients.setdefault(key, {"matter_ids": set()})
            group["matter_ids"].add(matter_id)

        today_iso = today.isoformat()
        dispatches: list[dict] = []
        wake_hashes: list[dict] = []
        wake_items: list[dict] = []
        legs: dict[str, int] = {}
        overflow_matters: set[str] = set()
        for (emails, leg), group in sorted(by_recipients.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            if len(dispatches) >= _MAX_DISPATCHES:
                # Never a silent drop: an over-cap recipient group's matters
                # land in the unroutable + memo lists so a person learns the
                # alert did not go (the review's finding 8).
                overflow_matters |= group["matter_ids"]
                continue
            sub = split_digest(digest, group["matter_ids"], today_iso)
            firing = _firing_items(sub)
            if not firing:
                # An alert with nothing in the needs-a-person universe is not
                # sent at all (output-format rule 8 generalized: a recipient
                # set whose only content is informational bands gets nothing).
                continue
            full_body = render.render_digest(sub, ack_snooze_days=ack_snooze_days, rekey_count=rekey)
            skeleton_body = render.render_skeleton(sub)
            appends = []
            for item in firing[:_MAX_APPENDS_PER_DISPATCH]:
                key_hex = ledger.item_key(
                    item.get("matter_id"),
                    item.get("task_id"),
                    item.get("label"),
                    item.get("authored_date"),
                )
                appends.append(
                    {
                        "item_key": key_hex,
                        "matter_id": item.get("matter_id"),
                        "event": "fired",
                        "attempt": ledger.next_attempt(states.get(key_hex)),
                        "token": item.get("ack_code"),
                    }
                )
                wake_items.append({"item_key": key_hex, "ack_code": item.get("ack_code")})
            dispatches.append(
                {
                    "recipients": list(emails),
                    "cc": [],
                    "routing_leg": leg,
                    "subject": sub["subject"],
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

        # The turn's residual memo duty: fallback-delivered matters, the
        # fail-closed floor, and any over-cap overflow all flag the matter in
        # place (routing.md steps 5-6). The unknown-matter sentinel is
        # EXCLUDED from both lists: it names no real matter, so there is
        # nothing to memo and nothing a person could open (the review's
        # finding 5); its items still ride the central/fallback dispatch.
        number_by_matter = {}
        for item in _firing_items(digest):
            number_by_matter.setdefault(item.get("matter_id"), item.get("matter_number"))
        undelivered = set(result.unroutable) | overflow_matters
        memo_matters = sorted(
            ({m for m, routed in result.routed.items() if routed.routing_leg == routing.LEG_FALLBACK} | undelivered)
            - {routing.UNKNOWN_MATTER}
        )
        unroutable = [
            {
                "matter_id": m,
                "matter_number": number_by_matter.get(m),
                "reason": "dispatch_cap_exceeded" if m in overflow_matters else "no_usable_staff",
            }
            for m in sorted(undelivered)
            if m != routing.UNKNOWN_MATTER
        ]

        envelope = {
            "skill": SKILL_NAME,
            "render_mode": "templated",
            "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "dispatches": dispatches,
            "unroutable": unroutable,
            "memo_matters": memo_matters,
            "in_turn": [{"name": "failure_note", "template": render.FAILURE_NOTE, "slots": {}}],
        }
        if not dispatches and not memo_matters and not unroutable:
            # Genuinely nothing to say: no envelope, no dispatch_expected, and
            # NO failure note either. This is the one empty-handed path that is
            # not a failure -- the digest rendered fine and had no recipients
            # to reach. Sending "the run failed" here would page on success.
            return {}
        if not _write_envelope(envelope):
            return write_failure_note_envelope(reason="envelope_write_failed", customer_yaml_path=customer_yaml_path)
        return {
            "render_mode": "templated",
            "body_sha256": wake_hashes,
            "items": wake_items,
            "dispatch_expected": True,
            "dispatch_count": len(dispatches),
            "routing_legs": legs,
            **({"rekey_notice_items": rekey} if rekey else {}),
        }
    except Exception as exc:  # noqa: BLE001 — the envelope is optional; the wake is not
        sys.stderr.write("[pre_run] dispatch envelope build failed (" + str(exc) + ")\n")
        # A build fault is exactly the 2026-09-02 case: the turn wakes with a
        # digest it cannot dispatch, and left undecorated it composes one. Give
        # it a rendered note to deliver instead of a gap to fill.
        return write_failure_note_envelope(reason="envelope_build_failed", customer_yaml_path=customer_yaml_path)
