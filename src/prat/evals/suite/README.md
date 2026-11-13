# PRAT's evaluation suite

Six small cases, each a Python module, a card and a hidden check. Run them with
`prat eval run`. The scripted attempts and verdicts are written so that every outcome the
harness reports appears at least once: right the first time (greet), caught by a gate
(slugify), caught by the reviewer (duration), missed by everyone (roman), good work sent
back (clamp) and bad work never handed over (chunks). The numbers from a scripted run
show the harness works; run `prat eval run --runtime claude` to measure an agent.

See docs/GUIDE.md, "Evaluating agents", for the case format.
