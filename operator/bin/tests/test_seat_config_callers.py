"""The seat-config caller table is a golden, and its instrument can fail.

operator/contracts/seat-config-callers.json records what every customer.yaml
hand parser, broker socket client and CUSTOMER_SLUG read in the operator tree
DOES when run over fixed inputs (wave 2, Phase A, item A2). The first test
regenerates the table by running every site again and fails on any difference:
a change to what a site returns, raises or puts on the wire is a reviewed
regeneration, never silent. The rest plant small sites with known behaviour in
a scratch tree and check the instrument reports exactly that behaviour, both
ways, so a pass of the golden means the instrument looked.

Run::

    cd operator && python3 -m pytest bin/tests/test_seat_config_callers.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

OPERATOR = Path(__file__).resolve().parents[2]
REPO = OPERATOR.parent
sys.path.insert(0, str(OPERATOR))

from bin.lib.seat_callers.canon import Normaliser, digest  # noqa: E402 - imported only once sys.path holds the operator tree
from bin.lib.seat_callers.capture import _brief, _connects, _row, run_site  # noqa: E402 - imported only once sys.path holds the operator tree
from bin.lib.seat_callers.diff import differences  # noqa: E402 - imported only once sys.path holds the operator tree
from bin.lib.seat_callers.discover import CONCEPTS, SiteSpec, file_matches, sites_in  # noqa: E402 - imported only once sys.path holds the operator tree
from bin.lib.seat_callers.modes import modes  # noqa: E402 - imported only once sys.path holds the operator tree

SCRIPT = OPERATOR / "bin" / "capture-seat-config-callers.py"
TABLE = OPERATOR / "contracts" / "seat-config-callers.json"


def test_the_committed_table_is_what_the_sites_do_today() -> None:
    proc = subprocess.run(  # noqa: S603 - this interpreter and the tracked capture script, fixed flags
        [sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True, timeout=1500
    )
    assert proc.returncode == 0, proc.stdout[-8000:] + proc.stderr[-2000:]


# ------------------------------------------------------------ planted sites


def _plant(tmp_path: Path, concept: str, source: str) -> dict[str, dict]:
    repo = tmp_path / "repo"
    pkg = repo / "operator" / "planted_tree"
    pkg.mkdir(parents=True)
    (pkg / "planted_site.py").write_text(source, encoding="utf-8")
    rel = "operator/planted_tree/planted_site.py"
    out = {}
    for spec in sites_in(concept, rel, source):
        result = run_site(spec, repo, sys.executable)
        runs = result.get("runs", [])
        out[spec.qualname or str(spec.line)] = {
            "result": result,
            "runs": {r["input"]: r for r in runs},
            "modes": modes(concept, runs) if runs else [],
        }
    return out


YAML_SITES = """
import yaml
from pathlib import Path

CUSTOMER_YAML = Path("/opt/data/customer.yaml")
DEFAULTS = {"enabled": True}


def strict():
    return yaml.safe_load(CUSTOMER_YAML.read_text())["memory"]


def forgiving():
    try:
        doc = yaml.safe_load(CUSTOMER_YAML.read_text()) or {}
        return doc.get("memory") or DEFAULTS
    except Exception:
        return DEFAULTS


def shells_out():
    import subprocess
    subprocess.run(["true"])
    return yaml.safe_load(CUSTOMER_YAML.read_text())
"""


def test_a_loader_that_raises_and_one_that_fails_open_are_told_apart(tmp_path: Path) -> None:
    got = _plant(tmp_path, "customer-yaml", YAML_SITES)
    strict, forgiving = got["strict"], got["forgiving"]
    assert strict["runs"]["good"]["outcome"] == "return"
    assert "raises-FileNotFoundError-on-missing" in strict["modes"]
    assert "raises-IsADirectoryError-on-unreadable" in strict["modes"]
    # it hands a wrong-typed block straight to its caller: the instrument calls that failing open
    assert [m for m in strict["modes"] if m.startswith("fails-open")] == [
        "fails-open-on-block-malformed",
        "fails-open-on-block-malformed-deep",
    ]
    assert "fails-open-on-unreadable" in forgiving["modes"]
    assert "fails-open-on-yaml-syntax-error" in forgiving["modes"]
    assert "returns-value-on-missing" in forgiving["modes"]
    # the block a site reads is observed, so block-missing removes exactly it
    assert "returns-as-good-on-block-missing" not in strict["modes"]
    assert strict["runs"]["block-missing"]["outcome"] == "raise"


def test_a_side_effect_is_blocked_and_recorded_not_performed(tmp_path: Path) -> None:
    got = _plant(tmp_path, "customer-yaml", YAML_SITES)["shells_out"]
    ran = [r for r in got["runs"].values() if r.get("outcome") not in (None, "not-applicable")]
    assert ran and {r.get("kind") for r in ran} == {"subprocess"}


SOCKET_SITES = """
import json, os, socket

SOCK = "/run/smd-broker/broker.sock"


def _read(s):
    data = b""
    while True:
        chunk = s.recv(4096)
        if not chunk:
            return data
        data += chunk


def honest(payload):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(2)
    s.connect(SOCK)
    s.sendall((json.dumps(payload) + "\\n").encode("utf-8"))
    return json.loads(_read(s))


def optimist(payload):
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(SOCK)
        s.sendall((json.dumps(payload, ensure_ascii=False) + "\\n").encode("utf-8"))
        return json.loads(_read(s))
    except Exception:
        return {"ok": True, "result": {"recorded": True}}
"""


def test_wire_bytes_and_a_client_that_reports_success_on_a_broken_reply(tmp_path: Path) -> None:
    got = _plant(tmp_path, "broker-socket", SOCKET_SITES)
    honest, optimist = got["honest"], got["optimist"]
    assert "wire-ascii-escaped" in honest["modes"]
    assert "wire-utf8" in optimist["modes"]
    assert honest["runs"]["ok/non-ascii"]["wire"][0]["bytes"].endswith("\n")
    assert "raises-JSONDecodeError-on-non-json" in honest["modes"]
    assert "raises-FileNotFoundError-on-no-socket" in honest["modes"]
    assert "raises-ConnectionRefusedError-on-refused" in honest["modes"]
    assert not [m for m in honest["modes"] if m.startswith("fails-open")]
    for scenario in ("empty-reply", "non-json", "fault-after-send", "no-socket", "refused"):
        assert f"fails-open-on-{scenario}" in optimist["modes"], scenario
    assert "fails-open-on-not-ok" not in optimist["modes"]


SLUG_SITES = """
import os


def plain():
    return os.environ.get("CUSTOMER_SLUG")


def defaulted():
    return (os.environ.get("CUSTOMER_SLUG") or "fallback-seat").strip()


def alternate_first():
    return os.environ.get("SMD_CUSTOMER_SLUG") or os.environ["CUSTOMER_SLUG"]
"""


def test_slug_reads_are_recorded_per_environment_state(tmp_path: Path) -> None:
    got = _plant(tmp_path, "slug", SLUG_SITES)
    plain, defaulted, alt = got["plain"], got["defaulted"], got["alternate_first"]
    assert plain["runs"]["unset"]["value"] is None
    assert plain["runs"]["whitespace"]["value"] == "   "
    assert "keeps-padding" in plain["modes"] and "prefers-CUSTOMER_SLUG" in plain["modes"]
    assert "fails-open-on-unset" in defaulted["modes"] and "strips-padding" in defaulted["modes"]
    assert "prefers-alternate" in alt["modes"]
    assert "raises-KeyError-on-unset" in alt["modes"]


def test_a_changed_row_is_named_by_the_diff() -> None:
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    edited = json.loads(json.dumps(table))
    row = edited["concepts"]["customer-yaml"]["rows"][0]
    row["modes"] = [*row.get("modes", []), "planted-mode"]
    found = differences(table, edited)
    assert len(found) == 1 and "planted-mode" in found[0] and row["file"] in found[0]
    assert differences(table, table) == []


@pytest.mark.parametrize(
    ("source", "kind", "assign"),
    [
        ('import os\nSLUG = os.environ.get("CUSTOMER_SLUG", "")\n', "module", "SLUG"),
        ('import os\nif __name__ == "__main__":\n    print(os.environ["CUSTOMER_SLUG"])\n', "script", None),
    ],
)
def test_module_level_reads_are_inline_sites(source: str, kind: str, assign: str | None) -> None:
    (site,) = sites_in("slug", "operator/x/y.py", source)
    assert isinstance(site, SiteSpec) and site.kind == kind and site.assign == assign and site.qualname is None


# ------------------------------------------------------------ real customer.yaml rows


def _real_files(repo: Path) -> list[str]:
    """Every operator/customers/<folder>/customer.yaml, found without the instrument's code."""
    root = repo / "operator" / "customers"
    return sorted(name for name in os.listdir(root) if os.path.isfile(os.path.join(root, name, "customer.yaml")))


def test_every_real_customer_yaml_is_an_input_of_every_executable_yaml_site() -> None:
    """The golden runs each yaml site over every real seat document the tree holds.

    It once held none: the child looked the real files up under its own sandbox,
    whose customer.yaml redirect sent Path.glob's lstat of each candidate to the
    absent input file, so every one read as missing and the real rows vanished
    without a difference to see.
    """
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    want = len(_real_files(REPO))
    assert want >= 1
    rows = [r for r in table["concepts"]["customer-yaml"]["rows"] if r["status"] != "not-executable"]
    assert rows
    for row in rows:
        got = [k for k in row["behaviour"] if k.startswith("real-")]
        assert len(got) == want, f"{row['file']}::{row['symbol']} has {len(got)} real rows, the tree holds {want}"


def test_a_planted_tree_with_real_seats_gets_a_row_per_seat(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    for name in ("seat-one", "seat-two", "seat-three"):
        seat = repo / "operator" / "customers" / name
        seat.mkdir(parents=True)
        (seat / "customer.yaml").write_text("customer_id: x\nmemory: {}\n", encoding="utf-8")
    (repo / "operator" / "customers" / "_template").mkdir()  # no customer.yaml of its own: not a seat
    got = _plant(tmp_path, "customer-yaml", YAML_SITES)["strict"]
    real = sorted(k for k in got["runs"] if k.startswith("real-"))
    assert len(real) == len(_real_files(repo)) == 3
    assert all(got["runs"][k]["outcome"] == "return" for k in real)


# ------------------------------------------------------------ every site's dependencies are installed


MISSING_MODULE = ("ModuleNotFoundError", "ImportError")


def _missing_module(row: dict) -> list[str]:
    """Where a row says a module was absent: an import that failed, or a call that raised it."""
    found = []
    if row["status"] == "not-executable" and any(m in str(row.get("reason")) for m in MISSING_MODULE):
        found.append(f"not-executable: {row['reason']}")
    found += [
        f"{i}: {b}"
        for i, b in (row.get("behaviour") or {}).items()
        if b.startswith(tuple("raise " + m for m in MISSING_MODULE))
    ]
    return found


def test_no_site_is_left_unrun_for_a_missing_module() -> None:
    """The capture environment carries every third-party module a site imports.

    httpx, lxml, pydantic and mcp are installed beside pytest in the
    operator-substrate job (pinned to requirements/jobs.txt). Before they were,
    six readers were recorded not-executable (their import failed) and a
    seventh, templates/lane-memory-caps-probe.py, raised ModuleNotFoundError
    from a function-level import on its good input, so none of them ran.
    """
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    missing = [
        f"{r['file']}::{r['symbol']} {hit}"
        for body in table["concepts"].values()
        for r in body["rows"]
        for hit in _missing_module(r)
    ]
    assert missing == []


IMPORTS_ABSENT = """
import yaml
import not_an_installed_module_for_the_capture


def load():
    return yaml.safe_load(open("/opt/data/customer.yaml"))
"""

CALLS_ABSENT = """
import yaml


def load():
    doc = yaml.safe_load(open("/opt/data/customer.yaml"))
    import not_an_installed_module_for_the_capture
    return doc
"""


@pytest.mark.parametrize("source", [IMPORTS_ABSENT, CALLS_ABSENT], ids=["at-import", "at-call"])
def test_a_missing_module_is_recorded_in_the_words_that_test_reads(tmp_path: Path, source: str) -> None:
    (spec,) = sites_in("customer-yaml", "operator/planted_tree/planted_site.py", source)
    repo = tmp_path / "repo"
    (repo / "operator" / "planted_tree").mkdir(parents=True)
    (repo / "operator" / "planted_tree" / "planted_site.py").write_text(source, encoding="utf-8")
    result = run_site(spec, repo, sys.executable)
    row = _row(spec, result, real_digests=False)
    assert _missing_module(row), row


# ------------------------------------------------------------ sites moved onto the shared readers


READER_SITES = """
import seat_config, broker_socket


def parses():
    return seat_config.load("/opt/data/customer.yaml", encoding="utf-8", on_missing="raise")


def parses_text(text):
    return seat_config.parse(text)


def asks_the_broker(payload):
    return broker_socket.request("/run/b.sock", payload, timeout=1.0)


def only_encodes(payload):
    return broker_socket.encode(payload)


def reads_the_slug():
    return seat_config.slug(on_missing="none")
"""


def test_a_site_that_calls_the_shared_reader_is_still_a_site() -> None:
    """Wave 2, B1: a migrated site keeps its row; only its call text changes."""
    found = {c: {s.qualname for s in sites_in(c, "operator/x/y.py", READER_SITES)} for c in CONCEPTS}
    assert found["customer-yaml"] == {"parses", "parses_text"}
    # the frame encoder alone opens no socket: a site that only encodes is not a client
    assert found["broker-socket"] == {"asks_the_broker"}
    assert found["slug"] == {"reads_the_slug"}
    assert all(file_matches(c, READER_SITES) for c in CONCEPTS)
    assert not file_matches("broker-socket", "import broker_socket\nbroker_socket.encode({})\n")


# ------------------------------------------------------------ what is recorded does not depend on the machine


class _Emitter:
    """A site's own object holding the interpreter's: a threading.Event, as the
    heartbeat emitter does."""

    def __init__(self, slug: str) -> None:
        self.slug = slug
        self.stop = threading.Event()


def test_a_standard_library_object_is_recorded_by_type_not_by_its_internals(monkeypatch) -> None:
    """The heartbeat emitter digested differently on two machines: threading.Condition
    gained a ``locked`` attribute in Python 3.14, and canon walked into it."""
    norm = Normaliser([])
    before = digest(_Emitter("seat-one"), norm)
    # The interpreter's internals change under the object (another Python release) ...
    original = threading.Condition.__init__

    def without_locked(self, lock=None):
        original(self, lock)
        self.__dict__.pop("locked", None)
        self.__dict__["_another_release_s_field"] = 1

    monkeypatch.setattr(threading.Condition, "__init__", without_locked)
    assert digest(_Emitter("seat-one"), norm) == before
    # ... while what the SITE put in its object still decides the digest (falsifier).
    assert digest(_Emitter("seat-two"), norm) != before


def test_a_retry_loop_s_wall_clock_count_is_recorded_as_retried() -> None:
    """The job ledger client retries a refused connect every 0.5 s for 10 s: 21
    connects idle, 20 under load. The table records that it retried, not how often."""
    assert _connects(1) == "1"
    assert _connects(20) == _connects(21) == "retried"
    run = {"outcome": "raise", "type": "JobLedgerError", "unix_connects": 20}
    assert _brief(run, False) == _brief({**run, "unix_connects": 21}, False) == "raise JobLedgerError connects=retried"
    assert _brief({**run, "unix_connects": 1}, False) == "raise JobLedgerError connects=1"
