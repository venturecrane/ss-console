"""The seat-config caller table (wave 2, Phase A, item A2).

Every place that hand-parses customer.yaml, talks to the broker socket, or
reads which seat it is (CUSTOMER_SLUG) is EXECUTED over a fixed input set, and
what it returned or raised is recorded in operator/contracts/
seat-config-callers.json. Nothing here classifies a site by reading its code:
discovery finds where the sites are, the child runs them, and modes.py derives
each row's mode labels from the recorded behaviour alone.

The entry point is operator/bin/capture-seat-config-callers.py; the golden test
is operator/bin/tests/test_seat_config_callers.py.
"""
