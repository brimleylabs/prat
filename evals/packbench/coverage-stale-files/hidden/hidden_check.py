"""Hidden check for the coverage.py stale files card: the fast part of coverage.py's own
tests still passes, and fingerprints and `coverage stale` behave as the card describes.
Run from the root of the checkout."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))

# A data file written by coverage.py before this feature existed: statement data for
# old_a.py (lines 1, 2, 3) and old_b.py (line 5).
OLD_DATA_FILE = """
eNrt3E1vG0UYB/B9ceysX+qgKLJCFGUuKLHquLUQKi8HCBCJikpQCBUn3LE9thfsXWd3ncQHhFwqyofgi/EFWq4V
vaBy4JnZWb8kdoETxPr/ZG+9uzOz88zOzGa9Sb+8f8+NBGv7QZ9H7E1jwzBN4wPGDMPI0btgTK3TOzWzbhp/L2dU
fyoXig+MfPpro/hN4fXiYb6Tf577Jf+281XOzVScKu0AAICVMb6fz5S2t81H30e80RNRwJsiiJeFj744Pjo5ZidH
H947ZvE2dpBl5PCQHbHAP2cD2tR2e4K5Xstt8sj1OizqiiT1MBQtec2ibXTVkgmrKr/8VHdblCsSHUo3CNw+D0bs
OzGqqAQ6fyQuongDFSLcjidTsAOdvcwC0RaB8JoijGtxQBuz5R9/yGZKm5vmE6Fi4kGTXrm5aGjDNJS7bSqo6Qct
WftGwL1mV4QVxicRNn1P1iSONvD7tyKf9VxPhemFbuT6HhMXojmMRGthgBU2g444Gw2V9VAmfxjn1MdamHlBTp1c
Z5aV8/yFR5WZVZ29Yb9BcXw77A/kyaEccd7IX5ZzWd7Ir/7Lc7PgXE7jncuSNPk019BzT4dicoDKTEtVdOAVFQT1
gDdy6dI7m6ZBvVJchKc9+tGpzoeRr9brdPLrNVrkH7+2nint7ZlPTlVHkSHWG24UTj44c51msnlJ15H7l/cb2Qbz
nUSOol6P+W01ZGYaOJwfM7eScnggBxrzdUKqyX/a23QdWKPnNxb0mFAIFViSrD30mnKohDKGpn8mAt4RVb33/9GR
qOvUnHTpeG9Z15l0gXpt8jE73k5nSjs75iNHdSNdnP4nM9eFJrW5Oo0mu/qCh8Mg6SOvmiSTHNNZMglN76FwDjPp
0tHOsnCSyGv6w/o4k8qUtrbMcTwiZBPJ99pcEPFZWHIh+OfVH/Cou6DucjNVfH8tXXpva1nF1bmryWX68Yalqvyz
rarcp0lfvu25Ksst0yp/KkaHZ7xHRxtwNwjltKGHsUrY4pSYN/xhpPqvXFU51SiY1Dcu4Gr9KVU5OdDnfhi6VCmZ
NXw32cr2uzyUs1C4zxq+T1cobzJo7oZ0TDdUB11wVXp/WkY4kmV0zvZVHWYH3glVOhlf9KHf515LX6/klBKXKudv
HVx1WihlCmmIXiozKVTvlTPWZPwORnGpfd6Kh/v0Kq9KPO+KK8XF+z7mkYjcvmAyySQrO+c0aAPBZ6dJ1WLdxcV8
QnuSOTSOprxvv6rvyFNcr8llSt8tG+M9M1O6edMcD/UIjoOrh9TmfX5p1bo0oud2TjvZZ9TeNDJmO1c034ZyVedy
PX3aG3HMSSo9eLJleYNvFl8a9AIAAAAAAACA1WOblpmR9/9W8Vej+IwWAAAAAAAAAHC95C17t5j8PsjtkmnvJL+E
cadau1N9i9+utsRZLavu/38zir/TAgAAAAAAAACutay94+j7/7y9ayZfDFjx8/+n8v7/KVoJAAAAAAAA4DrLWbax
6/da9UZ1MMqZeoXTSvz8/5lRfEELAAAAAAAAALjO5DP/5P7fytm7TnL/j///DwAAAAAAAGCl2aZtFLLq/v9Pg14A
AAAAAAAAsIpfANgFJ/77/+dG8Q9aAAAAAAAAAMBqSFsp07lhsTUz5Tg3bsR////CKL6kBQAAAAAAAACszlcAKdMx
LSuVchz1/N/YQJsAAAAAAAAArLIs7v8BAAAAAAAAVh6e/wMAAAAAAACsvr8AUiZIdg==
"""

UPSTREAM_TESTS = [
    "tests/test_data.py",
    "tests/test_sqlitedb.py",
    "tests/test_cmdline.py",
    "tests/test_api.py",
]

PROG = b"import helper\n\nprint(helper.double(21))\n"
HELPER = b"def double(n):\r\n    return n * 2\r\n"
OTHER = b"total = 1 + 2\nprint(total)\n"


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("COVERAGE_")}
    env["PYTHONPATH"] = str(ROOT)
    env.update(extra)
    return env


def run_python(args, cwd, **env):
    return subprocess.run(
        [sys.executable, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=clean_env(**env),
        cwd=cwd,
        timeout=120,
    )


def cov(args, cwd, **env):
    return run_python(["-m", "coverage", *args], cwd, **env)


class Data:
    """An opened coverage data file, closed again so Windows can delete it."""

    def __init__(self, path, read: bool = True):
        from coverage import CoverageData

        self.data = CoverageData(basename=str(path))
        if read:
            self.data.read()

    def __enter__(self):
        return self.data

    def __exit__(self, *exc):
        self.data.close()
        return False


def measured(data, basename: str) -> str:
    found = [f for f in data.measured_files() if os.path.basename(f) == basename]
    assert len(found) == 1, f"expected one measured {basename}, got {found}"
    return found[0]


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(os.path.realpath(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def write_programs(self):
        (self.dir / "prog.py").write_bytes(PROG)
        (self.dir / "helper.py").write_bytes(HELPER)
        (self.dir / "other.py").write_bytes(OTHER)

    def cov_ok(self, *args, **env):
        out = cov(list(args), self.dir, **env)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return out

    def old_data_file(self, name: str = "old.cov") -> Path:
        path = self.dir / name
        path.write_bytes(zlib.decompress(base64.b64decode(OLD_DATA_FILE)))
        return path


class FingerprintApi(TempDirCase):
    def test_file_that_was_not_measured(self):
        with Data(self.dir / "d.cov", read=False) as data:
            data.add_lines({"a.py": [1, 2]})
            self.assertIsNone(data.file_fingerprint("nope.py"))

    def test_measured_file_without_fingerprint(self):
        with Data(self.dir / "d.cov", read=False) as data:
            data.add_lines({"a.py": [1, 2]})
            self.assertIsNone(data.file_fingerprint("a.py"))

    def test_add_and_get(self):
        with Data(self.dir / "d.cov", read=False) as data:
            data.add_lines({"a.py": [1, 2], "b.py": [3]})
            data.add_file_fingerprints({"a.py": "abc123"})
            self.assertEqual(data.file_fingerprint("a.py"), "abc123")
            self.assertIsNone(data.file_fingerprint("b.py"))

    def test_add_several(self):
        with Data(self.dir / "d.cov", read=False) as data:
            data.add_lines({"a.py": [1], "b.py": [3], "c.py": [4]})
            data.add_file_fingerprints({"a.py": "one", "b.py": "two"})
            data.add_file_fingerprints({"c.py": "three"})
            self.assertEqual(
                [data.file_fingerprint(f) for f in ("a.py", "b.py", "c.py")],
                ["one", "two", "three"],
            )

    def test_newer_fingerprint_replaces_older(self):
        with Data(self.dir / "d.cov", read=False) as data:
            data.add_lines({"a.py": [1], "b.py": [2]})
            data.add_file_fingerprints({"a.py": "first", "b.py": "keep"})
            data.add_file_fingerprints({"a.py": "second"})
            self.assertEqual(data.file_fingerprint("a.py"), "second")
            self.assertEqual(data.file_fingerprint("b.py"), "keep")

    def test_same_fingerprint_twice(self):
        with Data(self.dir / "d.cov", read=False) as data:
            data.add_lines({"a.py": [1]})
            data.add_file_fingerprints({"a.py": "same"})
            data.add_file_fingerprints({"a.py": "same"})
            self.assertEqual(data.file_fingerprint("a.py"), "same")

    def test_unmeasured_file_is_an_error(self):
        from coverage.exceptions import DataError

        with Data(self.dir / "d.cov", read=False) as data:
            data.add_lines({"a.py": [1]})
            with self.assertRaises(DataError):
                data.add_file_fingerprints({"never_measured.py": "abc"})
            self.assertNotIn("never_measured.py", data.measured_files())

    def test_empty_mapping_changes_nothing(self):
        with Data(self.dir / "d.cov", read=False) as data:
            data.add_lines({"a.py": [1]})
            data.add_file_fingerprints({"a.py": "abc"})
            data.add_file_fingerprints({})
            self.assertEqual(data.file_fingerprint("a.py"), "abc")
            self.assertEqual(data.measured_files(), {"a.py"})

    def test_branch_data(self):
        with Data(self.dir / "d.cov", read=False) as data:
            data.add_arcs({"a.py": [(-1, 1), (1, 2), (2, -1)]})
            data.add_file_fingerprints({"a.py": "abc"})
            self.assertEqual(data.file_fingerprint("a.py"), "abc")
            self.assertTrue(data.has_arcs())
            self.assertEqual(sorted(data.arcs("a.py")), [(-1, 1), (1, 2), (2, -1)])

    def test_measurements_are_untouched(self):
        with Data(self.dir / "d.cov", read=False) as data:
            data.add_lines({"a.py": [1, 2, 17], "b.py": [3]})
            data.add_file_fingerprints({"a.py": "abc"})
            self.assertEqual(sorted(data.lines("a.py")), [1, 2, 17])
            self.assertEqual(sorted(data.lines("b.py")), [3])
            self.assertEqual(data.measured_files(), {"a.py", "b.py"})
            self.assertEqual(data.file_tracer("a.py"), "")
            self.assertFalse(data.has_arcs())

    def test_saved_in_the_data_file(self):
        path = self.dir / "d.cov"
        with Data(path, read=False) as data:
            data.add_lines({"a.py": [1], "b.py": [2]})
            data.add_file_fingerprints({"a.py": "abc"})
            data.write()
        with Data(path) as data:
            self.assertEqual(data.file_fingerprint("a.py"), "abc")
            self.assertIsNone(data.file_fingerprint("b.py"))
            self.assertEqual(sorted(data.lines("a.py")), [1])

    def test_erase_forgets_fingerprints(self):
        with Data(self.dir / "d.cov", read=False) as data:
            data.add_lines({"a.py": [1]})
            data.add_file_fingerprints({"a.py": "abc"})
            data.erase()
            data.add_lines({"a.py": [1]})
            self.assertIsNone(data.file_fingerprint("a.py"))


class Combining(TempDirCase):
    def two(self):
        return Data(self.dir / "one.cov", read=False), Data(self.dir / "two.cov", read=False)

    def test_fingerprints_come_along(self):
        one, two = self.two()
        with one as a, two as b:
            a.add_lines({"a.py": [1]})
            a.add_file_fingerprints({"a.py": "fa"})
            b.add_lines({"b.py": [2]})
            b.add_file_fingerprints({"b.py": "fb"})
            a.update(b)
            self.assertEqual(a.file_fingerprint("a.py"), "fa")
            self.assertEqual(a.file_fingerprint("b.py"), "fb")
            self.assertEqual(sorted(a.lines("b.py")), [2])

    def test_merged_in_data_wins(self):
        one, two = self.two()
        with one as a, two as b:
            a.add_lines({"a.py": [1]})
            a.add_file_fingerprints({"a.py": "mine"})
            b.add_lines({"a.py": [2]})
            b.add_file_fingerprints({"a.py": "theirs"})
            a.update(b)
            self.assertEqual(a.file_fingerprint("a.py"), "theirs")
            self.assertEqual(sorted(a.lines("a.py")), [1, 2])

    def test_kept_when_merged_in_data_has_none(self):
        one, two = self.two()
        with one as a, two as b:
            a.add_lines({"a.py": [1]})
            a.add_file_fingerprints({"a.py": "mine"})
            b.add_lines({"a.py": [2]})
            a.update(b)
            self.assertEqual(a.file_fingerprint("a.py"), "mine")

    def test_no_fingerprints_anywhere(self):
        one, two = self.two()
        with one as a, two as b:
            a.add_lines({"a.py": [1]})
            b.add_lines({"b.py": [2]})
            a.update(b)
            self.assertIsNone(a.file_fingerprint("a.py"))
            self.assertIsNone(a.file_fingerprint("b.py"))

    def test_remapped_file_names(self):
        one, two = self.two()
        with one as a, two as b:
            a.add_lines({"local.py": [1]})
            b.add_lines({"remote.py": [2], "plain.py": [3]})
            b.add_file_fingerprints({"remote.py": "fr", "plain.py": "fp"})
            a.update(b, map_path=lambda p: "local.py" if p == "remote.py" else p)
            self.assertEqual(a.file_fingerprint("local.py"), "fr")
            self.assertEqual(a.file_fingerprint("plain.py"), "fp")
            self.assertIsNone(a.file_fingerprint("remote.py"))
            self.assertEqual(sorted(a.lines("local.py")), [1, 2])


class OlderDataFiles(TempDirCase):
    def test_still_readable(self):
        with Data(self.old_data_file()) as data:
            self.assertEqual(data.measured_files(), {"old_a.py", "old_b.py"})
            self.assertEqual(sorted(data.lines("old_a.py")), [1, 2, 3])
            self.assertIsNone(data.file_fingerprint("old_a.py"))
            self.assertIsNone(data.file_fingerprint("old_b.py"))

    def test_can_be_added_to(self):
        path = self.old_data_file()
        with Data(path) as data:
            data.add_lines({"old_a.py": [9]})
            data.add_file_fingerprints({"old_a.py": "abc"})
            self.assertEqual(data.file_fingerprint("old_a.py"), "abc")
        with Data(path) as data:
            self.assertEqual(data.file_fingerprint("old_a.py"), "abc")
            self.assertIsNone(data.file_fingerprint("old_b.py"))
            self.assertEqual(sorted(data.lines("old_a.py")), [1, 2, 3, 9])

    def test_merged_into_new_data(self):
        with Data(self.old_data_file()) as old, Data(self.dir / "new.cov", read=False) as new:
            new.add_lines({"old_a.py": [7], "n.py": [1]})
            new.add_file_fingerprints({"old_a.py": "kept", "n.py": "fn"})
            new.update(old)
            self.assertEqual(sorted(new.lines("old_a.py")), [1, 2, 3, 7])
            self.assertEqual(sorted(new.lines("old_b.py")), [5])
            self.assertEqual(new.file_fingerprint("old_a.py"), "kept")
            self.assertIsNone(new.file_fingerprint("old_b.py"))

    def test_new_data_merged_into_it(self):
        with Data(self.old_data_file()) as old, Data(self.dir / "new.cov", read=False) as new:
            new.add_lines({"old_a.py": [7]})
            new.add_file_fingerprints({"old_a.py": "fresh"})
            old.update(new)
            self.assertEqual(old.file_fingerprint("old_a.py"), "fresh")
            self.assertEqual(sorted(old.lines("old_a.py")), [1, 2, 3, 7])

    def test_stale_command_has_nothing_to_say(self):
        path = self.old_data_file()
        out = cov(["stale", f"--data-file={path}"], self.dir)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(out.stdout, "No stale files.\n")

    def test_run_appends_to_it(self):
        self.write_programs()
        path = self.old_data_file()
        self.cov_ok("run", "--append", f"--data-file={path}", "prog.py")
        with Data(path) as data:
            self.assertEqual(sorted(data.lines("old_a.py")), [1, 2, 3])
            prog = measured(data, "prog.py")
            self.assertEqual(data.file_fingerprint(prog), sha(self.dir / "prog.py"))
            self.assertIsNone(data.file_fingerprint("old_a.py"))


class Recording(TempDirCase):
    def setUp(self):
        super().setUp()
        self.write_programs()

    def test_run_records_fingerprints(self):
        out = self.cov_ok("run", "prog.py")
        self.assertIn("42", out.stdout)
        with Data(self.dir / ".coverage") as data:
            prog = measured(data, "prog.py")
            helper = measured(data, "helper.py")
            self.assertEqual(data.file_fingerprint(prog), sha(self.dir / "prog.py"))
            self.assertEqual(data.file_fingerprint(helper), sha(self.dir / "helper.py"))
            self.assertTrue(data.lines(prog))

    def test_fingerprint_is_of_the_bytes_on_disk(self):
        # helper.py has Windows line endings: the digest is of the raw bytes.
        self.cov_ok("run", "prog.py")
        with Data(self.dir / ".coverage") as data:
            self.assertEqual(
                data.file_fingerprint(measured(data, "helper.py")),
                hashlib.sha256(HELPER).hexdigest(),
            )
            self.assertEqual(len(data.file_fingerprint(measured(data, "helper.py"))), 64)

    def test_branch_measurement(self):
        self.cov_ok("run", "--branch", "prog.py")
        with Data(self.dir / ".coverage") as data:
            self.assertTrue(data.has_arcs())
            helper = measured(data, "helper.py")
            self.assertEqual(data.file_fingerprint(helper), sha(self.dir / "helper.py"))

    def test_python_api(self):
        (self.dir / "use_api.py").write_text(
            "import coverage\n"
            "cov = coverage.Coverage(data_file='api.cov')\n"
            "cov.start()\n"
            "import helper\n"
            "helper.double(2)\n"
            "cov.stop()\n"
            "cov.save()\n",
            encoding="utf-8",
        )
        out = run_python(["use_api.py"], self.dir)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        with Data(self.dir / "api.cov") as data:
            helper = measured(data, "helper.py")
            self.assertEqual(data.file_fingerprint(helper), sha(self.dir / "helper.py"))

    def test_file_gone_before_saving(self):
        (self.dir / "vanish.py").write_text(
            "import os\n"
            "with open('gone.py', 'w') as f:\n"
            "    f.write('value = 5\\n')\n"
            "import gone\n"
            "os.remove('gone.py')\n"
            "print(gone.value)\n",
            encoding="utf-8",
        )
        out = self.cov_ok("run", "vanish.py")
        self.assertIn("5", out.stdout)
        with Data(self.dir / ".coverage") as data:
            gone = measured(data, "gone.py")
            self.assertIsNone(data.file_fingerprint(gone))
            vanish = measured(data, "vanish.py")
            self.assertEqual(data.file_fingerprint(vanish), sha(self.dir / "vanish.py"))
        out = cov(["stale"], self.dir)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(out.stdout, "No stale files.\n")

    def test_append_keeps_fingerprint_of_files_not_measured_again(self):
        self.cov_ok("run", "prog.py")
        before = sha(self.dir / "helper.py")
        (self.dir / "helper.py").write_bytes(HELPER + b"\r\nextra = 1\r\n")
        self.cov_ok("run", "--append", "other.py")
        with Data(self.dir / ".coverage") as data:
            helper = measured(data, "helper.py")
            self.assertEqual(data.file_fingerprint(helper), before)
            other = measured(data, "other.py")
            self.assertEqual(data.file_fingerprint(other), sha(self.dir / "other.py"))
        out = cov(["stale"], self.dir)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertEqual(out.stdout, f"changed {helper}\n")

    def test_append_updates_fingerprint_of_files_measured_again(self):
        self.cov_ok("run", "prog.py")
        (self.dir / "helper.py").write_bytes(HELPER + b"\r\nextra = 1\r\n")
        self.cov_ok("run", "--append", "prog.py")
        with Data(self.dir / ".coverage") as data:
            helper = measured(data, "helper.py")
            self.assertEqual(data.file_fingerprint(helper), sha(self.dir / "helper.py"))
        out = cov(["stale"], self.dir)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(out.stdout, "No stale files.\n")

    def test_combine_keeps_fingerprints(self):
        self.cov_ok("run", "--parallel-mode", "prog.py")
        self.cov_ok("run", "--parallel-mode", "other.py")
        self.assertFalse((self.dir / ".coverage").exists())
        self.cov_ok("combine")
        with Data(self.dir / ".coverage") as data:
            for name in ("prog.py", "helper.py", "other.py"):
                path = measured(data, name)
                self.assertEqual(data.file_fingerprint(path), sha(self.dir / name), name)
        out = cov(["stale"], self.dir)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_reports_still_work(self):
        self.cov_ok("run", "prog.py")
        out = self.cov_ok("report")
        self.assertIn("prog.py", out.stdout)
        self.assertIn("helper.py", out.stdout)
        self.assertIn("100%", out.stdout)
        self.cov_ok("json", "-o", "out.json")
        report = json.loads((self.dir / "out.json").read_text(encoding="utf-8"))
        self.assertEqual(report["totals"]["percent_covered"], 100.0)
        self.assertEqual(sorted(report["files"]), ["helper.py", "prog.py"])

    def test_erase_still_works(self):
        self.cov_ok("run", "prog.py")
        self.cov_ok("erase")
        self.assertFalse((self.dir / ".coverage").exists())


class StaleCommand(TempDirCase):
    def setUp(self):
        super().setUp()
        self.write_programs()

    def names(self, data_file: str = ".coverage") -> dict[str, str]:
        with Data(self.dir / data_file) as data:
            return {os.path.basename(f): f for f in data.measured_files()}

    def test_nothing_stale(self):
        self.cov_ok("run", "prog.py")
        out = cov(["stale"], self.dir)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(out.stdout, "No stale files.\n")

    def test_changed_file(self):
        self.cov_ok("run", "prog.py")
        (self.dir / "helper.py").write_bytes(HELPER + b"# edited\r\n")
        out = cov(["stale"], self.dir)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertEqual(out.stdout, f"changed {self.names()['helper.py']}\n")

    def test_missing_file(self):
        self.cov_ok("run", "prog.py")
        (self.dir / "helper.py").unlink()
        out = cov(["stale"], self.dir)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertEqual(out.stdout, f"missing {self.names()['helper.py']}\n")

    def test_several_in_file_name_order(self):
        (self.dir / "all.py").write_bytes(b"import prog\nimport other\n")
        self.cov_ok("run", "all.py")
        names = self.names()
        (self.dir / "prog.py").write_bytes(PROG + b"# edited\n")
        (self.dir / "helper.py").unlink()
        (self.dir / "all.py").write_bytes(b"import prog\nimport other\n\n")
        expected = sorted(
            [
                (names["prog.py"], "changed"),
                (names["helper.py"], "missing"),
                (names["all.py"], "changed"),
            ]
        )
        out = cov(["stale"], self.dir)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertEqual(
            out.stdout, "".join(f"{status} {name}\n" for name, status in expected)
        )

    def test_same_content_again_is_not_stale(self):
        self.cov_ok("run", "prog.py")
        (self.dir / "helper.py").write_bytes(HELPER + b"# edited\r\n")
        self.assertEqual(cov(["stale"], self.dir).returncode, 2)
        (self.dir / "helper.py").write_bytes(HELPER)
        out = cov(["stale"], self.dir)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(out.stdout, "No stale files.\n")

    def test_line_endings_count(self):
        self.cov_ok("run", "prog.py")
        (self.dir / "helper.py").write_bytes(HELPER.replace(b"\r\n", b"\n"))
        out = cov(["stale"], self.dir)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertEqual(out.stdout, f"changed {self.names()['helper.py']}\n")

    def test_asking_twice_gives_the_same_answer(self):
        self.cov_ok("run", "prog.py")
        (self.dir / "helper.py").write_bytes(HELPER + b"# edited\r\n")
        first = cov(["stale"], self.dir)
        second = cov(["stale"], self.dir)
        self.assertEqual(first.returncode, 2)
        self.assertEqual((second.returncode, second.stdout), (first.returncode, first.stdout))

    def test_data_file_option(self):
        self.cov_ok("run", "--data-file=custom.cov", "prog.py")
        (self.dir / "prog.py").write_bytes(PROG + b"# edited\n")
        out = cov(["stale", "--data-file=custom.cov"], self.dir)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertEqual(out.stdout, f"changed {self.names('custom.cov')['prog.py']}\n")

    def test_data_file_from_the_environment(self):
        self.cov_ok("run", "prog.py", COVERAGE_FILE="env.cov")
        (self.dir / "prog.py").write_bytes(PROG + b"# edited\n")
        out = cov(["stale"], self.dir, COVERAGE_FILE="env.cov")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertEqual(out.stdout, f"changed {self.names('env.cov')['prog.py']}\n")

    def test_no_data(self):
        out = cov(["stale"], self.dir)
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("No data to report.", out.stdout + out.stderr)
        self.assertNotIn("Traceback", out.stdout + out.stderr)
        self.assertFalse((self.dir / ".coverage").exists())

    def test_no_data_in_named_file(self):
        self.cov_ok("run", "--data-file=custom.cov", "prog.py")
        out = cov(["stale"], self.dir)
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("No data to report.", out.stdout + out.stderr)

    def test_files_without_fingerprint_are_never_listed(self):
        a, b, c, d = (str(self.dir / n) for n in ("a.py", "b.py", "c.py", "d.py"))
        for name in (a, b, c):
            Path(name).write_bytes(b"x = 1\n")
        with Data(self.dir / ".coverage", read=False) as data:
            data.add_lines({a: [1], b: [1], c: [1], d: [1]})
            data.add_file_fingerprints({a: sha(Path(a)), b: "0" * 64})
            data.write()
        out = cov(["stale"], self.dir)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        # a.py matches, b.py does not; c.py and d.py (which does not exist) have none.
        self.assertEqual(out.stdout, f"changed {b}\n")

    def test_missing_file_with_fingerprint_from_the_api(self):
        a, b = (str(self.dir / n) for n in ("a.py", "b.py"))
        Path(a).write_bytes(b"x = 1\n")
        with Data(self.dir / ".coverage", read=False) as data:
            data.add_lines({a: [1], b: [1]})
            data.add_file_fingerprints({a: sha(Path(a)), b: "0" * 64})
            data.write()
        out = cov(["stale"], self.dir)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertEqual(out.stdout, f"missing {b}\n")

    def test_help(self):
        out = cov(["help"], self.dir)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("stale", out.stdout)
        self.assertIn("combine", out.stdout)
        out = cov(["stale", "--help"], self.dir)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("--data-file", out.stdout)
        out = cov(["help", "stale"], self.dir)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("--data-file", out.stdout)


def main() -> int:
    upstream = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "-o", "addopts=", "-o", "strict=false", "-o", "verbosity_assertions=1",
            "--deselect", "tests/test_api.py::ApiTest::test_stdlib_symlink",
            *UPSTREAM_TESTS,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=clean_env(COVERAGE_CORE="pytrace"),
        timeout=600,
    )
    print("\n".join(upstream.stdout.strip().splitlines()[-3:]))
    if upstream.returncode != 0:
        print("coverage.py's own tests failed")
        return 1
    result = unittest.main(argv=["hidden_check"], exit=False, verbosity=1).result
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
