"""The negotiation watch: keep each open matter's Negotiation Details tab current
from the offer letters and emails saved to it, and tell the firm about each new
offer.

One scheduled job (``medchron negotiate <job_dir>``) per weekday slot:

* ``arrivals`` (lane ``negotiation``) names the documents saved to each open
  matter since the lane last looked; a matter the lane has never seen is SEEDED
  (its cursor set to the current file set) and nothing on it is read;
* ``select`` keeps the offer-related ones by file name (a coverage gap the skill
  doc names: a generically named scan is not read);
* ``extract`` reads ONE document, with the matter's current rows as context,
  through the doorway (structured output, the schema the firm approved);
* ``rows`` builds the rows the firm approved (our demand paired with the offer
  that answered it; an unconfirmed amount enters as a date only);
* the connector's ``add_negotiation_rows`` writes them with its read-back;
* each new OFFER becomes one notice: the broker turns it into one email to the
  firm's authored scheduled recipient.

Product code: nothing here names a firm, a matter or a client.
"""
