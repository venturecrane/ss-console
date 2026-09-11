"""Makes this directory's shared helpers importable by their plain module name.

Under the CI invocation (one pytest run over ten directories, importlib
import mode; see operator-substrate.yml and pytest.ini) the package name
``tests`` does not reliably mean this directory: ``bin/tests/test_chain_rehearsal.py``
puts ``workspace_broker/`` at the front of ``sys.path`` for its own imports,
and ``workspace_broker/tests`` answers to ``tests`` from then on. That is how
``from tests.vendored_sync import ...`` collected green in isolation and failed
under the full run (2026-09-11, #2768).

So a shared helper in this directory is imported by its own name
(``from vendored_sync import ...``), and this conftest is what puts the
directory on the path before pytest imports the test modules beside it.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
