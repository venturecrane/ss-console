"""The overlay's directory name is load-bearing for this suite (2026-10-22 fold).

pytest resolves a test module's import name by walking up through directories that
carry an ``__init__.py`` and stops at the first whose name is not a Python
identifier (``_pytest.pathlib.resolve_package_path``). This tree is itself a
package (the root ``__init__.py`` is the Hermes fan-out plugin), so the name of
the directory it lives in decides where the walk stops:

* ``hermes-smd-overlay`` (hyphens, not an identifier): the walk stops here, the
  root goes on ``sys.path``, ``tests/test_x.py`` imports as ``tests.test_x`` and
  ``from tests.conftest import ...`` means THIS ``tests`` package. The standalone
  repo had this property by accident of its name.
* ``overlay`` (an identifier): the walk continues into ``operator/``, the same
  file also imports as ``overlay.tests.test_x`` (a second execution of every
  module-level ``load_plugin``), and ``tests`` resolves to ss-console's
  ``operator/tests``. 116 files break at collection. Observed before the fold
  landed; this test is the reason the directory is named for the plugin.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_overlay_directory_name_is_not_a_python_identifier() -> None:
    assert not _ROOT.name.isidentifier(), (
        f"the overlay directory is named {_ROOT.name!r}; pytest's package walk would "
        "continue past it into operator/ and every tests.conftest import resolves to "
        "ss-console's operator/tests (see this file's docstring)"
    )


def test_the_root_is_the_plugin_package_the_walk_has_to_stop_at() -> None:
    # The property above only matters because these two files exist; if either
    # goes, the test above is protecting nothing and should be re-examined.
    assert (_ROOT / "__init__.py").is_file()
    assert (_ROOT / "tests" / "__init__.py").is_file()
