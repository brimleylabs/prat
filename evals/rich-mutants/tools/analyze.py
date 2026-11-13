"""Summarises benchmark evaluations (see ../PROTOCOL.md).

    python analyze.py EVALS_DIR PREFIX OUT_JSON

Reads every stored evaluation under EVALS_DIR whose label starts with PREFIX. A label is
PREFIX + setup + "-r" + round (for example "bench-prat-seeded-r2"). Prints Markdown tables and
writes every run's figures and the means to OUT_JSON.

How far an agent searched before its first change to a file: for PRAT, the builder's tool
calls as the policy saw them; for plain Claude Code, its tool calls in the session journal.
A shell command that edits a file in place (sed -i and the like) counts as a change.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

READ_TOOLS = {"Read", "Grep", "Glob", "Bash"}
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL_EDIT = re.compile(r"\bsed\s+-i|\bperl\s+-p?i|cat\s*>|\btee\s|>\s*[\w./-]+\.(py|md)\b")
SETUP_ORDER = ["plain", "plain-md", "plain-md-learned", "prat", "prat-seeded", "prat-learned"]


def ts(row: dict) -> datetime:
    return datetime.fromisoformat(row["ts"])


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def plain_search(journal: Path) -> tuple[int | None, float | None]:
    data = rows(journal)
    start, looks = ts(data[0]), 0
    for row in data:
        if row["kind"] != "plain.AssistantMessage":
            continue
        for block in row["data"].get("content") or []:
            if not isinstance(block, dict) or not block.get("name"):
                continue
            command = (block.get("input") or {}).get("command") if block["name"] == "Bash" else None
            if block["name"] in WRITE_TOOLS or (command and SHELL_EDIT.search(command)):
                return looks, (ts(row) - start).total_seconds()
            if block["name"] in READ_TOOLS:
                looks += 1
    return looks, None


def prat_search(journal: Path) -> tuple[int | None, float | None, int]:
    data = rows(journal)
    start = next((ts(r) for r in data if r["kind"] == "execute.model"), ts(data[0]))
    given = sum(
        len(r["data"].get("ids") or [])
        for r in data
        if r["kind"] == "knowledge" and r["data"].get("role") == "builder"
    )
    looks = 0
    for row in data:
        if row["kind"] != "policy" or row["data"].get("agent") != "builder":
            continue
        call = row["data"].get("call", {})
        kind = call.get("kind")
        if kind == "file_write" or (kind == "bash" and SHELL_EDIT.search(call.get("command") or "")):
            return looks, (ts(row) - start).total_seconds(), given
        if kind in ("file_read", "bash"):
            looks += 1
    return looks, None, given


def runs_of(evals: Path, prefix: str) -> list[dict]:
    found = []
    for folder in sorted(evals.iterdir()):
        result_file = folder / "result.json"
        if not result_file.is_file():
            continue
        result = json.loads(result_file.read_text(encoding="utf-8"))
        label = result.get("label", "")
        if not label.startswith(prefix):
            continue
        match = re.fullmatch(r"(.+)-r(\d+)", label[len(prefix) :])
        if match is None:
            continue
        setup, round_ = match.group(1), int(match.group(2))
        for case in result["cases"]:
            run = {"setup": setup, "round": round_, "case": case["name"], "evaluation": result["id"]}
            if case.get("error") or not case["attempts"]:
                found.append({**run, "error": case.get("error") or "no attempt"})
                continue
            attempts = case["attempts"]
            last = attempts[-1]
            handed_over = last["card_status"] == "review"
            passes = bool((last.get("check") or {}).get("passed"))
            journal = folder / "journals" / case["name"] / "attempt-1.jsonl"
            if result["runtime"] == "plain":
                looks, first, given = (*plain_search(journal), 0) if journal.is_file() else (None, None, 0)
            else:
                looks, first, given = prat_search(journal) if journal.is_file() else (None, None, 0)
            found.append(
                {
                    **run,
                    "handed_over": handed_over,
                    "passes": handed_over and passes,
                    "bad_handed_over": handed_over and not passes,
                    "first_attempt_passes": bool((attempts[0].get("check") or {}).get("passed")),
                    "runs": len(attempts),
                    "gate_failures": sum(1 for a in attempts if any(not g["passed"] for g in a["gates"])),
                    "reworks": sum(1 for a in attempts if a["card_status"] == "rework"),
                    "seconds": round(sum(a["duration_seconds"] for a in attempts), 1),
                    "first_edit_seconds": None if first is None else round(first, 1),
                    "looks_before_first_edit": looks,
                    "knowledge_entries_given": given,
                    "policy_denials": sum(a["policy"]["denied"] for a in attempts),
                    "policy_decisions": sum(a["policy"]["calls"] for a in attempts),
                    "cost_usd": round(sum(a["cost_usd"] for a in attempts), 4),
                }
            )
    return found


def mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return round(statistics.mean(values), 1) if values else None


def summary(runs: list[dict]) -> dict:
    ok = [r for r in runs if "error" not in r]
    return {
        "runs": len(ok),
        "errors": len(runs) - len(ok),
        "passes": sum(r["passes"] for r in ok),
        "pass_rate": round(sum(r["passes"] for r in ok) / len(ok), 3) if ok else None,
        "bad_handed_over": sum(r["bad_handed_over"] for r in ok),
        "nothing_handed_over": sum(not r["handed_over"] for r in ok),
        "mean_runs_per_card": mean(r["runs"] for r in ok),
        "gate_failures": sum(r["gate_failures"] for r in ok),
        "mean_seconds": mean(r["seconds"] for r in ok),
        "mean_first_edit_seconds": mean(r["first_edit_seconds"] for r in ok),
        "mean_looks_before_first_edit": mean(r["looks_before_first_edit"] for r in ok),
        "mean_knowledge_entries_given": mean(r["knowledge_entries_given"] for r in ok),
        "policy_decisions": sum(r["policy_decisions"] for r in ok),
        "policy_denials": sum(r["policy_denials"] for r in ok),
    }


def fmt(value) -> str:
    return "-" if value is None else str(value)


def main() -> None:
    evals, prefix, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    runs = runs_of(evals, prefix)
    by_setup: dict[str, list[dict]] = defaultdict(list)
    for run in runs:
        by_setup[run["setup"]].append(run)
    setups = [s for s in SETUP_ORDER if s in by_setup] + sorted(set(by_setup) - set(SETUP_ORDER))
    totals = {s: summary(by_setup[s]) for s in setups}
    print("| Setup | Runs | Passes hidden check | Bad work handed over | Nothing handed over | "
          "Runs per card | First edit (s) | Looks before it | Total time (s) |")  # fmt: skip
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for s in setups:
        t = totals[s]
        rate = f"{t['passes']}/{t['runs']} ({round(100 * t['pass_rate'])}%)" if t["runs"] else "-"
        errors = f" (+{t['errors']} errors)" if t["errors"] else ""
        print(f"| {s} | {t['runs']}{errors} | {rate} | "
              f"{t['bad_handed_over']} | {t['nothing_handed_over']} | {fmt(t['mean_runs_per_card'])} | "
              f"{fmt(t['mean_first_edit_seconds'])} | {fmt(t['mean_looks_before_first_edit'])} | "
              f"{fmt(t['mean_seconds'])} |")  # fmt: skip
    cases = sorted({r["case"] for r in runs})
    per_case = {c: {s: summary([r for r in by_setup[s] if r["case"] == c]) for s in setups} for c in cases}
    print()
    print("| Case | " + " | ".join(setups) + " |")
    print("| --- | " + " | ".join("---" for _ in setups) + " |")
    for c in cases:
        cells = []
        for s in setups:
            t = per_case[c][s]
            cells.append(f"{t['passes']}/{t['runs']}, {fmt(t['mean_first_edit_seconds'])}s" if t["runs"] else "-")
        print(f"| {c} | " + " | ".join(cells) + " |")
    out.write_text(json.dumps({"setups": totals, "cases": per_case, "runs": runs}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
