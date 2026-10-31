"""The root poll loops of a seat, run in one interpreter (ADR 0100).

A seat runs four root poll loops: the config applier, the spec applier, the
skills applier and the establishment intake. Each was its own Python process,
about 55-67 MB apiece, on a 1 GB seat where the gateway alone holds ~400 MB
(2026-10-31: 70-90 MB available on scott and brokery after the job runner was
gated off). They share an interpreter, the R2 client and the audit client, so
one process holds all four for roughly the cost of one.

Each loop keeps its own module, builder and tests; this package only runs them.
A loop that ends without a stop request (a startup refusal, an unexpected
error) is restarted after 5 s on its own thread, exactly as the shell loop
restarted the process it used to be, and the other three keep running. A stop
request (SIGTERM/SIGINT, the Machine going down) ends every loop and the
process exits 130, so the entrypoint's respawn loop does not restart it.

The boot fetches (``python -m spec_applier --once``, ``python -m
skills_applier --once``) stay separate, synchronous, short-lived processes:
they run before the privilege drop and must finish before bootstrap reads what
they installed.
"""
