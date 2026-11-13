+++
id = "kd7cf4be7a8"
kind = "decision"
summary = "Some pages restrict `:members:` to named items"
paths = ["docs/source/reference/text.rst", "docs/source/reference/traceback.rst", "docs/source/reference/logging.rst", "docs/source/reference/emoji.rst", "docs/source/reference/syntax.rst"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:41+00:00"
last_verified = "2026-11-13T12:13:41+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
Several pages list only specific members: emoji (`Emoji`), panel (`Panel`), syntax (`Syntax`), theme (`Theme`), logging (`RichHandler`), text (`Text, TextType`) and traceback (`Traceback, install`). The rest use bare `:members:` and document everything with a docstring. If you add a public class to one of the restricted modules, add it to the list or it won't appear in the docs.
