"""Turn the two operator guides into two PDFs, using nothing but PySide6.

    uv run python scripts/guide_pdf.py

The machine this ends up on has no internet and `uv sync` cannot be run there,
so this script may not add a dependency to the project. That rules out every
Markdown and PDF library there is, and it is why there is a small Markdown
reader further down this file instead of an import. PySide6 is already here -
the console is built on it - and `QTextDocument` can lay out HTML and print it
to a `QPdfWriter`, which is the whole of what a guide needs.

The Hebrew one is the one that matters, and it is the one with something that
can go quietly wrong: a PDF whose Hebrew came out as boxes, or as left-aligned
lines with the punctuation on the wrong end, still opens and still looks like a
document. So two things are done on purpose rather than hoped for. The
document's default text direction is set to right-to-left, which is what puts
the text against the right margin and the list markers on the right of the
list. And the font is named rather than left to whatever Qt would pick: Arial
and Segoe UI both carry Hebrew on Windows, and `_font_with_hebrew` checks that
the one it chose really does before the document is built, so a machine with a
stripped font set is a message on the screen rather than a PDF full of boxes.

The pictures come from `docs/guide/images/shots.json`, which another part of
this work writes. Each shot has numbered circles drawn on it and no words at
all, and the words are in the guide - which is what makes one set of pictures
serve both languages. A shot the manifest does not have is skipped with a line
on the screen rather than treated as an error: the guide is worth reading
without its pictures, and a guide that refuses to build because a screenshot
has not been taken yet is a guide nobody can read at all.

--------------------------------------------------------------------------
The Markdown this understands, which is only what the two guides use
--------------------------------------------------------------------------

    # Heading            one per chapter; every one but the first starts a page
    ## Heading           a section
    ### Heading          a step or a field
    Plain text           a paragraph, ended by a blank line
    **bold**             bold, which is how a button or a field is named
    `text`               words quoted off the screen exactly as they appear
                         there, set in a typewriter face so that a sentence
                         about them cannot be mistaken for one of them
    - item               a bulleted list
    1. item              a numbered list. The numbers in the file are ignored
                         and the list is numbered from one, as HTML does it
      more of it         an indented line under a list item is the rest of that
                         item, so a long item can be wrapped in the file at the
                         same width as everything around it
    | a | b |            a table. The first row is the header and the second
    |---|---|            row is the dashes that say so
    ![caption](shot:live-tab)
                         the picture whose slug is `live-tab` in shots.json,
                         with the caption printed under it. A caption may be
                         empty
    ![caption](some.png) a picture by path, relative to the guide's own folder
    [[pagebreak]]        start a new page here

Anything else is passed through as text, with `&`, `<` and `>` escaped. There
is no nesting: a list item is one line, and a table cell is one line. That is
all the two guides need, and every rule above is one somebody has to be able to
hold in their head while editing them.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QMarginsF, QRectF, QSizeF, QUrl, Qt
from PySide6.QtGui import (
    QFont,
    QGuiApplication,
    QImage,
    QPageLayout,
    QPageSize,
    QPainter,
    QPdfWriter,
    QRawFont,
    QTextDocument,
    QTextOption,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GUIDE_DIR = PROJECT_ROOT / "docs" / "guide"

# The two guides and what each one is called when it is a PDF.
#
# The Hebrew one is first because it is the one the operator reads; the English
# one exists for whoever keeps the system running, and is the same guide word
# for word.
GUIDES = (
    ("he", "guide-he.md", "VMD-User-Guide-he.pdf", "מדריך למפעיל VMD"),
    ("en", "guide-en.md", "VMD-User-Guide-en.pdf", "VMD Operator's Guide"),
)

# Which of these is written right to left. One entry today, and a set rather
# than a comparison so that adding a third language is adding a word here.
RIGHT_TO_LEFT = {"he"}

# The page. A4 because that is what the printer in that building has, and
# margins wide enough that a page held in one hand is not covered by the thumb.
PAGE_MARGIN_MM = 18.0

# How many dots of a screenshot are drawn per inch of paper.
#
# A screenshot is 1600 dots across and a page is under seven inches wide, so
# every one of these is going to be scaled down; the only question is by how
# much. At 150 the 1600-dot shots are wider than the page and are therefore
# drawn across the whole of it, and a small crop - a zoom bar, a single button -
# comes out at about the size it is on the screen instead of being blown up to
# the full width, which is what makes a crop of one control look like a mistake.
ASSET_DPI = 150.0

# The resolution the PDF is written at. It decides nothing about how sharp the
# text is - text in a PDF is drawn as outlines and has no resolution - but it is
# the unit every length in the laid-out document is measured in, so it wants to
# be high enough that a picture's width lands on a whole number of them.
PDF_RESOLUTION = 300

# The most of one page a single picture is ever allowed to take. Under 1, so
# that a picture which is exactly a page tall still has the caption under it and
# a line of text above it rather than being alone on a page of its own.
MOST_OF_A_PAGE = 0.86

# The faces that carry Hebrew on a Windows machine, in the order they are tried.
# Named rather than left to Qt: a document that falls back per character mixes
# two faces in one line, and the point of a guide is that it does not look like
# a machine made it.
FONT_CANDIDATES = ("Arial", "Segoe UI", "Tahoma", "David")

# The size the guide is set in. Larger than a document would normally be,
# because it is read by somebody who is not looking for an excuse to read it.
BODY_POINT_SIZE = 11.5


class GuideError(Exception):
    """Something that stops a PDF being made, in words rather than a traceback."""


# --------------------------------------------------------------- the pictures


@dataclass(frozen=True)
class Shot:
    """One annotated screenshot, as the manifest describes it."""

    slug: str
    path: Path
    width: int
    height: int
    # How many numbered circles are drawn on it. Kept for one reason, and it is
    # the reason this whole check exists: the circles are on the picture and the
    # words that explain them are in the guide, written by a different hand at a
    # different time. If those two ever disagree the guide points at the wrong
    # part of the screen and nothing about it looks wrong.
    callouts: int = 0


@dataclass
class Report:
    """What was not right about the guide, gathered while it was being built."""

    missing: list[str]
    mismatched: list[str]


def read_shots(images_dir: Path) -> dict[str, Shot]:
    """Every picture the manifest lists, keyed by slug and by a loose form of it.

    Loose as well as exact, because the guide and the manifest are written by
    two different hands: `live-tab`, `live_tab` and `Live Tab` are one picture
    to anybody reading either file, and a guide that silently lost its
    screenshots over a hyphen would be a guide nobody could see was wrong.

    A manifest that is not there at all is not an error. The pictures are taken
    separately from the words, and the words are worth printing on their own.
    """
    manifest = images_dir / "shots.json"
    if not manifest.exists():
        print(f"  no {manifest} yet - building without pictures")
        return {}
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GuideError(f"{manifest} could not be read: {exc}") from exc

    shots: dict[str, Shot] = {}
    for entry in payload.get("shots") or []:
        slug = str(entry.get("slug") or "").strip()
        name = str(entry.get("file") or "").strip()
        if not slug or not name:
            continue
        path = images_dir / name
        width = int(entry.get("width") or 0)
        height = int(entry.get("height") or 0)
        if width <= 0 or height <= 0:
            # The manifest is allowed not to know; the file does. Reading it
            # here rather than guessing keeps a picture from being stretched.
            width, height = _size_of(path)
        shot = Shot(
            slug=slug,
            path=path,
            width=width,
            height=height,
            callouts=len(entry.get("callouts") or []),
        )
        shots[slug] = shot
        shots.setdefault(_loose(slug), shot)
    return shots


def _size_of(path: Path) -> tuple[int, int]:
    image = QImage(str(path))
    if image.isNull():
        return (0, 0)
    return (image.width(), image.height())


def _loose(slug: str) -> str:
    """A slug with everything but its letters and digits taken out of it."""
    return re.sub(r"[^a-z0-9]", "", slug.lower())


# --------------------------------------------------------------- the Markdown

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_QUOTED = re.compile(r"`([^`]+)`")
_IMAGE = re.compile(r"^!\[(?P<caption>.*)\]\((?P<target>[^)]+)\)\s*$")
_TABLE_RULE = re.compile(r"^\|[\s:|-]+\|$")


def escape(text: str) -> str:
    """The three characters Qt's HTML reader would take as markup."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def inline(text: str) -> str:
    """A run of text, with the two marks that mean something inside a line.

    Both are about the same problem. This guide is written in one language
    about a screen written in another, so the reader has to be able to tell
    "press the button called Save" from "the screen says NOT recording". Bold
    names a control; the typewriter face quotes what the screen itself says,
    word for word, so a sentence about the words on the screen cannot be read
    as one of them.
    """
    return _QUOTED.sub(
        r"<span class='screen'>\1</span>", _BOLD.sub(r"<b>\1</b>", escape(text))
    )


def markdown_to_html(
    source: str,
    shots: dict[str, Shot],
    base_dir: Path,
    content: tuple[int, int],
    report: Report,
) -> str:
    """The guide as the HTML `QTextDocument` will lay out.

    Written as one pass over the lines with a tiny amount of state - which list
    is open, whether a table is being read - because the subset at the top of
    this file has no nesting in it. Anything cleverer would be a parser nobody
    asked for, on a document only two files are ever fed to.

    `report` is filled in rather than raised on: a picture that has not been
    taken yet is a gap in the guide, and a guide with a gap in it is still the
    thing the operator has to read tomorrow morning.

    The one thing it does check as it goes is that a numbered list straight
    under a picture has as many items as the picture has circles on it. That
    pairing is the whole design of these guides - the circles carry no words,
    so the list is the only thing that says what they are pointing at - and it
    is the one kind of wrongness that a proofread of either file on its own
    cannot catch.
    """
    lines = source.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    # What is open: "ul", "ol", "table" or nothing. One at a time, by the same
    # rule as everything else here - the guides do not nest anything.
    open_block = ""
    first_chapter = True
    # The lines of the paragraph being read. A paragraph is several lines in the
    # file and one paragraph on the page - which is what lets the guides be
    # wrapped at a sane width in an editor, and is also the only way a `**bold
    # phrase**` that happens to fall over a line break in the file still comes
    # out bold.
    paragraph: list[str] = []
    # The lines of the list item being read, for exactly the same reason. It is
    # kept as text and not as HTML until the item ends, because a `**bold
    # phrase**` wrapped over two lines has its two halves on two lines, and
    # marking up each line as it arrives pairs neither of them.
    item: list[str] = []
    # The picture the next numbered list is about, and how many items it has had
    # so far. Cleared by anything that is not that list.
    explaining: Shot | None = None
    explained = 0

    def end_item() -> None:
        if item:
            out.append(f"<li>{inline(' '.join(item))}</li>")
            item.clear()

    def close() -> None:
        nonlocal open_block, explaining, explained
        if open_block == "p":
            out.append(f"<p>{inline(' '.join(paragraph))}</p>")
            paragraph.clear()
        elif open_block == "ul":
            end_item()
            out.append("</ul>")
        elif open_block == "ol":
            end_item()
            out.append("</ol>")
            if explaining is not None and explaining.callouts != explained:
                report.mismatched.append(
                    f"{explaining.slug}: {explaining.callouts} circles on the "
                    f"picture, {explained} explained in the guide"
                )
            explaining = None
            explained = 0
        elif open_block == "table":
            out.append("</table>")
        open_block = ""

    index = 0
    while index < len(lines):
        raw = lines[index]
        line = raw.strip()
        index += 1

        if not line:
            close()
            continue

        # An indented line while a list is open is the rest of the item above
        # it. Without this a list item wrapped over two lines in the file comes
        # out as a list of one item followed by a paragraph, which is not what
        # anybody typing it meant and is exactly how the count of items under a
        # picture stops agreeing with the circles on it.
        if open_block in ("ul", "ol") and raw[:1].isspace() and item:
            item.append(line)
            continue

        if line == "[[pagebreak]]":
            close()
            explaining = None
            out.append(_PAGE_BREAK)
            continue

        if line.startswith("### "):
            close()
            explaining = None
            out.append(f"<h3>{inline(line[4:])}</h3>")
            continue
        if line.startswith("## "):
            close()
            explaining = None
            out.append(f"<h2>{inline(line[3:])}</h2>")
            continue
        if line.startswith("# "):
            close()
            explaining = None
            # Every chapter but the first starts a page of its own. A guide
            # somebody is working through wants to be openable at a chapter.
            #
            # Unless the last thing written was already a break, which is a
            # `[[pagebreak]]` written by hand in front of a chapter that was
            # going to start a page anyway. Two breaks in a row is not a bigger
            # gap; it is one entirely blank page in the middle of the guide, and
            # a blank page reads as a printing fault.
            if not first_chapter and (not out or out[-1] != _PAGE_BREAK):
                out.append(_PAGE_BREAK)
            first_chapter = False
            out.append(f"<h1>{inline(line[2:])}</h1>")
            continue

        picture = _IMAGE.match(line)
        if picture is not None:
            close()
            html, explaining = _picture_html(
                picture.group("caption"),
                picture.group("target"),
                shots,
                base_dir,
                content,
                report,
            )
            explained = 0
            out.append(html)
            continue

        if line.startswith("| "):
            explaining = None
            if open_block != "table":
                close()
                out.append(
                    "<table width='100%' cellspacing='0' cellpadding='6' "
                    "border='1'>"
                )
                open_block = "table"
                out.append(_table_row(line, header=True))
                # The row of dashes under the header says nothing this needs.
                if index < len(lines) and _TABLE_RULE.match(lines[index].strip()):
                    index += 1
                continue
            out.append(_table_row(line, header=False))
            continue

        if line.startswith("- "):
            explaining = None
            if open_block != "ul":
                close()
                out.append("<ul>")
                open_block = "ul"
            end_item()
            item.append(line[2:])
            continue

        numbered = re.match(r"^\d+\.\s+(.*)$", line)
        if numbered is not None:
            if open_block != "ol":
                close()
                out.append("<ol>")
                open_block = "ol"
            end_item()
            explained += 1
            item.append(numbered.group(1))
            continue

        if open_block != "p":
            close()
            explaining = None
            open_block = "p"
        paragraph.append(line)

    close()
    return "\n".join(out)


# How a page break is asked for. Qt's rich text has no page-break property, so
# what actually breaks the page is an empty block whose top margin is taller
# than the room left on the page - which is what `page-break-before` compiles
# to everywhere else anyway. Qt does honour it on a table, and a table with one
# empty cell is the smallest thing that carries it.
_PAGE_BREAK = (
    "<table style='page-break-before: always;' border='0' cellpadding='0' "
    "cellspacing='0'><tr><td></td></tr></table>"
)


def _table_row(line: str, header: bool) -> str:
    """One `| a | b |` line, as a row of cells."""
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    tag = "th" if header else "td"
    weight = " bgcolor='#e8e8e8'" if header else ""
    body = "".join(f"<{tag}{weight}>{inline(cell)}</{tag}>" for cell in cells)
    return f"<tr>{body}</tr>"


def _picture_html(
    caption: str,
    target: str,
    shots: dict[str, Shot],
    base_dir: Path,
    content: tuple[int, int],
    report: Report,
) -> tuple[str, Shot | None]:
    """One picture, at a width that fits the page, with its caption under it.

    Answers with the shot as well as the HTML, so that the numbered list under
    it can be counted against the circles drawn on it.

    The height is worked out here rather than left to Qt. Given only a width,
    Qt scales the picture's own height by nothing at all and draws it squashed
    or stretched, which on a screenshot with numbered circles on it means the
    circles no longer sit where the words say they do.
    """
    shot: Shot | None = None
    if target.startswith("shot:"):
        slug = target[len("shot:") :].strip()
        shot = shots.get(slug) or shots.get(_loose(slug))
        if shot is None or not shot.path.exists():
            report.missing.append(slug)
            return ("", None)
        source = shot.path.as_posix()
        natural_w, natural_h = shot.width, shot.height
    else:
        path = (base_dir / target).resolve()
        if not path.exists():
            report.missing.append(target)
            return ("", None)
        source = path.as_posix()
        natural_w, natural_h = _size_of(path)

    content_w, content_h = content
    width, height = content_w, 0
    if natural_w > 0 and natural_h > 0:
        width = min(content_w, int(natural_w / ASSET_DPI * PDF_RESOLUTION))
        height = int(width * natural_h / natural_w)
        # And never taller than the paper. A tall crop scaled only by its width
        # is a picture that cannot fit on any page, and Qt's answer to that is
        # to draw the top of it and put the rest nowhere.
        room = int(content_h * MOST_OF_A_PAGE)
        if height > room:
            width = int(width * room / height)
            height = room

    size = f" width='{width}'" + (f" height='{height}'" if height else "")
    # The caption is in the same paragraph as the picture, after a line break,
    # so that a page ending just below a screenshot cannot leave the words that
    # name it at the top of the next page with nothing above them.
    picture = f"<p class='figure' align='center'><img src='{escape(source)}'{size}>"
    if caption.strip():
        picture += f"<br><span class='caption'>{inline(caption)}</span>"
    return (picture + "</p>", shot)


# ------------------------------------------------------------------ the paper


def _font_with_hebrew() -> str:
    """The first face on this machine that can actually draw Hebrew.

    Checked rather than assumed. A face without Hebrew draws a box per letter,
    and a PDF full of boxes is still a PDF: it opens, it prints, and nobody
    finds out until it is in somebody's hands. `QRawFont` is the only thing in
    Qt that answers "does this font have this character" without drawing it.
    """
    hebrew = "אבגדהוזחטיכלמנסעפצקרשת"
    for family in FONT_CANDIDATES:
        raw = QRawFont.fromFont(QFont(family, 12))
        if all(raw.supportsCharacter(ord(letter)) for letter in hebrew):
            return family
    raise GuideError(
        "none of the fonts this script knows about can draw Hebrew on this "
        f"machine ({', '.join(FONT_CANDIDATES)}). Install one of them, or add "
        "one that is here to FONT_CANDIDATES."
    )


def dots(points: float) -> int:
    """A size in points, as the number of the PDF's own dots that makes it.

    Everything about this document is measured in one unit, and it is this one.
    A `QTextDocument` that has never been shown on a screen lays itself out
    against a nominal 96 dots per inch, so a size written as `11.5pt` in the
    stylesheet becomes about 15 of those - and then the pages this is printed
    onto are 2244 dots across, which is how a whole guide came out set in type
    a third of the size it should have been. Working in dots throughout removes
    the conversion rather than trying to get it right.
    """
    return max(1, int(round(points / 72.0 * PDF_RESOLUTION)))


def stylesheet(family: str) -> str:
    """How the guide is set. Every size is in the PDF's own dots - see `dots`."""
    return f"""
    body {{ font-family: '{family}'; font-size: {dots(BODY_POINT_SIZE)}px;
            color: #101010; }}
    h1 {{ font-size: {dots(21)}px; font-weight: bold; margin-top: {dots(6)}px;
          margin-bottom: {dots(10)}px; color: #101010; }}
    h2 {{ font-size: {dots(15)}px; font-weight: bold; margin-top: {dots(16)}px;
          margin-bottom: {dots(5)}px; color: #101010; }}
    h3 {{ font-size: {dots(12.5)}px; font-weight: bold; margin-top: {dots(12)}px;
          margin-bottom: {dots(3)}px; color: #202020; }}
    p {{ margin-top: {dots(4)}px; margin-bottom: {dots(4)}px; line-height: 140%; }}
    li {{ margin-top: {dots(3)}px; margin-bottom: {dots(3)}px; }}
    /* The markers sit outside the text, so a list with no margin of its own
       puts them against the edge of the paper - where a printer's own margin
       can take the top off a "3." nobody then knows was there. */
    ul, ol {{ margin-left: {dots(14)}px; margin-right: {dots(14)}px; }}
    td, th {{ font-size: {dots(BODY_POINT_SIZE - 1)}px; }}
    .caption {{ font-size: {dots(BODY_POINT_SIZE - 1.5)}px; color: #505050; }}
    .figure {{ line-height: 100%; margin-top: {dots(8)}px;
               margin-bottom: {dots(8)}px; }}
    .screen {{ font-family: 'Consolas', 'Courier New', monospace;
               font-size: {dots(BODY_POINT_SIZE - 1)}px; color: #202020; }}
    """


def make_pdf(
    source_path: Path,
    pdf_path: Path,
    title: str,
    right_to_left: bool,
    shots: dict[str, Shot],
    family: str,
) -> tuple[int, Report]:
    """Write one guide out as one PDF. Answers with its page count and its gaps."""
    writer = QPdfWriter(str(pdf_path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(
        QMarginsF(PAGE_MARGIN_MM, PAGE_MARGIN_MM, PAGE_MARGIN_MM, PAGE_MARGIN_MM),
        QPageLayout.Unit.Millimeter,
    )
    writer.setResolution(PDF_RESOLUTION)
    writer.setTitle(title)

    paper = writer.pageLayout().paintRectPixels(PDF_RESOLUTION)
    report = Report(missing=[], mismatched=[])
    body = markdown_to_html(
        source_path.read_text(encoding="utf-8"),
        shots,
        source_path.parent,
        (paper.width(), paper.height()),
        report,
    )

    document = QTextDocument()
    # Where the pictures are looked up from. Every `<img src>` this script
    # writes is already an absolute path, and this is here so that a path
    # written by hand into one of the guides resolves against the guide rather
    # than against whatever folder the script was started in.
    document.setBaseUrl(QUrl.fromLocalFile(str(source_path.parent) + "/"))
    document.setDefaultFont(_base_font(family))
    document.setDefaultStyleSheet(stylesheet(family))
    if right_to_left:
        # The whole document, and not a `dir` attribute on each paragraph.
        # This is what puts the text against the right margin, the bullets on
        # the right of the list and the first column of a table on the right -
        # and it is one setting, so it cannot be true of some of the guide and
        # false of the rest of it.
        option = QTextOption()
        option.setTextDirection(Qt.LayoutDirection.RightToLeft)
        option.setAlignment(Qt.AlignmentFlag.AlignRight)
        document.setDefaultTextOption(option)
    document.setHtml(f"<html><body>{body}</body></html>")
    # The size of one page, in the same dots everything else here is measured
    # in, and set before anything is drawn so that `pageCount` is the number of
    # pages that actually come out.
    document.setPageSize(QSizeF(paper.width(), paper.height()))

    _draw_pages(document, writer, paper.height())
    return (document.pageCount(), report)


def _draw_pages(document: QTextDocument, writer: QPdfWriter, page_height: int) -> None:
    """Paint the document onto the writer, one page at a time.

    Done here rather than by `QTextDocument.print_`, which does the same job and
    also decides for itself how to scale the document onto the page - and the
    scaling is exactly the thing this file has already been bitten by once. A
    page is a window onto the laid-out document, moved down by one page's height
    each time; there is nothing else to it, and nothing in it that can silently
    change the size of the type.

    The origin of a `QPdfWriter`'s painter is the top-left corner INSIDE the
    margins, so `(0, 0)` here is where the text starts on the paper.
    """
    painter = QPainter(writer)
    try:
        width = document.pageSize().width()
        for page in range(document.pageCount()):
            if page:
                writer.newPage()
            top = page * page_height
            painter.save()
            painter.translate(0, -top)
            document.drawContents(painter, QRectF(0, top, width, page_height))
            painter.restore()
    finally:
        painter.end()


def _base_font(family: str) -> QFont:
    font = QFont(family)
    font.setPixelSize(dots(BODY_POINT_SIZE))
    return font


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="guide_pdf",
        description="Make the operator guide PDFs from the Markdown beside them.",
    )
    parser.add_argument(
        "--images",
        default=str(GUIDE_DIR / "images"),
        help="the folder holding shots.json and the screenshots it names",
    )
    parser.add_argument(
        "--out",
        default=str(GUIDE_DIR),
        help="where to write the PDFs",
    )
    args = parser.parse_args(argv)

    # A QGuiApplication and not a QApplication: this draws no widgets, and it
    # is needed at all only because font metrics and image loading are not
    # available before one exists.
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        family = _font_with_hebrew()
        shots = read_shots(Path(args.images))
    except GuideError as exc:
        print(f"  {exc}")
        return 1

    print(f"  setting the guides in {family}, {len(shots) and 'with' or 'without'} pictures")
    problems = 0
    for language, source_name, pdf_name, title in GUIDES:
        source_path = GUIDE_DIR / source_name
        if not source_path.exists():
            print(f"  {source_path} is not there - skipped")
            problems += 1
            continue
        pdf_path = out_dir / pdf_name
        pages, report = make_pdf(
            source_path,
            pdf_path,
            title,
            language in RIGHT_TO_LEFT,
            shots,
            family,
        )
        size_kb = pdf_path.stat().st_size / 1024
        print(f"  {pdf_path}  -  {pages} pages, {size_kb:.0f} KB")
        for slug in dict.fromkeys(report.missing):
            print(f"      no picture for '{slug}' - that part has words only")
        for said in dict.fromkeys(report.mismatched):
            # Loud, and counted as a problem, because this is the guide pointing
            # at the wrong part of the screen and nothing on the page shows it.
            print(f"      THE NUMBERS DO NOT MATCH - {said}")
            problems += 1
    del app
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
