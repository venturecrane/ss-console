"""The $0 replay of the discovery-response path (``python -m jobs.drafting.response_replay``).

Before a firm's Operator drafts discovery responses for a real request, SMD
replays the whole path on a saved matter in the client's ops workspace and
compares what the code finds, scopes, projects and reads with an answer key
built from the hand drafts (design ``operator-drafts-discovery-responses``,
section F). No model is called: a model call is answered by a recording beside
the key (``recorded``), or the stage stops and says it needs one.

* ``recorded``: the recorded client and the doorway the stages are handed.
* ``key``: the answer key, read tolerantly.
* ``stages``: the stage entry points, imported lazily from the modules the
  response builders add (``EXPECTED`` names every one).
* ``compare``: key against what a stage wrote; pure, no I/O.
* ``body``: a produced response body split into numbered responses.
"""
