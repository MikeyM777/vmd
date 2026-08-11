"""The visual system from DESIGN.md, in the form Qt understands.

DESIGN.md is written in OKLCH because that is how the colours were chosen. Qt
stylesheets cannot parse OKLCH, so they are converted once here. The conversions
are exact; if a colour changes in DESIGN.md it is converted again rather than
adjusted by eye.

The type scale and the spacing rhythm live here for the same reason the colours
do: they are decisions, and a decision repeated by hand in six files is six
chances to get it wrong. Everything that draws itself reads its sizes from these
names rather than from a number typed at the point of use.
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

# ------------------------------------------------------------------ type scale
#
# Five sizes and no more. Everything used to be one size, which is the same as
# having no hierarchy at all: the sentence saying nothing is being recorded was
# drawn exactly like the sentence explaining what an arrow key does.
#
# The numbers are logical pixels, and the case that decides them is Windows
# display scaling: a 1920x1080 laptop panel at 150% reports 1280x720 logical
# pixels to Qt, so one logical pixel here is 1.5 real ones on the console's own
# screen. BAND is what an operator two metres back has to be able to read, and
# at 150% it lands at 24 real pixels.
SIZE_BAND = 16     # the state of the whole system, read from across the room
SIZE_TITLE = 14    # tab labels, the name of the application
SIZE_BODY = 13     # the default: values, sentences, form fields
SIZE_SMALL = 12    # notes and captions under something else
SIZE_HEADING = 11  # section headings, table headers, units

WEIGHT_HEADING = 600
WEIGHT_VALUE = 600

# --------------------------------------------------------------- spacing scale
#
# DESIGN.md's rhythm, named. Tight by web standards and correct for a briefed
# operator reading dense telemetry: the panels are close together and the gap
# BETWEEN groups is what separates them, not padding inside each one.
SPACE_HAIR = 2
SPACE_TIGHT = 4
SPACE_SNUG = 6
SPACE_STEP = 9
SPACE_ROOM = 12
SPACE_GROUP = 18
SPACE_WIDE = 22

# How wide a form is allowed to get. A settings form is a column of short
# fields, and a 13-character address stretched across 1900 px of a 4K panel is
# the single loudest thing wrong with that tab: the eye has to travel the whole
# screen to get from a label to the box it belongs to. Past this the column
# stops growing and the space goes to the margins.
FORM_MAX_WIDTH = 980

# What each state looks like: the colour, and the glyph that carries the same
# meaning for anyone who cannot tell the colours apart. DESIGN.md: colour never
# carries meaning alone.
STATE_GLYPHS: dict[str, str] = {
    "ok": "●",     # a filled circle
    "warn": "▲",   # a triangle
    "alarm": "■",  # a filled bar
    "muted": "○",  # a hollow circle: nothing is known
}


def state_colour(state: str) -> str:
    """The palette colour for a state name, defaulting to the quiet one."""
    return PALETTE.get(state, PALETTE["muted"])


def state_glyph(state: str) -> str:
    """The glyph for a state name. Never empty: the glyph is half the signal."""
    return STATE_GLYPHS.get(state, STATE_GLYPHS["muted"])


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
    font-size: {SIZE_BODY}px;
}}
/* A plain container carries no colour of its own, so whatever panel it sits on
   shows through it. Only the surfaces below are painted; everything else is a
   hole. Without this a group box cannot have a background at all, because every
   layout widget inside it would draw the page colour back over it. The leading
   dot is Qt's "this class exactly", so QLineEdit and the rest keep theirs. */
.QWidget {{ background: transparent; }}
QLabel, QCheckBox, QRadioButton, QSplitter {{ background: transparent; }}

QTabWidget::pane {{
    border: 0;
    border-top: 1px solid {p["line"]};
    background: {p["bg"]};
}}
QTabBar {{ background: {p["surface"]}; }}
QTabBar::tab {{
    background: {p["surface"]};
    color: {p["muted"]};
    font-size: {SIZE_TITLE}px;
    padding: {SPACE_STEP}px {SPACE_WIDE}px;
    border: 0;
    /* The active tab is marked by a bar in the accent, which is the one thing
       DESIGN.md allows amber on: the state of an active control. It is the only
       amber at rest anywhere in the window. */
    border-bottom: 2px solid transparent;
    border-radius: 0;
}}
QTabBar::tab:hover {{ color: {p["ink"]}; }}
QTabBar::tab:selected {{
    background: {p["bg"]};
    color: {p["ink"]};
    font-weight: {WEIGHT_VALUE};
    border-bottom: 2px solid {p["accent"]};
}}

QGroupBox {{
    background: {p["surface"]};
    border: 1px solid {p["line"]};
    border-radius: 0;
    margin-top: {SPACE_ROOM}px;
    padding: {SPACE_ROOM}px {SPACE_STEP}px {SPACE_STEP}px {SPACE_STEP}px;
    font-size: {SIZE_HEADING}px;
    font-weight: {WEIGHT_HEADING};
}}
/* The heading sits ON the panel's top edge rather than floating above it, and
   it is smaller and heavier than the body it heads. It used to be the same
   weight and a larger size than its own contents, which read as a label for
   nothing. */
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: {SPACE_SNUG}px;
    padding: 0 {SPACE_SNUG}px;
    background: {p["surface"]};
    color: {p["muted"]};
    font-size: {SIZE_HEADING}px;
    font-weight: {WEIGHT_HEADING};
}}

QPushButton {{
    background: {p["raised"]};
    color: {p["ink"]};
    border: 1px solid {p["line"]};
    border-radius: 0;
    padding: {SPACE_SNUG}px {SPACE_ROOM}px;
}}
QPushButton:hover {{ background: {p["line"]}; }}
QPushButton:pressed {{ border-color: {p["accent"]}; }}
QPushButton:focus {{ border-color: {p["accent"]}; }}
QPushButton:disabled {{ color: {p["muted"]}; background: {p["surface"]}; }}
/* The one button on a page that the page exists for. Marked by weight and by a
   heavier edge rather than by colour: amber on a resting control is exactly the
   drift DESIGN.md warns about. */
QPushButton[primary="true"] {{
    background: {p["line"]};
    border: 1px solid {p["line_strong"]};
    font-weight: {WEIGHT_VALUE};
    padding: {SPACE_STEP}px {SPACE_WIDE}px;
}}
QPushButton[primary="true"]:hover {{ background: {p["line_strong"]}; }}

QLineEdit, QComboBox, QSpinBox, QDateEdit, QDoubleSpinBox {{
    background: {p["raised"]};
    color: {p["ink"]};
    border: 1px solid {p["line"]};
    border-radius: 0;
    padding: {SPACE_TIGHT}px {SPACE_SNUG}px;
    selection-background-color: {p["line_strong"]};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus {{
    border-color: {p["accent"]};
}}
QComboBox::drop-down, QDateEdit::drop-down {{
    border: 0;
    width: {SPACE_GROUP}px;
}}
QComboBox QAbstractItemView {{
    background: {p["raised"]};
    border: 1px solid {p["line"]};
    selection-background-color: {p["line_strong"]};
}}

/* Recessed, but never the video well: DESIGN.md keeps that colour for pictures
   and nothing else, and a near-black rectangle in the middle of a panel reads
   as a hole rather than as a list. */
QTableWidget, QTableView, QPlainTextEdit, QListWidget {{
    background: {p["bg"]};
    border: 1px solid {p["line"]};
    border-radius: 0;
    font-family: {MONO};
    gridline-color: {p["surface"]};
    selection-background-color: {p["line"]};
    selection-color: {p["ink"]};
}}
QTableView::item {{ padding: {SPACE_TIGHT}px {SPACE_SNUG}px; }}
QHeaderView {{ background: {p["surface"]}; }}
QHeaderView::section {{
    background: {p["surface"]};
    color: {p["muted"]};
    border: 0;
    border-bottom: 1px solid {p["line"]};
    padding: {SPACE_TIGHT}px {SPACE_SNUG}px;
    font-family: system-ui;
    font-size: {SIZE_HEADING}px;
    font-weight: {WEIGHT_HEADING};
}}

/* Wide enough to be a handle. It was a hairline, which is invisible between two
   near-black pictures and impossible to grab. */
QSplitter::handle {{ background: {p["line"]}; }}
QSplitter::handle:horizontal {{ width: {SPACE_SNUG}px; }}
QSplitter::handle:vertical {{ height: {SPACE_SNUG}px; }}
QSplitter::handle:hover {{ background: {p["line_strong"]}; }}

QScrollBar:vertical {{
    background: {p["bg"]};
    width: {SPACE_ROOM}px;
    margin: 0;
    border: 0;
}}
QScrollBar:horizontal {{
    background: {p["bg"]};
    height: {SPACE_ROOM}px;
    margin: 0;
    border: 0;
}}
QScrollBar::handle {{ background: {p["line"]}; border-radius: 0; }}
QScrollBar::handle:hover {{ background: {p["line_strong"]}; }}
QScrollBar::handle:vertical {{ min-height: {SPACE_WIDE}px; }}
QScrollBar::handle:horizontal {{ min-width: {SPACE_WIDE}px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; border: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{
    background: {p["raised"]};
    color: {p["ink"]};
    border: 1px solid {p["line_strong"]};
    padding: {SPACE_SNUG}px;
}}
QStatusBar {{
    background: {p["surface"]};
    color: {p["muted"]};
    border-top: 1px solid {p["line"]};
}}
QStatusBar::item {{ border: 0; }}
"""
