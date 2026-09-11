"""medchron: the chronology-package runner (routine 11, ADR 0087, ss#2613).

A fixed-DAG driver over the medical-chronology pipeline. PR 1 is the strangler
shape: the driver owns order, state, spend, and the decisions a person used to
make, and invokes the frozen pipeline scripts as subprocesses. Stages move
in-process one seam at a time in later PRs; the argv contract each stage keeps
is what lets the driver flip a stage without changing the DAG.

Nothing in this package names a firm, a client, a provider, or a matter. Every
such fact arrives through the per-firm config (private) or the job envelope.
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
