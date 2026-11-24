"""Draw one chart per machine and case from the run records, as PNG files in charts/.

    python evals/feature-bench/charts.py

Needs Playwright (the `screens` extra). Each bar is a condition's median over the runs that
passed the hidden check; each dot is one run, and a hollow dot is a run that failed the check.
"""

from __future__ import annotations

import html
import statistics
from pathlib import Path

from playwright.sync_api import sync_playwright
from report import NAMES, ORDER, load, passed

HERE = Path(__file__).parent
TITLES = {
    "giftcards": "A small feature: gift cards in a 23-file shop backend",
    "megastore": "Eight modules from one 4,400-word specification",
    "coverage": "A feature in coverage.py from a ticket that names no files",
}
PRAT = {"single", "single+pack", "swarm"}
WIDTH = 820  # pixels for the longest run

PAGE = """<!doctype html><meta charset="utf-8">
<style>
:root {{ --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#8a8984; --grid:#e6e5e0; --rest:#c9c8c2; --accent:#2a78d6; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; width:1200px; background:var(--surface); color:var(--ink); font-family:"Segoe UI","Helvetica Neue",Arial,sans-serif; }}
.frame {{ padding:56px 64px 44px; display:flex; flex-direction:column; gap:26px; }}
.kicker {{ font-size:19px; font-weight:600; letter-spacing:.08em; text-transform:uppercase; color:var(--accent); }}
h1 {{ font-size:40px; line-height:1.15; font-weight:700; margin:10px 0 8px; text-wrap:balance; }}
.unit {{ font-size:21px; color:var(--ink2); }}
.plot {{ display:flex; flex-direction:column; gap:26px; }}
.label {{ font-size:23px; font-weight:500; margin-bottom:8px; }}
.label span {{ color:var(--ink2); font-weight:400; }}
.track {{ position:relative; height:46px; width:{width}px; }}
.bar {{ position:absolute; left:0; top:0; height:46px; border-radius:0 4px 4px 0; background:var(--rest); }}
.bar.prat {{ background:var(--accent); }}
.dot {{ position:absolute; top:15px; width:16px; height:16px; margin-left:-8px; border-radius:50%; background:var(--ink); border:2px solid var(--surface); }}
.dot.failed {{ background:var(--surface); border:3px solid var(--ink); }}
.key {{ display:flex; gap:28px; font-size:18px; color:var(--ink2); align-items:center; flex-wrap:wrap; }}
.key i {{ display:inline-block; vertical-align:middle; margin-right:8px; }}
.note {{ font-size:18px; line-height:1.45; color:var(--ink2); border-top:1px solid var(--grid); padding-top:16px; }}
</style>
<div class="frame">
  <div><div class="kicker">PRAT feature benchmark, {machine}</div><h1>{title}</h1>
  <div class="unit">Seconds from a ready card to a result in review. Lower is better.</div></div>
  <div class="plot">{rows}</div>
  <div class="key">
    <span><i style="width:34px;height:14px;background:var(--accent);border-radius:0 3px 3px 0"></i>PRAT, median</span>
    <span><i style="width:34px;height:14px;background:var(--rest);border-radius:0 3px 3px 0"></i>plain Claude Code, median</span>
    <span><i class="dot" style="position:static;margin:0 8px 0 0"></i>one run</span>
    {failed_key}
  </div>
  <div class="note">{note}</div>
</div>"""


def main() -> None:
    out = HERE / "charts"
    out.mkdir(exist_ok=True)
    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 400}, device_scale_factor=2)
        for machine, cases in load(HERE / "results").items():
            for case, conditions in cases.items():
                every = [r["seconds"] for runs in conditions.values() for r in runs if r.get("seconds")]
                top = max(every)
                rows, any_failed, count = [], False, 0
                for condition in sorted(conditions, key=ORDER.index):
                    runs = conditions[condition]
                    count = max(count, len(runs))
                    good = [r["seconds"] for r in runs if passed(r) and r.get("seconds")]
                    median = statistics.median(good)
                    dots = ""
                    for r in runs:
                        if not r.get("seconds"):
                            continue
                        failed = not passed(r)
                        any_failed |= failed
                        dots += f'<div class="dot{" failed" if failed else ""}" style="left:{r["seconds"] / top * WIDTH:.0f}px"></div>'
                    kind = " prat" if condition in PRAT else ""
                    rows.append(
                        f'<div><div class="label">{html.escape(NAMES[condition])} '
                        f"<span>· median {median:.0f} s, {min(good):.0f} to {max(good):.0f}"
                        f'{"" if len(good) == len(runs) else f", {len(good)} of {len(runs)} passed"}</span></div>'
                        f'<div class="track"><div class="bar{kind}" style="width:{median / top * WIDTH:.0f}px"></div>{dots}</div></div>'
                    )
                note = (
                    f"{count} runs of each condition, one at a time, every run shown. "
                    "Protocol, addendum and every run's record: github.com/brimleylabs/prat, evals/feature-bench."
                )
                failed_key = (
                    '<span><i class="dot failed" style="position:static;margin:0 8px 0 0"></i>a run that failed the hidden check</span>'
                    if any_failed else ""
                )
                page.set_content(PAGE.format(
                    width=WIDTH, machine=html.escape("Windows 11" if machine == "windows" else "macOS"),
                    title=html.escape(TITLES[case]), rows="".join(rows), note=note, failed_key=failed_key,
                ))
                target = out / f"{machine}-{case}.png"
                page.screenshot(path=str(target), full_page=True)
                print("wrote", target.name)
        browser.close()


if __name__ == "__main__":
    main()
