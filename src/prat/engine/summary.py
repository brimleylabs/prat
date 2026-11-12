"""Run summaries built from the actual diff, never from the agent's claims."""

from __future__ import annotations

from prat.git.worktree import DiffStats

MAX_FILES_LISTED = 50


def render_summary(stats: DiffStats) -> str:
    if stats.empty:
        return f"No changes between {stats.base[:10]} and {stats.head[:10]}."
    lines = [
        f"{len(stats.files)} file(s) changed, +{stats.insertions} -{stats.deletions} "
        f"({stats.base[:10]}..{stats.head[:10]})",
    ]
    if stats.commits:
        lines += ["", "Commits:"]
        lines += [f"- {sha[:10]} {subject}" for sha, subject in reversed(stats.commits)]
    lines += ["", "Files:"]
    for changed in stats.files[:MAX_FILES_LISTED]:
        counts = "binary" if changed.binary else f"+{changed.added} -{changed.deleted}"
        lines.append(f"- {changed.status} {changed.path} ({counts})")
    hidden = len(stats.files) - MAX_FILES_LISTED
    if hidden > 0:
        lines.append(f"- ... and {hidden} more")
    return "\n".join(lines)
