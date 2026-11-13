+++
id = "kf3a0ccd846"
kind = "convention"
summary = "Platform-specific tests are skipped on Windows with skipif"
paths = ["tests/test_console.py", "tests/test_layout.py", "tests/test_logging.py"]
status = "active"
confidence = "observed"
author = "scout"
helped = 0
hurt = 0
created = "2026-11-13T05:38:24+00:00"
last_verified = "2026-11-13T05:38:24+00:00"
invalid_at = ""
superseded_by = ""

[[sources]]
note = "deep scan, 20261113T053544"
+++
Tests that depend on terminal behaviour (screen, kitty/16color/truecolor detection, layout render, refresh_screen, brokenpipe) use pytest.mark.skipif(sys.platform == "win32"). test_logging uses an `os.name == "nt"` skip_win marker. Others bail out early with `if console.legacy_windows: return`. Follow this pattern for new tests that assert terminal escape output.
