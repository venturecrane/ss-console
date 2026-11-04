"""Evidence-first drafting: code, never the model, owns every cite, quote,
figure, date and aggregate a brief carries.

The model reads the record a window at a time and proposes facts
(``extract``); code verifies each against the page it names and assigns the
id (``verify``); thin and form pages behind a signed or verified claim are
read as images (``images``); the drafter composes prose over fact tokens only
(``markup``), code refuses anything hand-typed (``lint``), and code fills
every cite, quote and number last (``render``), proving every rendered span
traces to a verified fact (``render.assert_spans``). ``shapes`` is the
contract the other workstreams build against.
"""
