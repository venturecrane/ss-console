"""operator/templates/jobs-runtime.sh: the job runtime is built by one script, and it is the old build.

ADR 0100 W3 moved the connector-venv loop and the medchron venv out of the
Dockerfile's RUN lines into one script, because a second build (CI's `jobs`
bundle, scripts/ci-publish-bundle.sh) must produce the same trees at the same
paths, and two copies of a build drift. This change reaches every seat image, so
the bar is EQUIVALENCE with the RUN lines it replaced (Dockerfile at 84f47cbc2,
lines 661-715): same hash-locked pins, same --no-deps local installs, same
console-script assertions, same import list, same antiword assertion, same
root:root a+rX.

How it is proven: the script runs for real under /bin/sh with every external
command it calls replaced by a stub that records its argv, and the recorded
sequence must equal the sequence those RUN lines executed, transcribed below.
A PATH holding only the stubs means a command the script calls and the test does
not know about fails the run rather than reaching the host.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

OPERATOR = Path(__file__).resolve().parents[2]
SCRIPT = OPERATOR / "templates" / "jobs-runtime.sh"
DOCKERFILE = OPERATOR / "templates" / "Dockerfile"

# The build-time import smoke, verbatim from the replaced RUN line (Dockerfile:712 at 84f47cbc2).
IMPORT_LIST = (
    "import medchron.daemon, medchron.stages.upload, medchron.demand_lane, medchron.demand.run, "
    "medchron.drafting_lane, medchron.drafting.run, medchron.negotiation_lane, medchron.negotiation.run, "
    "medchron.note_lane, medchron.note_by_smd, medchron.litigation_lane, medchron.litigation.run, "
    "striprtf.striprtf, smokeball_connector.docx_format, docx, medchron.casefile_lane, medchron.casefile.run, "
    "medchron.casefile.firm"
)

# uv: `venv X` makes X/bin/python (itself a recording stub); a --no-deps install
# makes the console-script the replaced RUN lines asserted on.
UV_STUB = """#!/bin/sh
echo "uv $*" >> "$LOG"
if [ "$1" = venv ]; then
  mkdir -p "$2/bin"
  printf '#!/bin/sh\\necho "python $*" >> "$LOG"\\n' > "$2/bin/python"
  chmod_real=$(command -v chmod_real); "$chmod_real" +x "$2/bin/python"
fi
case "$*" in
  *--no-deps*) venv=$(dirname "$(dirname "$4")")
    case "$venv" in
      */medchron/.venv) : > "$venv/bin/medchron"; chmod_real +x "$venv/bin/medchron" ;;
      *) name=$(basename "$(dirname "$venv")"); : > "$venv/bin/${name}-mcp" ;;
    esac ;;
esac
"""
RECORD_STUB = '#!/bin/sh\necho "$(basename "$0") $*" >> "$LOG"\n'
REAL_TOOLS = ("basename", "dirname", "mkdir", "ls", "cp", "printf", "cat")


def _exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def box(tmp_path: Path) -> dict[str, Path]:
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for tool in REAL_TOOLS:
        real = shutil.which(tool)
        assert real, tool
        (stubs / tool).symlink_to(real)
    real_chmod = shutil.which("chmod")
    assert real_chmod
    (stubs / "chmod_real").symlink_to(real_chmod)
    _exe(stubs / "uv", UV_STUB)
    for name in ("chown", "chmod", "antiword"):
        _exe(stubs / name, RECORD_STUB)
    src = tmp_path / "src"
    for d in ("connectors/_sdk", "connectors/smokeball", "connectors/no-pyproject", "runners/medchron", "requirements"):
        (src / d).mkdir(parents=True)
    (src / "connectors/smokeball/pyproject.toml").write_text("")
    (src / "connectors/_sdk/pyproject.toml").write_text("")
    (src / "requirements/connector-smokeball.txt").write_text("")
    (src / "requirements/medchron.txt").write_text("")
    return {"tmp": tmp_path, "stubs": stubs, "src": src, "opt": tmp_path / "opt", "log": tmp_path / "log"}


def _run(box: dict[str, Path], *args: str, extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": str(box["stubs"]),
        "SRC": str(box["src"]),
        "JOBS_RUNTIME_OPT": str(box["opt"]),
        "LOG": str(box["log"]),
        **(extra or {}),
    }
    return subprocess.run(["/bin/sh", str(SCRIPT), *args], env=env, capture_output=True, text=True, check=False)  # noqa: S603 - fixed argv


def _log(box: dict[str, Path]) -> list[str]:
    text = box["log"].read_text() if box["log"].exists() else ""
    return [ln.replace(str(box["opt"]), "/opt").replace(str(box["src"]), "/app") for ln in text.splitlines()]


def test_connectors_runs_exactly_the_replaced_connector_loop(box: dict[str, Path]) -> None:
    res = _run(box, "connectors")
    assert res.returncode == 0, res.stderr + res.stdout
    v = "/opt/connectors/smokeball/.venv"
    assert _log(box) == [
        f"uv venv {v}",
        f"uv pip install --python {v}/bin/python --no-cache --no-config --require-hashes -r /app/requirements/connector-smokeball.txt",
        f"uv pip install --python {v}/bin/python --no-cache --no-deps /app/connectors/_sdk /app/connectors/smokeball/",
        "chmod -R a+rX /opt/connectors",
    ]


def test_medchron_runs_exactly_the_replaced_runner_build(box: dict[str, Path]) -> None:
    res = _run(box, "medchron")
    assert res.returncode == 0, res.stderr + res.stdout
    py = "/opt/medchron/.venv/bin/python"
    assert _log(box) == [
        "uv venv /opt/medchron/.venv",
        f"uv pip install --python {py} --no-cache --no-config --require-hashes -r /app/requirements/medchron.txt",
        f"uv pip install --python {py} --no-cache --no-deps /app/connectors/_sdk /app/connectors/smokeball /app/runners/medchron",
        f"python -c {IMPORT_LIST}",
        "chown -R root:root /opt/medchron",
        "chmod -R a+rX /opt/medchron",
    ]


def test_one_connector_for_the_bundle_is_the_same_install(box: dict[str, Path]) -> None:
    res = _run(box, "connector", "smokeball")
    assert res.returncode == 0, res.stderr + res.stdout
    log = _log(box)
    assert log[:3] == _expected_connector_lines()
    assert log[3] == "chmod -R a+rX /opt/connectors/smokeball"


def _expected_connector_lines() -> list[str]:
    v = "/opt/connectors/smokeball/.venv"
    return [
        f"uv venv {v}",
        f"uv pip install --python {v}/bin/python --no-cache --no-config --require-hashes -r /app/requirements/connector-smokeball.txt",
        f"uv pip install --python {v}/bin/python --no-cache --no-deps /app/connectors/_sdk /app/connectors/smokeball/",
    ]


def test_the_pricing_table_is_copied_beside_the_venv_when_asked(box: dict[str, Path]) -> None:
    pricing = box["tmp"] / "anthropic_pricing.json"
    pricing.write_text('{"x": 1}')
    assert _run(box, "medchron", extra={"PRICING_JSON": str(pricing)}).returncode == 0
    assert (box["opt"] / "medchron/anthropic_pricing.json").read_text() == '{"x": 1}'


def test_the_build_fails_without_its_assertions_met(box: dict[str, Path]) -> None:
    """FALSIFIERS: each assertion the replaced RUN lines made still fails the build."""
    (box["stubs"] / "antiword").unlink()
    res = _run(box, "medchron")
    assert res.returncode != 0 and "antiword is missing" in res.stdout
    (box["src"] / "requirements/connector-smokeball.txt").unlink()
    res = _run(box, "connectors")
    assert res.returncode != 0 and "has no pinned requirements" in res.stdout
    res = _run(box, "connector", "no-such")
    assert res.returncode != 0


def test_a_failing_import_fails_the_check(box: dict[str, Path]) -> None:
    bad = box["tmp"] / "badpy"
    _exe(bad, "#!/bin/sh\nexit 1\n")
    assert _run(box, "import-check", str(bad)).returncode != 0
    good = box["tmp"] / "goodpy"
    _exe(good, RECORD_STUB)
    assert _run(box, "import-check", str(good)).returncode == 0


def test_the_dockerfile_runs_the_script_where_the_replaced_lines_ran() -> None:
    """The image runs the script after its inputs are COPYed, and builds no job venv of its own."""
    lines = [ln for ln in DOCKERFILE.read_text().splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    at = {ln: i for i, ln in enumerate(lines)}
    copy_script = at["COPY operator/templates/jobs-runtime.sh /app/jobs-runtime.sh"]
    run_conn = at["RUN sh /app/jobs-runtime.sh connectors"]
    run_med = at["RUN sh /app/jobs-runtime.sh medchron"]
    assert at["COPY operator/requirements/ /app/requirements/"] < run_conn
    assert at["COPY operator/connectors/ /app/connectors/"] < copy_script < run_conn < run_med
    assert at["COPY operator/runners/ /app/runners/"] < run_med
    assert (
        at["COPY operator/adapter/cost_telemetry/anthropic_pricing.json /opt/medchron/anthropic_pricing.json"] < run_med
    )
    code = "\n".join(lines)
    for gone in ("uv venv /opt/medchron", "/opt/connectors/${name}", "medchron.casefile.firm", "command -v antiword"):
        assert gone not in code, f"the Dockerfile builds the job runtime itself again ({gone}); use jobs-runtime.sh"
    assert os.access(SCRIPT, os.R_OK)
