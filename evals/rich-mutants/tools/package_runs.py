"""Packages benchmark evaluations for publishing (see ../ADDENDUM-1.md, "Raw data").

    python package_runs.py EVALS_DIR PREFIX OUT_DIR REPLACE=WITH [REPLACE=WITH ...]

Copies result.json of every evaluation whose label starts with PREFIX into
OUT_DIR/results/<label>.json, and its journals into OUT_DIR/journals-<PREFIX>.tar.gz, after
replacing each REPLACE (this machine's folders, user name and the like) with WITH. Both
slash styles and JSON-escaped backslashes are replaced. Fails if any REPLACE survives.
"""

from __future__ import annotations

import io
import json
import sys
import tarfile
from pathlib import Path


def variants(text: str) -> list[str]:
    forward = text.replace("\\", "/")
    back = forward.replace("/", "\\")
    return sorted({text, forward, back, back.replace("\\", "\\\\")}, key=len, reverse=True)


def scrub(data: str, pairs: list[tuple[str, str]]) -> str:
    for old, new in pairs:
        for form in variants(old):
            data = data.replace(form, new)
            data = data.replace(form.lower(), new)
    return data


def main() -> None:
    evals, prefix, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    pairs = [tuple(arg.split("=", 1)) for arg in sys.argv[4:]]
    (out / "results").mkdir(parents=True, exist_ok=True)
    archive_path = out / f"journals-{prefix.rstrip('-')}.tar.gz"
    count = 0
    with tarfile.open(archive_path, "w:gz") as archive:
        for folder in sorted(evals.iterdir()):
            result_file = folder / "result.json"
            if not result_file.is_file():
                continue
            label = json.loads(result_file.read_text(encoding="utf-8")).get("label", "")
            if not label.startswith(prefix):
                continue
            text = scrub(result_file.read_text(encoding="utf-8"), pairs)
            (out / "results" / f"{label}.json").write_text(text, encoding="utf-8", newline="\n")
            for journal in sorted((folder / "journals").rglob("*.jsonl")):
                data = scrub(journal.read_text(encoding="utf-8"), pairs).encode("utf-8")
                info = tarfile.TarInfo(f"{label}/{journal.relative_to(folder / 'journals').as_posix()}")
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            count += 1
    # Nothing to be replaced may be left anywhere.
    leftovers = []
    for file in (out / "results").glob("*.json"):
        text = file.read_text(encoding="utf-8")
        leftovers += [f"{file.name}: {old}" for old, _ in pairs if any(v in text for v in variants(old))]
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            text = archive.extractfile(member).read().decode("utf-8")
            leftovers += [f"{member.name}: {old}" for old, _ in pairs if any(v in text for v in variants(old))]
    if leftovers:
        sys.exit("not scrubbed:\n" + "\n".join(leftovers[:20]))
    print(f"{count} evaluations packaged; journals in {archive_path.name} "
          f"({archive_path.stat().st_size // 1024} KB)")  # fmt: skip


if __name__ == "__main__":
    main()
