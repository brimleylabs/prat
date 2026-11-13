# Rich cases

Three bugs planted in [Rich](https://github.com/Textualize/rich) (MIT licence, copyright
Will McGugan and contributors), each a one-line change in a different part of the library:

| Case | Planted in | Symptom given to the agent |
| --- | --- | --- |
| filesize-boundary | `rich/filesize.py` | a file of exactly 1,000 bytes is shown as bytes, not kB |
| ellipsis-width | `rich/text.py` | text cut short with an ellipsis is one character too wide |
| ratio-columns | `rich/_ratio.py` | table columns with ratios do not fill the table |

Each case starts from the same Rich commit (`[source]` in `case.toml`), with no history. The
files in `repo/` are laid over it: the planted bug, and Rich's test files without the tests
that catch it. `hidden/` holds those test files as Rich ships them; the hidden check runs the
whole test suite (about 1,000 tests, a few seconds) with them restored.

The files under `repo/` and `hidden/` are Rich's own, changed only as described above.
