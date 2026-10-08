"""Progress lines for the long stages, so a quiet log means a stall.

On the seat, extract read scanned pages through vision for 16 minutes without
writing a line while the job dir kept changing; anything tailing the log
(a person, a watcher) read that as a stall. A stage now says where it is:
every ``every`` items, and at least once every ``seconds`` while it works,
even inside one long item (a scanned document is many vision calls).

COUNTS ONLY. The log is SMD-visible; a line never carries a file name, a
matter name, or document text. ``line`` builds it from integers alone.
"""

from __future__ import annotations

import time
from typing import Callable

DEFAULT_SECONDS = 60.0


class Progress:
    def __init__(
        self,
        log: Callable[[str], None],
        stage: str,
        total: int,
        unit: str,
        *,
        every: int,
        seconds: float = DEFAULT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.log, self.stage, self.total, self.unit = log, stage, int(total), unit
        self.every, self.seconds, self.clock = max(1, int(every)), float(seconds), clock
        self.done = 0
        self.counts: dict[str, int] = {}
        self._last = clock()

    def line(self) -> str:
        extra = "".join(f", {int(v)} {k}" for k, v in self.counts.items())
        return f"[{self.stage}] {self.done}/{self.total} {self.unit}{extra}"

    def _emit(self) -> None:
        self.log(self.line())
        self._last = self.clock()

    def add(self, key: str, n: int = 1) -> None:
        """Count something inside the current item (a vision page, a
        failure) and speak if the stage has been quiet too long."""
        self.counts[key] = self.counts.get(key, 0) + n
        self.beat()

    def beat(self) -> None:
        if self.clock() - self._last >= self.seconds:
            self._emit()

    def step(self, n: int = 1) -> None:
        """One more item finished."""
        before = self.done
        self.done += n
        if self.done // self.every != before // self.every or self.done >= self.total:
            self._emit()
        else:
            self.beat()

    def finish(self) -> None:
        if self.done < self.total or not self.total:
            self.done = max(self.done, self.total)
        self._emit()
