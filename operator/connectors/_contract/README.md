# operator-records-contract (`records_contract`)

The one copy of what the matter-records reply and the records lane must agree on
(ADR 0102): the SMD review row and its digest (`review_row`), how staged files
split into replies (`batches`), and the facts block rendered from the page
finder's results (`facts`). The overlay and the lane both import it; neither keeps a copy.

Installed by `operator/templates/jobs-runtime.sh medchron` into the jobs-bundle
venv (`/opt/medchron/.venv`) and by `operator/templates/Dockerfile` into the
Hermes venv beside the overlay. RULE: stdlib only, no runtime dependency, ever;
`tests/test_no_dependencies.py` fails the moment one appears.
