+++
id = "kf6f23da0a8"
kind = "convention"
summary = "Question files are named `<slug>.question.md` and use YAML front matter"
paths = ["questions/README.md", "questions/square_brackets.question.md", "questions/logging_color.question.md"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:14+00:00"
last_verified = "2026-11-13T05:41:14+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
README.md requires the `.question.md` extension. Each file starts with `---` front matter holding a `title` (phrased as the user's question or symptom) and, usually, `alt_titles` as a list. The answer is a few short paragraphs of markdown below the front matter.
