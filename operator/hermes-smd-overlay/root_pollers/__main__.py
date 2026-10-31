"""``python -m root_pollers``: every root poll loop in one process (see the package docstring).

Launched by the Machine entrypoint as ROOT, through the same respawn loop the
appliers used (``smd_root_poller``). Signals are installed here, in the main
thread, because Python only delivers them there; each loop reads the shared
stop event instead of installing its own handlers.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("root_pollers")

#: 130, as each applier returned on a signalled stop: the Machine is going down,
#: and the entrypoint's respawn loop must not restart into a dying box.
EXIT_STOPPED = 130
RESTART_SECONDS = 5.0
JOIN_SECONDS = 30.0

Stop = Callable[[], bool]


def _config(stop: Stop) -> None:
    from config_applier.__main__ import build_loop

    build_loop().run_forever(stop=stop)


def _spec(stop: Stop) -> None:
    from spec_applier.__main__ import build_loop

    build_loop().run_forever(stop=stop)


def _skills(stop: Stop) -> None:
    from skills_applier.__main__ import build_loop

    build_loop(with_audit=True).run_forever(stop=stop)


def _intake(stop: Stop) -> None:
    from establish_intake.__main__ import serve

    serve(stop)


#: name -> (run, the log-level env var the loop's own ``main`` read).
POLLERS: dict[str, tuple[Callable[[Stop], None], str]] = {
    "config_applier": (_config, "SMD_APPLIER_LOG_LEVEL"),
    "spec_applier": (_spec, "SMD_SPEC_LOG_LEVEL"),
    "skills_applier": (_skills, "SMD_SKILLS_LOG_LEVEL"),
    "establish_intake": (_intake, "SMD_ESTABLISH_LOG_LEVEL"),
}


def supervise(name: str, run: Callable[[Stop], None], stopping: threading.Event) -> None:
    """Run one loop until ``stopping`` is set, restarting it when it ends on its own."""
    while not stopping.is_set():
        try:
            run(stopping.is_set)
        except SystemExit as exc:  # a builder's "required env var unset"
            logger.error("%s: startup failed: %s", name, exc)
        except Exception:  # noqa: BLE001 - one loop's crash must not end the other three
            logger.exception("%s: exited with an error", name)
        if stopping.is_set():
            break
        logger.warning("%s: loop ended; restarting in %ss", name, RESTART_SECONDS)
        stopping.wait(RESTART_SECONDS)
    logger.info("%s: stopped", name)


def main(argv: list[str] | None = None, *, pollers: dict[str, Any] | None = None) -> int:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    table = POLLERS if pollers is None else pollers
    for name, (_run, level_var) in table.items():
        logging.getLogger(name).setLevel(os.environ.get(level_var, "INFO"))

    stopping = threading.Event()

    def _request_stop(signum: int, _frame: Any) -> None:
        logger.info("root_pollers: received signal %s; stopping every loop", signum)
        stopping.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    threads = [
        threading.Thread(target=supervise, args=(name, run, stopping), name=name, daemon=True)
        for name, (run, _level) in table.items()
    ]
    for thread in threads:
        thread.start()
    logger.info("root_pollers: running %s", ", ".join(table))
    while not stopping.wait(1.0):
        pass
    for thread in threads:
        thread.join(JOIN_SECONDS)
    return EXIT_STOPPED


if __name__ == "__main__":
    sys.exit(main())
