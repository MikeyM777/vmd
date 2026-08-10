"""The visual system from DESIGN.md, in the form Qt understands.

DESIGN.md is written in OKLCH because that is how the colours were chosen. Qt
stylesheets cannot parse OKLCH, so they are converted once here. The conversions
are exact; if a colour changes in DESIGN.md it is converted again rather than
adjusted by eye.
"""

from __future__ import annotations

# oklch(L C H) -> sRGB, converted from the table in DESIGN.md.
PALETTE: dict[str, str] = {
    "bg": "#1B1D20",           # oklch(0.23 0.006 265)
    "surface": "#27292C",      # oklch(0.28 0.007 265)
    "raised": "#33353A",       # oklch(0.33 0.008 265)
    "well": "#050607",         # oklch(0.12 0.005 265) - video only
    "line": "#45484D",         # oklch(0.40 0.010 265)
    "line_strong": "#656970",  # oklch(0.52 0.012 265)
    "ink": "#F4F5F7",          # oklch(0.97 0.003 265)
    "muted": "#B4B7BE",        # oklch(0.78 0.010 265)
    "ok": "#6ED889",           # oklch(0.80 0.15 150)
    "warn": "#FFBC56",         # oklch(0.84 0.14 75)
    "alarm": "#FF534B",        # oklch(0.68 0.21 27)
    "accent": "#EEBB58",       # oklch(0.82 0.13 82) - interactive emphasis only
}

MONO = '"Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace'


def stylesheet() -> str:
    """The whole application's appearance.

    Radius 0 throughout: square corners are the strongest single signal
    separating equipment from web application, and they cost nothing.
    """
    p = PALETTE
    return f"""
QWidget {{
    background: {p["bg"]};
    color: {p["ink"]};
    font-size: 12pt;
}}
QTabWidget::pane {{ border: 1px solid {p["line"]}; }}
QTabBar::tab {{
    background: {p["surface"]};
    color: {p["muted"]};
    padding: 7px 16px;
    border: 1px solid transparent;
    border-radius: 0;
}}
QTabBar::tab:selected {{
    background: {p["raised"]};
    color: {p["ink"]};
    border-color: {p["line"]};
}}
QGroupBox {{
    border: 1px solid {p["line"]};
    border-radius: 0;
    margin-top: 14px;
    padding-top: 8px;
}}
QGroupBox::title {{
    color: {p["muted"]};
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
}}
QPushButton {{
    background: {p["surface"]};
    color: {p["ink"]};
    border: 1px solid {p["line"]};
    border-radius: 0;
    padding: 6px 13px;
}}
QPushButton:hover {{ background: {p["raised"]}; }}
QPushButton:focus {{ border-color: {p["accent"]}; }}
QPushButton:disabled {{ color: {p["muted"]}; }}
QLineEdit, QComboBox, QSpinBox {{
    background: {p["raised"]};
    color: {p["ink"]};
    border: 1px solid {p["line"]};
    border-radius: 0;
    padding: 5px 7px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {p["accent"]}; }}
QTableWidget, QTableView, QPlainTextEdit {{
    background: {p["well"]};
    border: 1px solid {p["line"]};
    border-radius: 0;
    font-family: {MONO};
}}
QHeaderView::section {{
    background: {p["surface"]};
    color: {p["muted"]};
    border: 0;
    border-bottom: 1px solid {p["line"]};
    padding: 4px 6px;
}}
QSplitter::handle {{ background: {p["line_strong"]}; }}
QStatusBar {{ background: {p["surface"]}; color: {p["muted"]}; }}
"""
