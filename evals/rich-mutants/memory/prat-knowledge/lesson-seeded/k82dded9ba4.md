+++
id = "k82dded9ba4"
kind = "pitfall"
summary = "Every card that touches rich/ needs a new bullet under '## Unreleased' in CHANGELOG.md"
paths = ["CHANGELOG.md", "tools/check_changelog.py", "CONTRIBUTING.md"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:24+00:00"
last_verified = "2026-11-13T05:41:24+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
check_changelog.py parses the file literally. The heading must be exactly '## Unreleased' and the entry must be a new line starting with '- ' (it can go under a '### Fixed/Changed/Added' subheading). Rewording an existing bullet does not count. Use Keep a Changelog style, as in the existing entries.
