"""CI coverage conformance (#1688) — the forcing-function culture applied to
the CI wiring itself.

The 2026-07-04 strategic review found five bin/tests suites (including the
skill-frontmatter gate and the seat-timezone regression test), the per-skill
pre_run tests, the migration tests, and templates/tests running NOWHERE in CI:
the workflow's pytest invocation was a hand-named list and its `paths:` filter
omitted whole directories, so a regression in those areas merged green.

This test pins the contract so the gap cannot silently reopen:

  1. Every ``test_*.py`` under ``operator/`` (excluding ``connectors/``,
     which the workflow's dedicated per-connector-venv conformance step
     covers) must fall under a directory the workflow's pytest step invokes.
  2. Every test file's path must be matched by at least one entry in
     ``.github/operator-substrate-paths.txt`` — otherwise a change to that
     area does not make the job run its suites. (Until 2026-09-09 this list
     was the workflow's ``on.pull_request.paths`` filter. It moved into the
     job because operator-substrate became a REQUIRED check on main, and a
     path-filtered workflow reports nothing on the PRs it skips, which
     strands them at "Expected". The matcher is imported from the detect
     script so this test and the workflow cannot disagree about a path.)
  3. The list must keep covering the mechanism itself (workflow, list, script),
     and the workflow must NOT regrow a trigger path filter.
  3b. Every step after the detect step must be gated on its output, except
     the final "nothing changed" report; an ungated heavy step would run on
     every PR, a gated report step would leave the job silent.
  4. ``operator/pytest.ini``'s ``testpaths`` must equal the workflow's pytest
     arguments, so a bare local ``pytest`` runs exactly what CI runs. Drift
     here (found 2026-08-09: testpaths listed 3 of the 10 CI directories)
     means the local suite is quietly a subset and the gap surfaces on a red
     PR instead of on the developer's machine.

Stdlib + PyYAML only (the workflow's bare env installs exactly pytest+pyyaml,
and this file runs inside that env).
"""

from __future__ import annotations

import configparser
import importlib.util
import re
from pathlib import Path

import yaml

OPERATOR_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = OPERATOR_DIR.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "operator-substrate.yml"
PATHS_FILE = REPO_ROOT / ".github" / "operator-substrate-paths.txt"
DETECT_SCRIPT = OPERATOR_DIR / "bin" / "substrate-paths-changed.py"
PYTEST_INI_PATH = OPERATOR_DIR / "pytest.ini"

# The detect script owns the matcher and the list loader; import them so the
# test's notion of "which files run the suites" is the workflow's, not a copy.
_spec = importlib.util.spec_from_file_location("substrate_paths_changed", DETECT_SCRIPT)
assert _spec is not None and _spec.loader is not None
_detect = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_detect)

DETECT_STEP_NAME = "Detect substrate-relevant changes"
SKIP_STEP_NAME = "No substrate paths changed"
GUARD = "steps.changes.outputs.relevant == 'true'"

# Directories whose tests are deliberately NOT part of the bare pytest step.
# connectors/ runs in the workflow's per-connector uv-venv conformance step
# (real installed artifact + live stdio MCP), never in the bare env.
# runners/ (ss#2613, the medchron runner) runs in its own uv-venv step for the
# same reason: it installs the connector client and needs its deps.
# hermes-smd-overlay/ (the seat runtime, in-tree since the 2026-10-22 fold, ADR
# 0095) runs as its own job, `overlay`: the seat interpreter, its own venv and
# the pinned Hermes source; test_overlay_job_runs_the_seat_runtime_suite pins it.
PYTEST_EXEMPT_TOPDIRS = {"connectors", "runners", "hermes-smd-overlay"}

# Cache/build dirs that legitimately contain no first-class tests. `.venv` is
# here because operator/'s own test instructions create one at operator/.venv,
# and site-packages ships thousands of third-party `test_*.py` files — so
# following the documented setup made this conformance check fail locally with a
# list of matplotlib and jsonschema tests. CI never sees it (it pip-installs into
# the runner's system Python), which is exactly why it went unnoticed. Added
# 2026-09-11, same shape as the `**/.venv/**` ignore added to eslint.config.js.
IGNORED_PARTS = {".pytest_cache", ".ruff_cache", "__pycache__", ".rendered", "node_modules", ".venv"}


def _load_workflow() -> dict:
    with WORKFLOW_PATH.open() as f:
        return yaml.safe_load(f)


def _on_block(wf: dict) -> dict:
    # PyYAML parses the bare `on:` key as boolean True.
    on = wf.get("on") or wf.get(True)
    assert on is not None, "workflow has no `on:` block"
    return on


def _trigger_paths(wf: dict) -> list[str]:
    """The substrate path list. Named for what it used to be (the trigger
    filter); it now lives in PATHS_FILE and gates the job's steps instead."""
    del wf  # kept for call-site symmetry; the list no longer lives in the workflow
    return _detect.load_patterns(PATHS_FILE)


def _pytest_dirs(wf: dict) -> list[str]:
    """Extract the directory/file arguments of the workflow's pytest step."""
    for job in wf["jobs"].values():
        for step in job["steps"]:
            run = step.get("run", "")
            if "-m pytest" in run and step.get("working-directory") == "operator":
                args = run.split("-m pytest", 1)[1].split()
                return [a for a in args if not a.startswith("-")]
    raise AssertionError("no pytest step with working-directory: operator found in the workflow")


def _matches(pattern: str, path: str) -> bool:
    """The detect script's matcher, not a copy of it."""
    return _detect.matches(pattern, path)


def _operator_test_files() -> list[Path]:
    out = []
    for p in OPERATOR_DIR.rglob("test_*.py"):
        rel = p.relative_to(OPERATOR_DIR)
        if IGNORED_PARTS.intersection(rel.parts):
            continue
        out.append(rel)
    return sorted(out)


def test_every_test_file_is_invoked_by_the_pytest_step() -> None:
    wf = _load_workflow()
    invoked = _pytest_dirs(wf)
    uncovered = []
    for rel in _operator_test_files():
        top = rel.parts[0]
        if top in PYTEST_EXEMPT_TOPDIRS:
            continue
        posix = rel.as_posix()
        if not any(posix == d or posix.startswith(d.rstrip("/") + "/") for d in invoked):
            uncovered.append(posix)
    assert not uncovered, (
        "test files not reachable by the CI pytest step (add their dir to the "
        f"pytest invocation in {WORKFLOW_PATH.name}): {uncovered}"
    )


def test_pytest_ini_testpaths_match_the_ci_invocation() -> None:
    cfg = configparser.ConfigParser()
    cfg.read(PYTEST_INI_PATH)
    testpaths = cfg["pytest"]["testpaths"].split()
    invoked = _pytest_dirs(_load_workflow())
    assert sorted(testpaths) == sorted(invoked), (
        "operator/pytest.ini testpaths and the workflow's pytest arguments have "
        f"drifted. testpaths-only: {sorted(set(testpaths) - set(invoked))}; "
        f"workflow-only: {sorted(set(invoked) - set(testpaths))}"
    )


def test_every_test_file_area_triggers_the_workflow() -> None:
    wf = _load_workflow()
    paths = _trigger_paths(wf)
    untriggered = []
    for rel in _operator_test_files():
        repo_rel = f"operator/{rel.as_posix()}"
        if not any(_matches(pat, repo_rel) for pat in paths):
            untriggered.append(repo_rel)
    assert not untriggered, (
        f"test files whose changes would NOT run the substrate suites (extend {PATHS_FILE.name}): {untriggered}"
    )


def test_path_list_covers_the_mechanism_itself() -> None:
    paths = _trigger_paths(_load_workflow())
    for repo_rel in (
        ".github/workflows/operator-substrate.yml",
        ".github/operator-substrate-paths.txt",
        "operator/bin/substrate-paths-changed.py",
    ):
        assert any(_matches(p, repo_rel) for p in paths), (
            f"{repo_rel} is part of the substrate gate and must be in {PATHS_FILE.name}, "
            "so a change to the mechanism runs the mechanism"
        )


def test_workflow_has_no_trigger_path_filter() -> None:
    # A required check must report on every PR. `paths:` or `paths-ignore:`
    # under pull_request would skip the workflow entirely on some PRs, and a
    # skipped required check strands the PR at "Expected". The path decision
    # belongs to the detect step, never to the trigger.
    on = _on_block(_load_workflow())
    pr = on.get("pull_request")
    assert "pull_request" in on, "workflow must trigger on pull_request"
    if isinstance(pr, dict):
        assert "paths" not in pr and "paths-ignore" not in pr, (
            "operator-substrate is a required check; move any path decision into "
            f"{PATHS_FILE.name}, never back onto the trigger"
        )
    assert "push" in on and "main" in (on["push"] or {}).get("branches", []), (
        "workflow must also run on push to main so main itself is proven"
    )


def test_every_suite_step_is_gated_on_the_detect_step() -> None:
    steps = _load_workflow()["jobs"]["substrate"]["steps"]
    names = [s.get("name") for s in steps]
    assert DETECT_STEP_NAME in names, f"missing the '{DETECT_STEP_NAME}' step"
    detect_idx = names.index(DETECT_STEP_NAME)
    detect = steps[detect_idx]
    assert detect.get("id") == "changes"
    assert "substrate-paths-changed.py" in (detect.get("run") or "")
    assert names[-1] == SKIP_STEP_NAME, "the last step must be the legible skip report"
    assert steps[-1].get("if") == "steps.changes.outputs.relevant != 'true'"
    ungated = [s.get("name") for s in steps[detect_idx + 1 : -1] if (s.get("if") or "").strip() != GUARD]
    assert not ungated, (
        f"steps after the detect step must carry `if: {GUARD}` so an unrelated PR does not run the suites: {ungated}"
    )
    # And the steps before it are exactly the checkout, with full history so
    # base...head resolves.
    assert names[:detect_idx] == ["Checkout"], names[:detect_idx]
    assert (steps[0].get("with") or {}).get("fetch-depth") == 0


def test_detect_script_fails_closed_and_matches_the_list(tmp_path: Path) -> None:
    patterns = _detect.load_patterns(PATHS_FILE)
    # A change under a listed dir is relevant; a docs-only change is not.
    assert _detect.relevant_paths(["operator/skills/x/pre_run.py"], patterns)
    assert not _detect.relevant_paths(["docs/reviews/x.md", "src/pages/index.astro"], patterns)
    # Exact-file patterns match only themselves.
    assert _detect.relevant_paths(["operator/ruff.toml"], patterns)
    assert not _detect.relevant_paths(["operator/ruff.toml.bak"], patterns)
    # An empty list is an error, not "nothing is relevant".
    empty = tmp_path / "empty.txt"
    empty.write_text("# nothing\n")
    try:
        _detect.load_patterns(empty)
    except ValueError:
        pass
    else:
        raise AssertionError("an empty pattern list must raise, not silently skip every suite")
    # No base sha means run everything (push to main).
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _detect.main(["--base", "", "--paths", str(PATHS_FILE)])
    assert rc == 0 and "relevant=true" in buf.getvalue()
    # An unresolvable sha means run everything too.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _detect.main(["--base", "0" * 40, "--head", "HEAD", "--paths", str(PATHS_FILE)])
    assert rc == 0 and "relevant=true" in buf.getvalue()


_PATH_LITERAL = re.compile(
    r"""["'](?P<p>(?:operator/)?(?:fixtures|customers|contracts|templates|skills|verticals)/[A-Za-z0-9_./\-]+)["']"""
)


def _paths_a_test_reaches(test_file: Path) -> list[str]:
    """Repo-relative paths a substrate test opens or executes, read from its
    string literals. Not every literal is a path, so only the shapes the
    tree uses are matched (a known area, then a slash-separated tail)."""
    out = []
    for m in _PATH_LITERAL.finditer(test_file.read_text(encoding="utf-8")):
        lit = m.group("p")
        repo_rel = lit if lit.startswith("operator/") else f"operator/{lit}"
        out.append(repo_rel)
    return out


def test_every_path_a_substrate_test_reaches_triggers_the_workflow() -> None:
    """2026-09-10 review, top action item 4. `operator/tests/test_closeout_seed.py`
    executes a script under `operator/fixtures/` by subprocess, and that
    directory was not in the list: a fixture-only PR reported "No substrate
    paths changed" and merged green, while the break surfaced on the next
    unrelated PR. The test-file check above cannot see this; this one walks
    each substrate test for the paths it reaches."""
    paths = _trigger_paths(_load_workflow())
    untriggered = []
    for rel in _operator_test_files():
        if rel.parts[0] in PYTEST_EXEMPT_TOPDIRS:
            continue
        for reached in _paths_a_test_reaches(OPERATOR_DIR / rel):
            if not any(_matches(pat, reached) for pat in paths):
                untriggered.append(f"{rel.as_posix()} -> {reached}")
    assert not untriggered, (
        "substrate tests reach paths whose change would NOT run the suites "
        f"(extend {PATHS_FILE.name}): {sorted(set(untriggered))}"
    )
    # The instrument must see the case it exists for.
    seed = OPERATOR_DIR / "tests" / "test_closeout_seed.py"
    assert any(p.startswith("operator/fixtures/") for p in _paths_a_test_reaches(seed)), (
        "the closeout seed test no longer references operator/fixtures/; re-check the matcher"
    )


def test_connector_tests_are_covered_by_the_conformance_step_trigger() -> None:
    # connectors/ is pytest-exempt above, so pin its trigger path explicitly:
    # if it left the filter, connector regressions would merge green too.
    wf = _load_workflow()
    assert any(_matches(p, "operator/connectors/smokeball/server.py") for p in _trigger_paths(wf))


def test_runner_tests_are_covered_by_their_own_step_and_trigger() -> None:
    # runners/ is pytest-exempt above (own venv step, ss#2613); pin both the
    # trigger path and the step so a runner regression cannot merge green.
    wf = _load_workflow()
    assert any(_matches(p, "operator/runners/medchron/medchron/driver.py") for p in _trigger_paths(wf))
    steps = wf["jobs"]["substrate"]["steps"]
    runs = "\n".join(s.get("run") or "" for s in steps)
    assert "./runners/medchron" in runs and "runners/medchron/tests" in runs, (
        "the medchron runner venv step is missing from operator-substrate.yml"
    )


def test_overlay_job_runs_the_seat_runtime_suite() -> None:
    """ADR 0095 (2026-10-22): the seat runtime is operator/hermes-smd-overlay and
    its suite runs as the `overlay` job. hermes-smd-overlay is pytest-exempt
    above, so pin here what makes that exemption honest: the job exists, is
    gated the way `substrate` is (detect step, every suite step guarded, a
    legible skip report last), runs pytest FROM the overlay directory on the
    seat interpreter, fetches Hermes at the sha the image installs (read from
    the Dockerfile, not typed), and a change under the overlay triggers it."""
    wf = _load_workflow()
    assert any(_matches(p, "operator/hermes-smd-overlay/shared/heartbeat.py") for p in _trigger_paths(wf))
    job = wf["jobs"].get("overlay")
    assert job, "operator-substrate.yml has no `overlay` job"
    steps = job["steps"]
    names = [s.get("name") for s in steps]
    assert names[:2] == ["Checkout", DETECT_STEP_NAME], names[:2]
    assert steps[1].get("id") == "changes"
    assert names[-1] == SKIP_STEP_NAME and steps[-1].get("if") == "steps.changes.outputs.relevant != 'true'"
    ungated = [s.get("name") for s in steps[2:-1] if (s.get("if") or "").strip() != GUARD]
    assert not ungated, f"overlay job steps must carry `if: {GUARD}`: {ungated}"
    pytest_steps = [
        s
        for s in steps
        if "-m pytest" in (s.get("run") or "") and s.get("working-directory") == "operator/hermes-smd-overlay"
    ]
    assert len(pytest_steps) == 1, "exactly one pytest step runs from operator/hermes-smd-overlay"
    runs = "\n".join(s.get("run") or "" for s in steps)
    assert "uv venv --python 3.13" in runs, "the overlay suite runs on the seat interpreter"
    assert "ARG HERMES_UPSTREAM_SHA" in runs and "NousResearch/hermes-agent" in runs, (
        "the pinned Hermes must be read from the Dockerfile and fetched from upstream"
    )
    assert "HERMES_AGENT_SRC=" in runs
    assert "operator/requirements/compile.sh" in runs and "hermes-overlay.txt" in runs, (
        "the overlay job must prove hermes-overlay.txt is current (compile.sh no-diff)"
    )


def test_overlay_e2e_job_runs_the_harness_at_the_image_pin() -> None:
    """ss W3: `overlay-e2e` runs operator/hermes-smd-overlay/tests_e2e against the
    pinned Hermes. Pin what makes it an instrument rather than a decoration: it
    is gated like `overlay` (detect step, every later step guarded, the scope
    summary allowed to run after a failure, a legible skip report last); the
    Hermes sha comes from the Dockerfile's ARG HERMES_UPSTREAM_SHA, never typed,
    and a dispatch override is accepted only as a full 40-hex commit; Hermes is
    installed with its [mcp,anthropic] extras on the seat interpreter; the harness runs
    from the overlay directory with -x and a per-test timeout; and the workflow
    can be dispatched with a candidate sha."""
    wf = _load_workflow()
    job = wf["jobs"].get("overlay-e2e")
    assert job, "operator-substrate.yml has no `overlay-e2e` job"
    steps = job["steps"]
    names = [s.get("name") for s in steps]
    assert names[:2] == ["Checkout", DETECT_STEP_NAME], names[:2]
    assert (steps[0].get("with") or {}).get("fetch-depth") == 0
    assert steps[1].get("id") == "changes"
    assert names[-1] == SKIP_STEP_NAME and steps[-1].get("if") == "steps.changes.outputs.relevant != 'true'"
    summary_guard = f"always() && {GUARD}"
    ungated = [s.get("name") for s in steps[2:-1] if (s.get("if") or "").strip() not in (GUARD, summary_guard)]
    assert not ungated, f"overlay-e2e steps must carry `if: {GUARD}`: {ungated}"

    pin = next((s for s in steps if s.get("id") == "pin"), None)
    assert pin is not None, "overlay-e2e must resolve the Hermes sha in a step with id `pin`"
    pin_run = pin.get("run") or ""
    assert "ARG HERMES_UPSTREAM_SHA" in pin_run and "operator/templates/Dockerfile" in pin_run, (
        "the Hermes sha must be read from the Dockerfile's ARG, not typed into the workflow"
    )
    assert "[0-9a-f]{40}" in pin_run, "a dispatch override must be validated as a 40-hex commit"
    assert "hermes_sha" in str((pin.get("env") or {}).get("CANDIDATE", ""))

    runs = "\n".join(s.get("run") or "" for s in steps)
    assert "NousResearch/hermes-agent" in runs and "steps.pin.outputs.sha" in str(steps), (
        "Hermes must be fetched from upstream at the resolved sha"
    )
    assert "HERMES_AGENT_SRC=" in runs, "the harness skips without HERMES_AGENT_SRC"
    assert "uv venv --python 3.13" in runs, "the harness runs on the seat interpreter"
    assert '[mcp,anthropic]"' in runs and "hermes-smd-overlay[e2e]" in runs, (
        "Hermes needs its anthropic extra: the seat adapter (anthropic_messages) imports the SDK"
    )
    harness = [
        s
        for s in steps
        if "-m pytest tests_e2e" in (s.get("run") or "") and s.get("working-directory") == "operator/hermes-smd-overlay"
    ]
    assert len(harness) == 1, "exactly one step runs tests_e2e from operator/hermes-smd-overlay"
    assert "-x" in harness[0]["run"] and "--timeout=" in harness[0]["run"]

    on = _on_block(wf)
    dispatch = on.get("workflow_dispatch") or {}
    hermes_input = (dispatch.get("inputs") or {}).get("hermes_sha")
    assert hermes_input is not None, "workflow_dispatch must accept a hermes_sha candidate"
    assert hermes_input.get("required") is False, "a blank hermes_sha runs at the Dockerfile pin"


# -- the release controller (ADR 0096) ----------------------------------------------------

RELEASE_TICK_PATH = REPO_ROOT / ".github" / "workflows" / "release-tick.yml"
DEPLOY_PATH = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
RINGS_PATH = OPERATOR_DIR / "fleet" / "rings.yaml"
FLEET_RELEASE_ENVIRONMENT = "fleet-release"


def _job_runs(job: dict) -> str:
    return "\n".join(s.get("run") or "" for s in job.get("steps") or [])


def _ringed_seats() -> list[str]:
    doc = yaml.safe_load(RINGS_PATH.read_text(encoding="utf-8")) or {}
    return [seat for ring in doc.get("rings") or [] for seat in ring.get("seats") or []]


def test_release_tick_installs_every_tool_a_seat_render_needs() -> None:
    """The tick renders each seat's fly.toml with operator/bin/lib/render-fly-toml.sh,
    which calls `uv run --with pyyaml`. The first live tick (2026-10-26) ran on a
    runner without uv: every render failed and the tick halted at ring 0 as
    tooling_error. The unit tests stub the renderer, so only this pin catches it."""
    render = (REPO_ROOT / "operator/bin/lib/render-fly-toml.sh").read_text(encoding="utf-8")
    assert "uv run" in render, "the renderer no longer needs uv; drop this test with it"
    wf = yaml.safe_load(RELEASE_TICK_PATH.read_text(encoding="utf-8"))
    steps = wf["jobs"]["tick"]["steps"]
    uses = [str(s.get("uses") or "") for s in steps]
    names = [str(s.get("name") or "") for s in steps]
    assert any(u.startswith("astral-sh/setup-uv@") for u in uses), "the tick job must set up uv"
    assert names.index("Set up uv") < names.index("Tick"), "uv must be installed before the tick runs"


def test_release_tick_checks_out_full_history_for_the_latest_resolution() -> None:
    """`fleet release latest --tick` keeps only image releases whose commit is an
    ancestor of main (cli._built_from_main, `git merge-base --is-ancestor`). On a
    shallow clone any commit but main's head is missing, git exits 128, and every
    tick halts as a tooling error once main has moved (run 40362697118)."""
    cli = (OPERATOR_DIR / "bin/lib/fleet/cli.py").read_text(encoding="utf-8")
    assert "--is-ancestor" in cli, "latest no longer walks history; drop this test with it"
    wf = yaml.safe_load(RELEASE_TICK_PATH.read_text(encoding="utf-8"))
    checkout = next(s for s in wf["jobs"]["tick"]["steps"] if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert (checkout.get("with") or {}).get("fetch-depth") == 0, "the tick needs main's full history"


def test_release_tick_runs_protected_serialized_and_on_schedule() -> None:
    """The tick reads the per-app Fly tokens, so it runs only in the protected
    environment, on main, one at a time, and on a schedule plus build completion."""
    wf = yaml.safe_load(RELEASE_TICK_PATH.read_text(encoding="utf-8"))
    on = _on_block(wf)
    assert (on.get("schedule") or [{}])[0].get("cron"), "release-tick must run on a schedule"
    assert "Deploy" in ((on.get("workflow_run") or {}).get("workflows") or []), "it must run when Deploy completes"
    assert "workflow_dispatch" in on
    concurrency = wf.get("concurrency") or {}
    assert concurrency.get("group") and concurrency.get("cancel-in-progress") is False, (
        "ticks must never overlap, and a running tick must never be cancelled mid-deploy"
    )
    job = wf["jobs"]["tick"]
    assert job.get("environment") == FLEET_RELEASE_ENVIRONMENT
    assert "refs/heads/main" in str(job.get("if")), "a tick runs only from main"
    runs = _job_runs(job)
    assert "--tick" in runs and "operator/bin/fleet" in runs


def test_release_automation_never_forces_a_busy_seat_or_accepts_lane_inputs() -> None:
    for path in (RELEASE_TICK_PATH, DEPLOY_PATH):
        text = path.read_text(encoding="utf-8")
        body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        assert "--force-mid-job" not in body, f"{path.name} must never pass --force-mid-job"
        assert "--accept-lane-inputs" not in body, f"{path.name} must never pass --accept-lane-inputs"


ENABLE_GATE = "vars.FLEET_RELEASE_ENABLED == 'true'"


def test_every_job_that_enters_fleet_release_is_gated_on_the_enable_variable_at_job_level() -> None:
    """A job `if:` is evaluated before the environment is entered, so a disabled
    run never touches fleet-release, and never makes GitHub auto-create it
    without its main-only branch policy (ADR 0096). FALSIFIER: drop the variable
    from either job's `if:` and this fails."""
    gated = []
    for path in (RELEASE_TICK_PATH, DEPLOY_PATH):
        wf = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, job in wf["jobs"].items():
            if job.get("environment") == FLEET_RELEASE_ENVIRONMENT:
                assert ENABLE_GATE in str(job.get("if") or ""), f"{path.name} job {name} must gate on {ENABLE_GATE}"
                gated.append(name)
            else:
                assert "secrets." not in str(job) or "FLY_" not in str(job), (
                    f"{path.name} job {name} reads a Fly secret outside the fleet-release environment"
                )
    assert sorted(gated) == ["build-seat-image", "tick"]
    disabled = yaml.safe_load(RELEASE_TICK_PATH.read_text(encoding="utf-8"))["jobs"].get("disabled") or {}
    assert disabled.get("if") == "vars.FLEET_RELEASE_ENABLED != 'true'" and "environment" not in disabled, (
        "a disabled run says why, without entering the environment"
    )


def test_release_tick_carries_one_token_per_ringed_seat_named_as_the_cli_reads_it() -> None:
    from bin.lib.fleet.fly import REGISTRY_TOKEN_ENV, seat_token_env_name

    wf = yaml.safe_load(RELEASE_TICK_PATH.read_text(encoding="utf-8"))
    job = wf["jobs"]["tick"]
    env: dict[str, str] = {}
    for step in job["steps"]:
        env.update(step.get("env") or {})
    wanted = {seat_token_env_name(f"hermes-{seat}") for seat in _ringed_seats()} | {REGISTRY_TOKEN_ENV}
    missing = sorted(name for name in wanted if env.get(name) != f"${{{{ secrets.{name} }}}}")
    assert not missing, f"release-tick.yml must pass these from the fleet-release secrets: {missing}"
    assert not any("FLY_API_TOKEN" in k for k in env), "no ambient Fly token: the CLI picks one per app"
    assert "toJSON(secrets)" not in RELEASE_TICK_PATH.read_text(encoding="utf-8")


def test_build_seat_image_job_builds_at_the_merge_from_the_protected_environment() -> None:
    wf = yaml.safe_load(DEPLOY_PATH.read_text(encoding="utf-8"))
    job = wf["jobs"].get("build-seat-image")
    assert job, "deploy.yml has no build-seat-image job"
    assert job.get("environment") == FLEET_RELEASE_ENVIRONMENT
    assert ((job.get("concurrency") or {}).get("cancel-in-progress")) is False
    steps = job["steps"]
    assert (steps[0].get("with") or {}).get("fetch-depth") == 0, "the build guard needs full history"
    detect = next(s for s in steps if s.get("id") == "changes")
    assert "substrate-paths-changed.py" in detect["run"] and "operator-substrate-paths.txt" in detect["run"]
    build = next(s for s in steps if "fleet build" in (s.get("run") or ""))
    assert build.get("if") == "steps.changes.outputs.relevant == 'true'"
    assert '"$rc" -eq 4' in build["run"] and "HEAD..origin/main" in build["run"], (
        "exit 4 is a clean skip only when main has moved past this commit"
    )
    assert (build.get("env") or {}).get("FLY_REGISTRY_TOKEN") == "${{ secrets.FLY_REGISTRY_TOKEN }}"
    assert not any(k.startswith("FLY_DEPLOY_TOKEN__") for k in build.get("env") or {}), (
        "the build touches the registry app only; it never holds a seat token"
    )
