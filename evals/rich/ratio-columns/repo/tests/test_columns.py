# encoding=utf-8

import io

from rich.columns import Columns
from rich.console import Console

COLUMN_DATA = [
    "Ursus americanus",
    "American buffalo",
    "Bison bison",
    "American crow",
    "Corvus brachyrhynchos",
    "American marten",
    "Martes americana",
    "American racer",
    "Coluber constrictor",
    "American woodcock",
    "Scolopax minor",
    "Anaconda (unidentified)",
    "Eunectes sp.",
    "Andean goose",
    "Chloephaga melanoptera",
    "Ant",
    "Anteater, australian spiny",
    "Tachyglossus aculeatus",
    "Anteater, giant",
]


def render():
    console = Console(file=io.StringIO(), width=100, legacy_windows=False)

    console.rule("empty")
    empty_columns = Columns([])
    console.print(empty_columns)
    columns = Columns(COLUMN_DATA)
    columns.add_renderable("Myrmecophaga tridactyla")
    console.rule("optimal")
    console.print(columns)
    console.rule("optimal, expand")
    columns.expand = True
    console.print(columns)
    console.rule("column first, optimal")
    columns.column_first = True
    columns.expand = False
    console.print(columns)
    console.rule("column first, right to left")
    columns.right_to_left = True
    console.print(columns)
    console.rule("equal columns, expand")
    columns.equal = True
    columns.expand = True
    console.print(columns)
    console.rule("fixed width")
    columns.width = 16
    columns.expand = False
    console.print(columns)
    console.print()
    render_result = console.file.getvalue()
    print(render_result)
    print(repr(render_result))
    return render_result




if __name__ == "__main__":
    result = render()
    print(result)
    print(repr(result))
