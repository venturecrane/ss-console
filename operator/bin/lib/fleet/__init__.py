"""`fleet`: build one Operator image per release and roll it out in rings.

THE CONTRACT (plan zesty-gathering-axolotl, 2026-10-19). An SMD agent runs
``fleet build`` once, then ``fleet release <release-id>``; every seat lands on
the new release with no per-seat build, no running job or answer killed, a seat
that truly fails auto-rolled-back with the rollout halted, and ``fleet status``
shows what each seat runs. Skill-only changes reach seats through the same rings
with no restart (``fleet release --skills <version>``).

A release is a pair: an image digest (built once into the Fly registry app
``hermes-fleet``) and a skills version (a content-addressed manifest CI
publishes to R2). D1 holds the record: ``fleet_releases``, ``fleet_rollouts``,
``fleet_holds``, ``fleet_lease`` and the heartbeat-fed ``fleet_status``.

Layout:
  cli        argparse entry (``operator/bin/fleet`` execs ``python -m bin.lib.fleet``)
  runner     the one subprocess seam; every external call goes through it
  d1, r2, fly  thin adapters over wrangler, the aws CLI and flyctl
  rings      ring membership (operator/fleet/rings.yaml) and holds
  waves      lease + ring iteration + halt, shared by image and skills releases
  busy       the live busy gate (Machines-API exec probe that works on old images) + quiesce
  preflight  per-seat checks before anything is touched
  rollout    one seat's image release: snapshot, deploy, smoke, read-back, rollback
  skills     one seat's skills release: capability gate, pointer, read-back
  tick       ``fleet release --tick``: one resumable scheduler step, the bake (ADR 0096)
  ledger     one verify-ledger row per judged seat (crane-context POST /verify)
  build      ``fleet build``
  status     ``fleet status`` / ``releases`` / ``hold`` / ``unhold``

Nothing here prints a secret: the adapters pass credentials through the
environment of the child process only, and ``fly secrets list`` is read for
NAMES.
"""
