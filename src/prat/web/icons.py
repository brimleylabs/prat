"""Inline SVG icons.

The only place in the web UI that produces markup outside the template
engine. Shapes are constants; the optional label is escaped.
"""

from __future__ import annotations

from markupsafe import Markup, escape

_SHAPES: dict[str, str] = {
    "board": '<rect x="3" y="4" width="5" height="16" rx="1.5"/><rect x="10" y="4" width="5" '
    'height="11" rx="1.5"/><rect x="17" y="4" width="4" height="7" rx="1.5"/>',
    "folder": '<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6H9l2 2h8.5A1.5 1.5 0 0 1 21 9.5v8A1.5 1.5 0 '
    '0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5z"/>',
    "sliders": '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" '
    'cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
    "database": '<ellipse cx="12" cy="6" rx="7.5" ry="3"/><path d="M4.5 6v12c0 1.7 3.4 3 '
    '7.5 3s7.5-1.3 7.5-3V6"/><path d="M4.5 12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3"/>',
    "copy": '<rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2"/><path d="M15.5 8.5V5.5A1.5 '
    '1.5 0 0 0 14 4H5.5A1.5 1.5 0 0 0 4 5.5V14a1.5 1.5 0 0 0 1.5 1.5h3"/>',
    "loop": '<path d="M4 11a8 8 0 0 1 14-4.5L20 8"/><path d="M20 3.5V8h-4.5"/><path d="M20 13a8 8 '
    '0 0 1-14 4.5L4 16"/><path d="M4 20.5V16h4.5"/>',
    "chart": '<path d="M4 4v16h16"/><path d="M8 16v-4M12 16V8M16 16v-6"/>',
    "graph": '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="8" r="2.5"/>'
    '<circle cx="9" cy="18" r="2.5"/><path d="M8.4 6.6l7.2 1M7 8.3l1.4 7.3M16.4 9.9l-5.6 6.4"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    "maximize": '<path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3'
    'M16 21h3a2 2 0 0 0 2-2v-3"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "play": '<path d="M7 5.5v13a.5.5 0 0 0 .77.42l10-6.5a.5.5 0 0 0 0-.84l-10-6.5A.5.5 0 0 0 '
    '7 5.5z"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "undo": '<path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "merge": '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" '
    'cy="12" r="2"/><path d="M6 7v10M6 7c0 3 3 5 10 5"/>',
    "branch": '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" '
    'cy="7" r="2"/><path d="M6 7v10M18 9c0 5-6 4-11.5 8.5"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "check-circle": '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.8 2.8L16 10"/>',
    "x-circle": '<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
    "alert": '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17.5v0"/>',
    "minus-circle": '<circle cx="12" cy="12" r="9"/><path d="M8 12h8"/>',
    "circle": '<circle cx="12" cy="12" r="8"/>',
    "dot": '<circle cx="12" cy="12" r="3.5" fill="currentColor"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 '
    '12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
    "moon": '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
    "monitor": '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M8 20h8M12 16v4"/>',
    "keyboard": '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h0M10 10h0M14 '
    '10h0M18 10h0M7 14h10"/>',
    "logout": '<path d="M15 4h3.5A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5H15"/><path '
    'd="M10 8l-4 4 4 4M6 12h10"/>',
    "arrow-right": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "chevron-right": '<path d="M9 6l6 6-6 6"/>',
    "chevron-down": '<path d="M6 9l6 6 6-6"/>',
    "file": '<path d="M14 3H7.5A1.5 1.5 0 0 0 6 4.5v15A1.5 1.5 0 0 0 7.5 21h9a1.5 1.5 0 0 0 '
    '1.5-1.5V7z"/><path d="M14 3v4h4"/>',
    "terminal": '<rect x="3" y="4.5" width="18" height="15" rx="2"/><path d="M7 9.5l3 2.5-3 '
    '2.5M12.5 15H17"/>',
    "shield": '<path d="M12 3l7.5 3v5.5c0 4.5-3.2 8-7.5 9.5-4.3-1.5-7.5-5-7.5-9.5V6z"/>',
    "shield-x": '<path d="M12 3l7.5 3v5.5c0 4.5-3.2 8-7.5 9.5-4.3-1.5-7.5-5-7.5-9.5V6z"/><path '
    'd="M9.5 9.5l5 5M14.5 9.5l-5 5"/>',
    "message": '<path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v9a1.5 1.5 0 0 1-1.5 '
    '1.5H10l-4.5 4v-4h0A1.5 1.5 0 0 1 4 14.5z"/>',
    "history": '<path d="M3.5 12a8.5 8.5 0 1 0 2.5-6"/><path d="M3 4v4h4"/><path d="M12 8v4.5l3 '
    '1.5"/>',
    "replay": '<path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 4.5v4h4"/><path d="M10.5 9.5v5l4-'
    '2.5z"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11M4.5 6h0M4.5 12h0M4.5 18h0"/>',
    "cpu": '<rect x="6" y="6" width="12" height="12" rx="1.5"/><path d="M10 2.5V6M14 2.5V6M10 '
    '18v3.5M14 18v3.5M2.5 10H6M2.5 14H6M18 10h3.5M18 14h3.5"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M16 7l2.5 2.5M14 9l2 2"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 7.5v0"/>',
    "lock": '<rect x="5" y="10.5" width="14" height="10" rx="1.5"/><path d="M8 10.5V7.5a4 4 0 0 '
    '1 8 0v3"/>',
    "send": '<path d="M4 12l16-8-6 16-2.5-6.5z"/><path d="M11.5 13.5L20 4"/>',
    "edit": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
    "inbox": '<path d="M3.5 13.5l2.5-8h12l2.5 8v5a1.5 1.5 0 0 1-1.5 1.5h-14A1.5 1.5 0 0 1 3.5 '
    '18.5z"/><path d="M3.5 13.5H8l1.5 2.5h5l1.5-2.5h4.5"/>',
    "filter": '<path d="M4 5h16l-6 7.5V19l-4-2v-4.5z"/>',
    "external": '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 '
    '1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
}

# PRAT's mark: a navy P on an amber tile.
LOGO = Markup(
    '<svg class="logo-mark" width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" '
    'focusable="false"><rect x="1" y="1" width="22" height="22" rx="6" class="logo-bg"/>'
    '<path d="M9 18V6h4a3.5 3.5 0 0 1 0 7H9" class="logo-fg" fill="none" '
    'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>'
    "</svg>"
)


def icon(name: str, size: int = 16, label: str | None = None) -> Markup:
    shape = _SHAPES[name]
    aria = f'role="img" aria-label="{escape(label)}"' if label else 'aria-hidden="true"'
    # Constant shapes plus an escaped label: nothing untrusted reaches this markup.
    return Markup(  # noqa: S704
        f'<svg class="icon icon-{name}" width="{int(size)}" height="{int(size)}" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" '
        f'stroke-linecap="round" stroke-linejoin="round" focusable="false" {aria}>'
        f"{shape}</svg>"
    )


def names() -> list[str]:
    return sorted(_SHAPES)
