+++
id = "k5ee74cf35b"
kind = "decision"
summary = "The protocol and pretty pages document the extension hooks (__rich__, __rich_console__, __rich_measure__, __rich_repr__) as the supported way to customize rendering."
paths = ["docs/source/protocol.rst", "docs/source/pretty.rst"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T12:13:41+00:00"
last_verified = "2026-11-13T12:20:41+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T121106"
+++
protocol.rst describes __rich__ (returns one renderable, and a string is treated as markup), __rich_console__ (a generator of renderables or Segments) and __rich_measure__ (returns a Measurement with a minimum and a maximum). pretty.rst covers __rich_repr__ yield forms, the `angular` attribute and the rich.repr.auto decorator. Third-party classes can use these hooks without making Rich a dependency.
