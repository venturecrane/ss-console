"""The one reader and the one downloader of a firm's files for every jobs lane (wave 5): ``policy``
(one row per lane), ``pdf`` (the text layer and page images), ``readable`` (which pages have usable
text), ``word`` (the two declared Word forms), ``paid`` (a page read by a model), ``store`` (paid page
text kept once per file, page and method) and ``pull`` (the one downloader). Nothing is imported
here: import the module you use."""
