"""Bugs fixed out of three, per run, for the cases of ADDENDUM-4.md.

    python analyze_gnarly.py EVALS_DIR OUT_JSON

The hidden check runs Rich's whole suite; a bug counts as fixed when none of its failing tests
(from the sweep) is among the failures the check reports. Also counts, per setup, the runs that
handed over work missing a bug, and PRAT's runs sent back by its reviewer or gates.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
FAILED = re.compile(r"FAILED tests[\\/](\S+?)\.py::(\S+)")


def bug_tests() -> dict[str, list[set[str]]]:
    """Case name -> one set of 'test_module::test_name' per bug."""
    drawn = json.loads((HERE / "sweep" / "gnarly-drawn.json").read_text(encoding="utf-8"))["cases"]
    cards = json.loads((HERE / "gnarly-cards.json").read_text(encoding="utf-8"))
    out = {}
    for case in drawn:
        name = cards[str(case["case"])]["name"]
        out[name] = [{t.split("[")[0] for t in m["failing"]} for m in case["bugs"]]
    return out


def failures(output: str) -> set[str]:
    return {f"{module.split('/')[-1]}::{test.split('[')[0]}" for module, test in FAILED.findall(output)}


def main() -> None:
    evals, out = Path(sys.argv[1]), Path(sys.argv[2])
    bugs = bug_tests()
    rows = defaultdict(list)
    for folder in sorted(evals.iterdir()):
        result = folder / "result.json"
        if not result.is_file():
            continue
        data = json.loads(result.read_text(encoding="utf-8"))
        if not data.get("label", "").startswith("gnarly-"):
            continue
        setup = data["label"][len("gnarly-") :].rsplit("-r", 1)[0]
        for case in data["cases"]:
            def fixed_in(attempt: dict | None) -> int:
                check = (attempt or {}).get("check") or {}
                if check.get("passed"):
                    return 3
                failed = failures(check.get("output", ""))
                return sum(1 for tests in bugs[case["name"]] if not tests & failed)

            attempts = case["attempts"]
            final = attempts[-1] if attempts else None
            check = (final or {}).get("check") or {}
            fixed = fixed_in(final)
            rows[setup].append({
                "case": case["name"], "label": data["label"], "passed": bool(check.get("passed")),
                "bugs_fixed": fixed, "runs": len(case["attempts"]),
                "sent_back": sum(a["card_status"] == "rework" for a in case["attempts"]),
                "gate_failed": sum(a["steps"].get("verify") == "failed" for a in case["attempts"]),
                "first_passed": bool(((attempts[0] if attempts else {}).get("check") or {}).get("passed")),
                "first_bugs_fixed": fixed_in(attempts[0] if attempts else None),
                "cost": sum(a.get("cost_usd") or 0 for a in case["attempts"]),
                "seconds": sum(a.get("duration_seconds") or 0 for a in case["attempts"]),
            })  # fmt: skip
    summary = {}
    print("| Setup | Runs | All three fixed | Bugs fixed (of 3, mean) | First run: all fixed | First run: bugs fixed | Runs per card | Sent back | Cost per case | Time per case (s) |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for setup in ("plain", "plain-md", "prat", "prat-seeded", *sorted(set(rows) - {"plain", "plain-md", "prat", "prat-seeded"})):
        r = rows.get(setup)
        if not r:
            continue
        n = len(r)
        s = {
            "runs": n, "passed": sum(x["passed"] for x in r),
            "bugs_fixed": sum(x["bugs_fixed"] for x in r) / n,
            "first_passed": sum(x["first_passed"] for x in r),
            "first_bugs_fixed": sum(x["first_bugs_fixed"] for x in r) / n,
            "runs_per_card": sum(x["runs"] for x in r) / n,
            "sent_back": sum(x["sent_back"] for x in r), "gate_failed": sum(x["gate_failed"] for x in r),
            "cost": sum(x["cost"] for x in r) / n, "seconds": sum(x["seconds"] for x in r) / n,
        }  # fmt: skip
        summary[setup] = s
        print(f"| {setup} | {n} | {s['passed']}/{n} | {s['bugs_fixed']:.2f} | {s['first_passed']}/{n} | "
              f"{s['first_bugs_fixed']:.2f} | {s['runs_per_card']:.2f} | "
              f"{s['sent_back']} | {s['cost']:.3f} | {s['seconds']:.0f} |")  # fmt: skip
    print()
    cases = sorted({x["case"] for r in rows.values() for x in r})
    print("| Case | " + " | ".join(summary) + " |")
    print("| --- |" + " --- |" * len(summary))
    for case in cases:
        cells = []
        for setup in summary:
            r = [x for x in rows[setup] if x["case"] == case]
            cells.append(f"{sum(x['passed'] for x in r)}/{len(r)} ({'+'.join(str(x['bugs_fixed']) for x in r)})")
        print(f"| {case} | " + " | ".join(cells) + " |")
    out.write_text(json.dumps({"summary": summary, "runs": rows}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
