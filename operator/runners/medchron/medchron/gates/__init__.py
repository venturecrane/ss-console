"""The four registered content controls (ADR 0087, `runtime-controls.yaml`
rows `medchron_*`), each a thin module over the stage that enforces it, so
the registry can name one file per control and a probe can target it.

  claim_audit   every live claim finally audited and finally SUPPORTED
  extractive    non-record pages leave the exhibits; the strip's remap is
                verified by content, or nothing is written
  cross_client  a file whose text names the other unit's client more than
                its own is flagged before composition
  provenance    every pulled file is cited or explained, or the run holds
"""
