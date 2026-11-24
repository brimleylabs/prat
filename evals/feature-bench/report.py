"""Summarise the benchmark's run records as Markdown tables.

    python evals/feature-bench/report.py [results dir]

Reads results/<machine>/<case>-<condition>-<round>.json and prints, per machine and case, each
condition's median, fastest and slowest time over the runs that passed the hidden check, how
many passed, and the median model cost. Then every run on its own line.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ORDER = ["plain", "plainlow", "single", "single+pack", "swarm"]
NAMES = {
    "plain": "Plain Claude Code",
    "plainlow": "Plain Claude Code, effort low",
    "single": "PRAT, one card",
    "single+pack": "PRAT, one card, with a fact pack",
    "swarm": "PRAT swarm",
}


def load(root: Path) -> dict:
    found: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for path in sorted(root.glob("*/*.json")):
        case, condition, round_ = path.stem.rsplit("-", 2)
        record = json.loads(path.read_text(encoding="utf-8"))
        record["round"] = int(round_)
        found[path.parent.name][case][condition].append(record)
    return found


def passed(record: dict) -> bool:
    return bool((record.get("hidden_check") or {}).get("passed"))


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "results"
    for machine, cases in load(root).items():
        print(f"## {machine}\n")
        for case, conditions in cases.items():
            print(f"### {case}\n")
            print("| Condition | Passed | Median (s) | Fastest (s) | Slowest (s) | Median cost (USD) |")
            print("| --- | --- | --- | --- | --- | --- |")
            for condition in sorted(conditions, key=ORDER.index):
                runs = conditions[condition]
                good = [r for r in runs if passed(r) and r.get("seconds")]
                times = [r["seconds"] for r in good]
                cost = [r["cost_usd"] + r.get("prep_cost_usd", 0) for r in good if r.get("cost_usd") is not None]
                cells = (
                    [f"{statistics.median(times):.0f}", f"{min(times):.0f}", f"{max(times):.0f}"]
                    if times else ["", "", ""]
                )
                money = f"{statistics.median(cost):.2f}" if cost else ""
                print(f"| {NAMES[condition]} | {len(good)}/{len(runs)} | {' | '.join(cells)} | {money} |")
            print("\nEvery run:\n")
            print("| Condition | Round | Seconds | Hidden check | Outcome | Pack research (s) |")
            print("| --- | --- | --- | --- | --- | --- |")
            for condition in sorted(conditions, key=ORDER.index):
                for r in sorted(conditions[condition], key=lambda r: r["round"]):
                    ran = (r.get("hidden_check") or {}).get("ran", "")
                    check = ("passed" if passed(r) else "FAILED") + (f" ({ran})" if ran else "")
                    print(
                        f"| {NAMES[condition]} | {r['round']} | {r.get('seconds') or ''} | {check} "
                        f"| {r.get('final_status', '')} | {r.get('prep_seconds', '')} |"
                    )
            print()


if __name__ == "__main__":
    main()
