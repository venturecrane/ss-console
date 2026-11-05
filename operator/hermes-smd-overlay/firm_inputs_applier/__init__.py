"""Root firm-inputs applier: a firm's lane configuration adopted live from R2.

WHAT IT IS FOR. Each job lane reads its firm's configuration (the demand,
drafting, litigation, case file and records inputs, and the chronology lane's
``medchron-firm.yaml``) from ``/var/lib/smd-config``. Before this applier the
only way a published change reached a seat was a boot (the entrypoint
fragments fetch once), so every correction to a firm's lane configuration cost
a restart or a reprovision. This loop polls the vault and adopts a published
change on a running seat within a poll interval, validated before it is used.

THE SHAPE (shared with the boot fragments in operator/templates). Each lane's
live path is a SYMLINK to a versioned copy named for the first 8 hex of its
marker object's ETag: ``/var/lib/smd-config/records -> records.v1a2b3c4d/`` and
``/var/lib/smd-config/medchron-firm.yaml -> medchron-firm.v1a2b3c4d.yaml``. A
change is staged whole into a new version, validated by the runner's own
validator from the jobs bundle venv (``python -m jobs.<lane>.firm <dir>``,
``jobs.medchron.config.load`` for the chronology), owned root:jobs 0750/0640,
its broker copy refreshed, and only then committed by an atomic rename of the
symlink. A runner job resolves the symlink once (``os.path.realpath`` in the
lane's ``resolve_dir``), so a job already running keeps the version it started
on; the newest three versions are kept on disk.

WHAT IT NEVER DOES.

* It never swaps in a copy the validator refused. A refused version is removed,
  the live symlink stays where it was, the lane's verdict reads ``rejected``
  in ``/run/smd-firm-inputs/state.json`` (world-readable: the hermes-run
  heartbeat ships it) with the validator's first line root-only beside it in
  ``<lane>.rejected``, and that ETag is not retried until the vault's ETag
  changes again.
* It never touches a lane this seat does not author. The authored set is the
  boot's ``SMD_AUTHORED_LANES`` (entrypoint-lanes.sh). A lane newly authored in
  customer.yaml still needs a boot, which builds the lane's dirs and threads.
* It never removes a lane's inputs because the vault no longer holds them; a
  removal reaches the seat at its next boot.
"""
