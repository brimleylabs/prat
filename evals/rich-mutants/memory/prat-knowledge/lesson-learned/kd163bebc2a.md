+++
id = "kd163bebc2a"
kind = "pitfall"
summary = "Translated READMEs mirror README.md but are not all in sync; leave them alone unless asked"
paths = ["README.cn.md", "README.de-ch.md", "README.de.md", "README.es.md", "README.fa.md", "README.fr.md", "README.hi.md", "README.id.md"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:41:06+00:00"
last_verified = "2026-11-13T05:41:06+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053836"
+++
The READMEs README.cn.md, de-ch, de, es, fa, fr, hi and id (plus others like kr, ja, ru and tr) are translations of README.md with the same sections: print, REPL, Console, inspect, and collapsible renderable sections. They link to images in `imgs/` via absolute GitHub `main` URLs. Several still say Rich needs Python 3.6.3 (de, de-ch, fr, id and others), while es says 3.8. Some end with `<!-- This is a test, no need to translate -->`. Don't copy version claims between them or treat them as the source of truth.
