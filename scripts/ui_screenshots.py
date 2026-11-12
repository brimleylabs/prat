"""Take screenshots of a running PRAT web UI with a headless Chromium browser.

    python scripts/ui_screenshots.py <prat-home> <browser-executable> <output-dir> \
        [--shot name=/path ...] [--widths 1440,768] [--themes light,dark]

Each shot signs in through a fresh one-time login link (minted from the
access token in <prat-home>, the same way `prat login-url` does), so no
authentication bypass is needed. The browser runs with its own throwaway
profile under the output directory.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from prat.config import load_settings
from prat.web.tokens import login_url

DEFAULT_SHOTS = {
    "board": "/board",
    "card-running": "/cards/9",
    "card-review": "/cards/2",
    "replay": "/cards/2/runs/2/replay",
    "wall": "/wall",
    "projects": "/projects",
    "usage": "/usage",
    "settings": "/settings",
}
HEIGHT = {1440: 1000, 768: 1100, 520: 1200}


def shoot(browser: str, url: str, out: Path, width: int, height: int, theme: str) -> None:
    profile = Path(tempfile.mkdtemp(prefix="prat-shot-"))
    args = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        f"--user-data-dir={profile}",
        f"--window-size={width},{height}",
        "--timeout=6000",
        # Lets scripts finish (the knowledge graph fetches its data after load).
        "--virtual-time-budget=5000",
        f"--blink-settings=preferredColorScheme={0 if theme == 'dark' else 1}",
        *(["--force-dark-mode"] if theme == "dark" else []),
        f"--screenshot={out}",
        url,
    ]
    try:
        subprocess.run(args, check=False, capture_output=True, timeout=90)
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("home", type=Path)
    parser.add_argument("browser")
    parser.add_argument("out", type=Path)
    parser.add_argument("--shot", action="append", default=[], help="name=/path")
    parser.add_argument("--widths", default="1440,768")
    parser.add_argument("--themes", default="light,dark")
    parser.add_argument("--height", type=int, default=0)
    parser.add_argument("--port", type=int, default=0, help="the port the server listens on")
    args = parser.parse_args()
    shots = dict(s.split("=", 1) for s in args.shot) if args.shot else DEFAULT_SHOTS
    settings = load_settings(args.home, **({"web_port": args.port} if args.port else {}))
    args.out.mkdir(parents=True, exist_ok=True)
    for name, path in shots.items():
        for width in (int(w) for w in args.widths.split(",")):
            for theme in args.themes.split(","):
                target = args.out / f"{name}-{width}-{theme}.png"
                url = login_url(settings, next_path=path)
                shoot(
                    args.browser, url, target, width, args.height or HEIGHT.get(width, 1100), theme
                )
                print(target, "ok" if target.exists() else "MISSING", flush=True)


if __name__ == "__main__":
    main()
