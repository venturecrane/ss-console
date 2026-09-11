"""In-process stages. Each module exposes `run(sr: StageRun) -> int` (or a
named pair, as msg.py does); dag.py binds them by name."""
