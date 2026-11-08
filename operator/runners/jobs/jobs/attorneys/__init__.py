"""The firm's attorney roster, as a firm input (design 2026-11-06, "who an attorney is").

* ``roster``: the reader, byte for byte the connector toolkit's
  ``operator_connector_sdk/library/roster.py`` (the runtime readers import that
  one; this copy lets the firm-inputs publish validate a roster with the runner
  alone on its path).
* ``firm``: the validator, ``python -m jobs.attorneys.firm <dir>``, run by the
  publish (``operator/bin/lib/publish-firm-inputs.sh``) and by the seat's
  firm-inputs applier before a roster is swapped in.
* ``agree``: the seat check that counts each way a lane's own attorney list
  disagrees with an installed roster, so SMD is paged and nothing differs silently.
"""
