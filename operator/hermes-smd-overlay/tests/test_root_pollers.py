"""root_pollers: the four root poll loops in one process (ADR 0100)."""

from __future__ import annotations

import os
import signal
import threading
import time
from pathlib import Path

from root_pollers import __main__ as rp

OVERLAY = Path(__file__).resolve().parents[1]
TEMPLATES = OVERLAY.parent / "templates"


def _stop_soon(after: float) -> threading.Timer:
    timer = threading.Timer(after, lambda: os.kill(os.getpid(), signal.SIGTERM))
    timer.start()
    return timer


def test_a_stop_ends_every_loop_and_exits_130() -> None:
    seen: dict[str, int] = {}

    def loop(name: str):
        def run(stop):
            while not stop():
                seen[name] = seen.get(name, 0) + 1
                time.sleep(0.01)

        return run

    table = {n: (loop(n), "UNUSED_LEVEL_VAR") for n in ("a", "b")}
    _stop_soon(0.3)
    assert rp.main(pollers=table) == rp.EXIT_STOPPED
    assert seen["a"] > 0 and seen["b"] > 0


def test_one_loop_that_crashes_restarts_and_the_others_keep_running(monkeypatch) -> None:
    """FALSIFIER: drop the try/except in supervise and the crash ends the whole thread."""
    monkeypatch.setattr(rp, "RESTART_SECONDS", 0.05)
    starts = {"crash": 0, "steady": 0}

    def crash(stop):
        starts["crash"] += 1
        raise RuntimeError("tick blew up")

    def startup_refused(stop):
        raise SystemExit("required env var unset")

    def steady(stop):
        starts["steady"] += 1
        while not stop():
            time.sleep(0.01)

    table = {
        "crash": (crash, "X"),
        "refused": (startup_refused, "X"),
        "steady": (steady, "X"),
    }
    _stop_soon(0.5)
    assert rp.main(pollers=table) == rp.EXIT_STOPPED
    assert starts["crash"] >= 3
    assert starts["steady"] == 1


def test_the_table_is_the_four_root_loops_and_each_one_imports() -> None:
    from config_applier import __main__ as config_main
    from establish_intake import __main__ as intake_main
    from skills_applier import __main__ as skills_main
    from spec_applier import __main__ as spec_main

    assert set(rp.POLLERS) == {
        "config_applier",
        "spec_applier",
        "skills_applier",
        "establish_intake",
    }
    for builder in (
        config_main.build_loop,
        spec_main.build_loop,
        skills_main.build_loop,
        intake_main.serve,
    ):
        assert callable(builder)
    # `python -m <loop>` keeps working on its own: tests and a hand run on a seat use it.
    for mod in (config_main, spec_main, skills_main, intake_main):
        assert callable(mod.main)


def test_the_entrypoint_launches_one_root_poller_not_four() -> None:
    entry = (TEMPLATES / "entrypoint.sh").read_text(encoding="utf-8")
    supervisor = (OVERLAY.parent / "runners/supervisor/entrypoint-supervisor.sh").read_text(
        encoding="utf-8"
    )
    code = "\n".join(
        line
        for text in (entry, supervisor)
        for line in text.splitlines()
        if not line.lstrip().startswith("#")
    )
    assert code.count("smd_root_poller root_pollers") == 1
    for gone in (
        "smd_root_poller config_applier",
        "smd_root_poller spec_applier",
        "smd_root_poller skills_applier",
    ):
        assert gone not in code
    assert "-m establish_intake" not in code
    # The boot fetches stay their own synchronous processes, before the drop.
    assert "-m spec_applier --once" in code and "-m skills_applier --once" in code
