# Addendum 2: what the builder did before its first edit

Written on 2026-11-23, after all 70 runs were finished and their results had been read.

The protocol measures the whole build. On the coverage.py ticket that total came out about level
with and without a fact pack, which says nothing about where the time went. A fact pack is meant
to save the builder's looking around before it starts, so we went back to the journals of the ten
PRAT runs on that case and counted, for the builder whose work was kept:

- the tool calls made before the first Edit or Write;
- the seconds from the start of the build to that first edit;
- the seconds from that edit to the end of the build;
- all its tool calls.

`first_edit.py` does the counting; its output is in
[first-edit/windows-coverage.json](first-edit/windows-coverage.json).

This is a look back at runs already made. The measures were chosen after seeing the results, so
they are weaker evidence than the totals the protocol named beforehand, and RESULTS.md marks them
as such. No run was added, dropped or repeated for it.
