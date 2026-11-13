+++
id = "k73fb6d5f7d"
kind = "layout"
summary = "docs/source: docs/source holds the Sphinx reStructuredText sources for Rich's user guide: one page per feature (console, style, markup, text, pretty, logging, traceback, prompt, columns, gro"
paths = ["docs/source"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:40+00:00"
last_verified = "2026-11-13T12:20:41+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
docs/source holds the Sphinx reStructuredText sources for Rich's user guide: one page per feature (console, style, markup, text, pretty, logging, traceback, prompt, columns, group, markdown, padding, panel, progress, syntax, tables, tree, live, layout, protocol), plus the reference.rst and appendix.rst entry points. docs/source/index.rst holds the master toctree, and every page must be listed there to be built. conf.py configures Sphinx with the sphinx_rtd_theme, autodoc, napoleon, intersphinx, autosectionlabel, viewcode and sphinx_copybutton. The guide pages are hand-written prose with embedded code samples. The API pages live in docs/source/reference/*.rst and are pulled in through reference.rst, and appendix.rst pulls in docs/source/appendix/box.rst and colors.rst. Pages link to the library's classes with Sphinx roles such as :class:`~rich.console.Console` and to each other with explicit labels.
