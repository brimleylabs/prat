"""Rich's changelog rule (CONTRIBUTING.md): every change under rich/ is described in
CHANGELOG.md, in a new entry under "## Unreleased".

    python tools/check_changelog.py [BASE_CHANGELOG]

Compares CHANGELOG.md with BASE_CHANGELOG, or with CHANGELOG.md on the branch this work
started from (git merge-base with main).
"""

import subprocess
import sys
from pathlib import Path


def unreleased_entries(text):
    entries, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line.strip().lower() == "## unreleased"
            continue
        if inside and line.strip().startswith("- "):
            entries.append(line.strip())
    return entries


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8")


def main():
    if len(sys.argv) > 1:
        base = Path(sys.argv[1]).read_text(encoding="utf-8")
        changed_code = True
    else:
        fork = git("merge-base", "HEAD", "main").stdout.strip() or "HEAD"
        base = git("show", f"{fork}:CHANGELOG.md").stdout
        changed = git("diff", "--name-only", fork, "--", "rich/").stdout.split()
        dirty = git("status", "--porcelain", "--", "rich/").stdout.split()
        changed_code = bool(changed or dirty)
    if not changed_code:
        print("no change under rich/: nothing to describe")
        return 0
    now = unreleased_entries(Path("CHANGELOG.md").read_text(encoding="utf-8"))
    before = unreleased_entries(base)
    if len([e for e in now if e not in before]) == 0:
        print("CHANGELOG.md: this change under rich/ has no new entry under ## Unreleased.")
        print("Rich asks for every change to be described there (see CONTRIBUTING.md).")
        return 1
    print("changelog ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
