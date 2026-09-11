"""Runtime-control conformance — the CI forcing function (wires-nothing subset).

The validator-style precedents in this tree prove a thing is well-SHAPED. This
proves the set of runtime safety controls is COMPLETE and DOCUMENTED, so a
control cannot ship silently absent or silently undocumented — the exact gap that
let `sticky_stop` (a complete, tested circuit breaker) sit with zero callers and
zero overlay references while every other test stayed green.

This is the deterministic, offline, CI-time tier. It does NOT prove a control
fires on a live turn: that is the negative-fire probe suite (harness Component 3,
built 2026-08-17 in ss#2387), which lives at operator/bin/control-probes.py with
its specs in operator/contracts/runtime-control-probes.yaml. What this tier adds
is the LINKAGE, so a status cannot rest on a probe name that resolves to nothing.
What it enforces against operator/contracts/runtime-controls.yaml:

  (a) completeness from the declared cross-repo surface — every safety-critical
      hook in overlay-hook-surface.json maps to >=1 registry entry (sees
      overlay-resident controls a single-repo grep cannot);
  (b) KNOWN_CONTROLS subset of the registry — the day-one acceptance that the
      harness actually catches the sticky_stop class;
  (c) well-formedness — enforced => live_probe + wired_via + probe_surface;
      unprobed/inert => owner + tracking + note;
  (d) tracking hygiene — references are well-formed and (once the referenced ADR
      is in-repo) actually resolve, so an inert control cannot point at a
      vanished work item;
  (e) probe linkage — an `enforced` row names a probe that EXISTS in the probe
      specs, every control is named by at least one probe, and every
      unprobed/inert row carries a dated risk review. A status resting on a
      string was the gap ss#2387 closed.

Run::

    cd operator && python3 -m pytest bin/tests/test_runtime_control_conformance.py -v
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

_OP = Path(__file__).resolve().parents[2]
_REGISTRY = _OP / "contracts" / "runtime-controls.yaml"
_PROBE_SPECS = _OP / "contracts" / "runtime-control-probes.yaml"
_HOOK_SURFACE = _OP / "contracts" / "overlay-hook-surface.json"
_ADR_DIR = _OP.parent / "docs" / "adr"

_VALID_STATUS = {"enforced", "unprobed", "inert"}
_VALID_CLASS = {"dispatch-guard", "emitter", "circuit-breaker"}
_VALID_PROBE_SURFACE = {"staging", "prod-boot"}
_TRACKING_RE = re.compile(r"^(ADR-\d{4}:B\d+|#\d+|https?://\S+)$")

# Day-one acceptance: the harness is worthless if its discovery silently misses
# the very class that motivated it. These MUST appear in the registry. If this
# list and the registry ever diverge, that is the bug (a real control dropped, or
# a stale expectation) — surfaced, never silent.
_KNOWN_CONTROLS = {
    "trust_ceiling",
    "audit_emission",
    "sticky_stop_cost_cap",
    "sticky_stop_tool_failure",
    "sticky_stop_refusal_cascade",
    "sticky_stop_time_budget",
}


def _registry() -> dict:
    return yaml.safe_load(_REGISTRY.read_text(encoding="utf-8")) or {}


def _controls() -> dict:
    return _registry().get("controls") or {}


def _probes() -> dict:
    return (yaml.safe_load(_PROBE_SPECS.read_text(encoding="utf-8")) or {}).get("probes") or {}


def _hook_surface() -> dict:
    return json.loads(_HOOK_SURFACE.read_text(encoding="utf-8"))


def _hook_of(wired_via: str) -> str:
    """`"<hook> / <plugin>"` -> `<hook>`."""
    return (wired_via or "").split("/", 1)[0].strip()


#: `wired_via` prefix for a control the agent reaches by CALLING A TOOL rather
#: than by a hook firing around one.
#:
#: The schema assumed every control was hook-wired, which was true until a
#: control existed that the agent invokes directly. A tool IS a wiring — it is
#: registered, classified in TOOL_ACTION_CLASS_MAP, and refusable — it is simply
#: not in the hook surface, and forcing it to name a hook would invent one.
#:
#: This is NOT an escape hatch from the hook cross-check. A hook-form value is
#: still validated against overlay-hook-surface.json, and a tool-form value gets
#: its own cross-check below: the named tool must actually be instructed
#: somewhere a shipped skill will read it. A control nothing tells the agent to
#: call is inert in exactly the way this registry exists to expose, and writing
#: `tool / anything` must not buy an exemption from proving otherwise.
_TOOL_PREFIX = "tool"


def _tool_of(wired_via: str) -> str:
    """`"tool / <tool_name>"` -> `<tool_name>`, else `""`."""
    parts = (wired_via or "").split("/", 1)
    if len(parts) != 2 or parts[0].strip() != _TOOL_PREFIX:
        return ""
    return parts[1].strip()


#: `wired_via` prefix for a control that fires as a mandatory stage of the
#: medchron runner's fixed DAG (ss#2614) — neither a hook nor an agent-called
#: tool. The remainder is the gate module's repo path.
_RUNNER_PREFIX = "runner"


def _runner_of(wired_via: str) -> str:
    """`"runner / <repo path>"` -> `<repo path>`, else `""`."""
    parts = (wired_via or "").split("/", 1)
    if len(parts) != 2 or parts[0].strip() != _RUNNER_PREFIX:
        return ""
    return parts[1].strip()


# --------------------------------------------------------------------------- #
# structure                                                                    #
# --------------------------------------------------------------------------- #


def test_registry_has_maintainer_and_controls() -> None:
    reg = _registry()
    assert reg.get("maintainer"), "registry must name an overall maintainer"
    assert _controls(), "registry has no controls"


def test_keys_match_control_ids() -> None:
    for key, spec in _controls().items():
        assert spec.get("control") == key, f"control map key {key!r} != entry's control id {spec.get('control')!r}"


def test_status_and_class_are_valid() -> None:
    for key, spec in _controls().items():
        assert spec.get("status") in _VALID_STATUS, f"{key}: invalid status {spec.get('status')!r}"
        assert spec.get("class") in _VALID_CLASS, f"{key}: invalid class {spec.get('class')!r}"
        assert spec.get("owner"), f"{key}: every control needs a named owner"


def test_well_formed_by_status() -> None:
    for key, spec in _controls().items():
        status = spec.get("status")
        if status == "enforced":
            assert spec.get("live_probe"), f"{key}: enforced => must name a live_probe"
            assert spec.get("wired_via"), f"{key}: enforced => must name wired_via"
            assert spec.get("probe_surface") in _VALID_PROBE_SURFACE, (
                f"{key}: enforced => probe_surface must be one of {_VALID_PROBE_SURFACE}"
            )
        else:  # unprobed | inert
            assert spec.get("tracking"), (
                f"{key}: {status} => must carry a `tracking` work item (no silent dead control)"
            )
            assert spec.get("note"), f"{key}: {status} => must carry a `note` explaining why"


# --------------------------------------------------------------------------- #
# probe linkage (ss#2387)                                                      #
# --------------------------------------------------------------------------- #


def test_enforced_rows_name_a_probe_that_exists() -> None:
    """`enforced` means a probe proves it fires. Before the probe suite existed,
    `live_probe` was a name with nothing behind it, which is how a status came to
    rest on a string. Now the name must resolve."""
    probes = _probes()
    for key, spec in _controls().items():
        if spec.get("status") != "enforced":
            continue
        name = spec.get("live_probe")
        assert name in probes, (
            f"{key}: live_probe {name!r} has no entry in runtime-control-probes.yaml. "
            "An enforced status must point at a probe that exists."
        )
        assert probes[name].get("control") == key, (
            f"{key}: probe {name!r} declares control {probes[name].get('control')!r}"
        )


def test_candidate_probes_resolve_and_are_not_claimed_as_live() -> None:
    """`candidate_probe` is deliberately a different field from `live_probe`:
    naming a probe must never be mistakable for having passed one."""
    probes = _probes()
    for key, spec in _controls().items():
        name = spec.get("candidate_probe")
        if not name:
            continue
        assert spec.get("status") != "enforced", f"{key}: an enforced row must use live_probe, not candidate_probe"
        assert name in probes, f"{key}: candidate_probe {name!r} has no entry in the probe specs"
        assert probes[name].get("control") == key, f"{key}: candidate probe names another control"


def test_every_control_is_named_by_at_least_one_probe() -> None:
    """A control nobody wrote a probe for is a control whose status nobody can
    ever challenge. New controls inherit the requirement automatically."""
    claimed = {spec.get("control") for spec in _probes().values()}
    missing = sorted(set(_controls()) - claimed)
    assert not missing, (
        f"control(s) {missing} have no probe in runtime-control-probes.yaml. Author one "
        "(a seat probe with no driver is honest and holds; silence is not)."
    )


def test_every_probe_names_a_real_control() -> None:
    controls = set(_controls())
    for name, spec in _probes().items():
        assert spec.get("control") in controls, (
            f"probe {name!r} speaks for control {spec.get('control')!r}, which is not in the registry"
        )


def test_unprobed_and_inert_rows_carry_a_dated_risk_review() -> None:
    """AC4 of ss#2387. An undated risk note is indistinguishable from one nobody
    has looked at since the day it was written."""
    for key, spec in _controls().items():
        if spec.get("status") == "enforced":
            continue
        review = spec.get("risk_review") or ""
        assert review, f"{key}: {spec.get('status')} => needs a dated risk_review"
        assert re.match(r"^\d{4}-\d{2}-\d{2}\b", review.strip()), (
            f"{key}: risk_review must open with an ISO date; got {review[:40]!r}"
        )


# --------------------------------------------------------------------------- #
# completeness                                                                 #
# --------------------------------------------------------------------------- #


def test_every_safety_critical_hook_is_covered() -> None:
    """Drive completeness from the DECLARED surface, not a one-repo grep: every
    safety-critical hook the overlay must register has >=1 registry control that
    claims to be wired via it. Catches an overlay-resident control nobody listed.
    """
    required = _hook_surface().get("requiredHooks") or {}
    safety_hooks = {h for h, meta in required.items() if meta.get("safetyCritical")}
    covered = {_hook_of(spec.get("wired_via", "")) for spec in _controls().values() if spec.get("wired_via")}
    missing = safety_hooks - covered
    assert not missing, (
        f"safety-critical hook(s) {sorted(missing)} are declared in "
        "overlay-hook-surface.json but no runtime-controls.yaml entry is wired via them. "
        "A governing hook with no registered control is exactly the inert-control gap."
    )


def test_known_controls_are_registered() -> None:
    declared = set(_controls())
    missing = _KNOWN_CONTROLS - declared
    assert not missing, (
        f"KNOWN_CONTROLS {sorted(missing)} are absent from the registry. The harness's "
        "discovery is silently incomplete — this is the day-one acceptance that it catches "
        "the sticky_stop class. Add the entry (or, if genuinely removed, update _KNOWN_CONTROLS)."
    )


def test_wired_via_hooks_exist_in_surface() -> None:
    required = set(_hook_surface().get("requiredHooks") or {})
    for key, spec in _controls().items():
        wired = spec.get("wired_via")
        if not wired or _tool_of(wired) or _runner_of(wired):
            continue
        hook = _hook_of(wired)
        assert hook in required, f"{key}: wired_via names hook {hook!r} which is not in overlay-hook-surface.json"


def test_runner_wired_controls_name_a_module_the_runner_ships() -> None:
    """ss#2614: a third wiring shape beside hooks and tools. `runner / <path>`
    means the control fires because it is a mandatory stage of the medchron
    runner's fixed DAG — no hook wraps it and no agent chooses to call it; the
    driver cannot reach delivery without it. The claim is checkable two ways:
    the named module exists in this tree, and the runner's DAG module names it
    (a gate module the DAG never binds would be inert by construction)."""
    dag_src = (_OP / "runners" / "medchron" / "medchron" / "dag.py").read_text(encoding="utf-8")
    stages_dir = _OP / "runners" / "medchron" / "medchron" / "stages"
    stage_srcs = "\n".join(p.read_text(encoding="utf-8") for p in stages_dir.glob("*.py"))
    audit_dir = _OP / "runners" / "medchron" / "medchron" / "audit"
    audit_srcs = "\n".join(p.read_text(encoding="utf-8") for p in audit_dir.glob("*.py"))
    for key, spec in _controls().items():
        path = _runner_of(spec.get("wired_via", ""))
        if not path:
            continue
        module = _OP / path.removeprefix("operator/")
        assert module.is_file(), f"{key}: runner-wired module {path!r} does not exist"
        # Two honest shapes of "the DAG reaches this gate": a stage imports the
        # gate by name (identity.py -> cross_client), or the gate delegates to
        # a stage/audit seam whose module the DAG binds (extractive -> strip,
        # provenance -> coverage, claim_audit -> audit.coverage). Either way
        # there is a text path from dag.py to the module; a gate with neither
        # is inert by construction.
        stem = module.stem
        src = module.read_text(encoding="utf-8")
        seams = re.findall(r"from \.\.(?:stages|audit) import ([a-z_]+)", src)
        seam_bound = any(seam in dag_src for seam in seams)
        named_by_stage = stem in dag_src or stem in stage_srcs or stem in audit_srcs
        assert seam_bound or named_by_stage, (
            f"{key}: {stem!r} is neither named by a DAG/stage module nor delegating "
            f"to a DAG-bound seam (imports: {seams})"
        )


def test_tool_wired_controls_are_actually_instructed() -> None:
    """A tool-wired control must be something a shipped skill tells the agent to call.

    The counterpart of the hook cross-check, and the reason `tool /` is not an
    exemption. A hook fires whether or not anyone asked; a tool runs only if
    something in the agent's context says to run it. So a tool-wired control
    whose name appears in no shipped skill or discipline is inert by
    construction — exactly the state this registry exists to expose — and it
    would otherwise be indistinguishable from a wired one.
    """
    searched = [
        *(_OP / "skills").rglob("*.md"),
        *(_OP / "templates" / "drafting").rglob("*.md"),
    ]
    for key, spec in _controls().items():
        tool = _tool_of(spec.get("wired_via") or "")
        if not tool:
            continue
        instructed = any(tool in path.read_text() for path in searched)
        assert instructed, (
            f"{key}: wired_via names tool {tool!r}, but no shipped skill or drafting "
            "discipline instructs the agent to call it. A control nothing invokes is "
            "inert; either wire it or mark the entry inert."
        )


# --------------------------------------------------------------------------- #
# substrate + tracking hygiene                                                 #
# --------------------------------------------------------------------------- #


def test_ss_console_substrate_paths_exist() -> None:
    """ss-console substrate modules must exist on disk. Overlay-resident modules
    (prefixed `overlay:`) are not checked out here — the cross-repo limit
    overlay-pairs.json / overlay-hook-surface.json already document. Modules in
    the private engagements repo (prefixed `engagements:`, ADR 0087: the
    chronology-package runner gates live beside the pipeline they audit) are
    likewise not checked out here; the prefix names the repo so the row is
    honest about where the code is, not a way to skip the check for a path
    that ought to be in this tree."""
    for key, spec in _controls().items():
        module = spec.get("substrate_module") or ""
        assert module, f"{key}: substrate_module is required"
        if module.startswith(("overlay:", "engagements:")):
            continue
        assert (_OP / module).is_file(), f"{key}: substrate_module {module!r} does not exist under operator/"


def test_tracking_references_are_well_formed() -> None:
    for key, spec in _controls().items():
        if spec.get("status") == "enforced":
            continue
        tracking = spec.get("tracking", "")
        assert _TRACKING_RE.match(tracking), f"{key}: tracking {tracking!r} must be `ADR-NNNN:Bx`, `#<issue>`, or a URL"


def test_adr_tracking_resolves_when_present() -> None:
    """Offline teeth: when a tracking ref points at an ADR that IS in-repo, that
    ADR file must actually contain the cited backlog item — an inert control
    cannot point at a vanished work item. Skips refs whose ADR is not yet merged
    onto this branch (e.g. ADR 0050 lands via a sibling PR); activates on merge.
    """
    unresolved: list[str] = []
    for key, spec in _controls().items():
        tracking = spec.get("tracking", "")
        m = re.match(r"^ADR-(\d{4}):(B\d+)$", tracking)
        if not m:
            continue
        adr_num, item = m.group(1), m.group(2)
        matches = list(_ADR_DIR.glob(f"{adr_num}-*.md"))
        if not matches:
            unresolved.append(f"{key}->{tracking} (ADR {adr_num} not in-repo yet; skipped)")
            continue
        text = matches[0].read_text(encoding="utf-8")
        assert item in text, (
            f"{key}: tracking {tracking!r} but ADR {adr_num} does not mention {item}. "
            "An inert control may not point at a backlog item that no longer exists."
        )
    if unresolved:
        # Visible, non-failing: these activate once the referenced ADR merges.
        print("runtime-control tracking refs pending ADR merge:\n  " + "\n  ".join(unresolved))
