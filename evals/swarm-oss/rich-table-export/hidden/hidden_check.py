"""Hidden check for the Rich table export epic: Rich's own tests still pass, and each
export behaves as the card describes. Run from the root of the checkout."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))


def table(**kwargs):
    from rich.table import Table

    t = Table(**kwargs)
    t.add_column("Name")
    t.add_column("Qty", justify="right")
    t.add_column("Note", justify="center")
    t.add_row("[bold]apple[/bold]", "3", "red, crisp")
    t.add_row("pear", "10", 'say "hi"')
    return t


class CellText(unittest.TestCase):
    def test_kinds(self):
        from rich.segment import Segment
        from rich.table_export import cell_text
        from rich.text import Text

        class Two:
            def __rich_console__(self, console, options):
                yield Segment("first  ")
                yield Segment.line()
                yield Segment("second")

        self.assertEqual(cell_text(None), "")
        self.assertEqual(cell_text("[bold red]hot[/bold red] tea"), "hot tea")
        self.assertEqual(cell_text(Text("plain", style="bold")), "plain")
        self.assertEqual(cell_text(Two()), "first\nsecond")


class Csv(unittest.TestCase):
    def test_header_and_rows(self):
        from rich.table_export import to_csv

        self.assertEqual(
            to_csv(table()),
            'Name,Qty,Note\napple,3,"red, crisp"\npear,10,"say ""hi"""\n',
        )

    def test_no_header_and_footer(self):
        from rich.table import Table
        from rich.table_export import to_csv

        t = Table(show_header=False)
        t.add_column("A", footer="total")
        t.add_column("B", footer="[i]3[/i]")
        t.add_row("x", None)
        self.assertEqual(to_csv(t, header=False, footer=True), "x,\ntotal,3\n")
        self.assertEqual(to_csv(t), "A,B\nx,\n")


class Markdown(unittest.TestCase):
    def test_table(self):
        from rich.table_export import to_markdown

        self.assertEqual(
            to_markdown(table()),
            "| Name | Qty | Note |\n"
            "| --- | ---: | :---: |\n"
            "| apple | 3 | red, crisp |\n"
            '| pear | 10 | say "hi" |\n',
        )

    def test_title_escape_and_align(self):
        from rich.table import Table
        from rich.table_export import to_markdown

        t = Table(title="[b]Stock[/b]")
        t.add_column("L", justify="left")
        t.add_column("F", justify="full")
        t.add_row("a|b", "one\ntwo")
        self.assertEqual(
            to_markdown(t),
            "**Stock**\n\n| L | F |\n| --- | --- |\n| a\\|b | one<br>two |\n",
        )


class Json(unittest.TestCase):
    def test_objects(self):
        from rich.table_export import to_json

        self.assertEqual(
            json.loads(to_json(table())),
            [
                {"Name": "apple", "Qty": "3", "Note": "red, crisp"},
                {"Name": "pear", "Qty": "10", "Note": 'say "hi"'},
            ],
        )
        self.assertEqual(to_json(table(), indent=None)[:20], '[{"Name": "apple", "')
        self.assertIn('\n  {\n    "Name": "apple"', to_json(table()))

    def test_keys(self):
        from rich.table import Table
        from rich.table_export import to_json

        t = Table()
        t.add_column("x")
        t.add_column("x")
        t.add_column("")
        t.add_column("x")
        t.add_row("1", "2", "3", "é")
        self.assertEqual(
            json.loads(to_json(t)),
            [{"x": "1", "x (2)": "2", "column 3": "3", "x (3)": "é"}],
        )
        self.assertIn("é", to_json(t))
        self.assertEqual(json.loads(to_json(Table("a"))), [])


class Html(unittest.TestCase):
    def test_full(self):
        from rich.table import Table
        from rich.table_export import to_html

        t = Table(title="Fruit & veg", show_footer=True)
        t.add_column("Name", footer="total")
        t.add_column("Qty", justify="right", footer="13")
        t.add_row("<apple>", "3")
        t.add_row("line\nbreak", "10")
        self.assertEqual(
            to_html(t),
            "<table>\n"
            "<caption>Fruit &amp; veg</caption>\n"
            "<thead>\n"
            '<tr><th>Name</th><th style="text-align: right">Qty</th></tr>\n'
            "</thead>\n"
            "<tbody>\n"
            '<tr><td>&lt;apple&gt;</td><td style="text-align: right">3</td></tr>\n'
            '<tr><td>line<br>break</td><td style="text-align: right">10</td></tr>\n'
            "</tbody>\n"
            "<tfoot>\n"
            '<tr><td>total</td><td style="text-align: right">13</td></tr>\n'
            "</tfoot>\n"
            "</table>\n",
        )

    def test_no_header_no_rows(self):
        from rich.table import Table
        from rich.table_export import to_html

        t = Table(show_header=False)
        t.add_column("A", justify="center")
        self.assertEqual(to_html(t), "<table>\n<tbody>\n</tbody>\n</table>\n")


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(ROOT)}
        return subprocess.run(
            [sys.executable, "-m", "rich.table_export", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=ROOT,
            timeout=60,
        )

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "stock.csv"
        self.path.write_text('name,qty\napple,3\n"pear, ripe",10\nfig\n', encoding="utf-8")

    def tearDown(self):
        self.dir.cleanup()

    def test_formats(self):
        out = self.run_cli(str(self.path), "--format", "csv")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout, 'name,qty\napple,3\n"pear, ripe",10\nfig,\n')
        out = self.run_cli(str(self.path), "--format", "markdown")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(out.stdout.startswith("| name | qty |\n| --- | --- |\n| apple | 3 |\n"))
        out = self.run_cli(str(self.path), "--format", "json")
        self.assertEqual(json.loads(out.stdout)[1], {"name": "pear, ripe", "qty": "10"})
        out = self.run_cli(str(self.path), "--format", "html")
        self.assertIn("<th>name</th>", out.stdout)
        out = self.run_cli(str(self.path))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("apple", out.stdout)
        self.assertIn("pear, ripe", out.stdout)

    def test_errors(self):
        out = self.run_cli(str(Path(self.dir.name) / "missing.csv"))
        self.assertEqual(out.returncode, 1)
        self.assertIn("no such file", out.stderr)
        self.path.write_text("a,b\n1,2\n1,2,3\n", encoding="utf-8")
        out = self.run_cli(str(self.path), "--format", "csv")
        self.assertEqual(out.returncode, 1)
        self.assertIn("row 3", out.stderr)


def main() -> int:
    upstream = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    print("\n".join(upstream.stdout.strip().splitlines()[-3:]))
    if upstream.returncode != 0:
        print("Rich's own tests failed")
        return 1
    result = unittest.main(argv=["hidden_check"], exit=False, verbosity=1).result
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
