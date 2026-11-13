# coding=utf-8

MARKDOWN = """Heading
=======

Sub-heading
-----------

### Heading

#### H4 Heading

##### H5 Heading

###### H6 Heading


Paragraphs are separated
by a blank line.

Two spaces at the end of a line
produces a line break.

Text attributes _italic_,
**bold**, `monospace`.

Horizontal rule:

---

Bullet list:

  * apples
  * oranges
  * pears

Numbered list:

  1. lather
  2. rinse
  3. repeat

An [example](http://example.com).

> Markdown uses email-style > characters for blockquoting.
>
> Lorem ipsum

![progress](https://github.com/textualize/rich/raw/master/imgs/progress.gif)


```
a=1
```

```python
import this
```

```somelang
foobar
```

"""

import io
import re

from rich.console import Console, RenderableType
from rich.markdown import Markdown

re_link_ids = re.compile(r"id=[\d\.\-]*?;.*?\x1b")


def replace_link_ids(render: str) -> str:
    """Link IDs have a random ID and system path which is a problem for
    reproducible tests.

    """
    return re_link_ids.sub("id=0;foo\x1b", render)


def render(renderable: RenderableType) -> str:
    console = Console(
        width=100, file=io.StringIO(), color_system="truecolor", legacy_windows=False
    )
    console.print(renderable)
    output = replace_link_ids(console.file.getvalue())
    return output




if __name__ == "__main__":
    markdown = Markdown(MARKDOWN, hyperlinks=False)
    rendered = render(markdown)
    print(rendered)
    print(repr(rendered))
