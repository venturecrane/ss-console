"""The safety substrate: the code-level floor under every Operator seat.

A package since 2026-09-14 (packaging stage 4). Its modules import each other
and `adapter.*` by package name; on the seat, bootstrap.sh and entrypoint.sh
run the invariant checks with `PYTHONPATH=/app`, which is where this tree and
`adapter/` are copied (operator/templates/Dockerfile). In CI the tree is
installed editable; a bare local pytest gets the root from
`pythonpath = .` in operator/pytest.ini.
"""
