"""What the paid-call doorway (``llm.py``) reads from a lane's firm config.

The doorway needs three things from a firm config: the authored model tiers
(``models.tiers``), the levers (``levers.cache``, ``levers.network_retry_s``)
and the stages the firm batches (``levers.batch_stages``). It used to import
the chronology's ``FirmConfig`` for them, which put a lane inside the runner
core. This protocol is the shape instead; the chronology's ``FirmConfig`` is
one, and the core imports no lane (``tests/test_runner_core_imports_no_lane.py``).
"""

from __future__ import annotations

from typing import Any, Protocol


class FirmLevers(Protocol):
    def get(self, section: str, key: str, default: Any = None) -> Any: ...

    @property
    def batch_stages(self) -> list[str]: ...


__all__ = ["FirmLevers"]
