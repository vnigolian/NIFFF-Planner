"""Extracts movie-title positions from the official NIFFF program-chart
PDF (the grid showing every screening across all venues/days) into a
small JSON lookup file consumed by the planner site.

Run this yourself, locally, whenever the official PDF changes (a new
year, a corrected version, etc.) -- it's intentionally a standalone
script with no dependency on any particular chat session, same spirit
as extract_programme_as_csv.py for the main movie catalog.

Usage:
    pip install pymupdf
    python3 extract_pdf_layout.py GRILLE-HORAIRE_NIFFF2026.pdf layout.json

MATCHING KEY: (title text, day-block). Originally this also tried to
detect venue/cinema rows for extra disambiguation, but checked directly
against the real PDF: no single title repeats WITHIN the same day
anywhere in the document (only ACROSS different days, e.g. "QUINCE"
screens on 03.07, 05.07, AND 09.07), so (title, day) alone is already a
unique key -- venue detection would be unnecessary extra complexity.

WHY RECTANGLE-BASED EXTRACTION, NOT TEXT-BLOCK CLUSTERING: this chart
encodes each screening as a COLORED BOX positioned by time (x) and venue
(y), with the title drawn as text on top of it. An earlier version of
this script tried to reconstruct titles purely from PyMuPDF's own
text-block/line grouping (get_text("blocks")) -- this works fine for an
ISOLATED title, but badly misrepresents CLUSTERS of several short
screenings packed into adjacent narrow boxes on the same row: PyMuPDF
merges their text into ONE block (since the lines sit close together),
and every line within that merged block was incorrectly given the SAME,
much-too-wide x-span (the whole cluster's width) instead of its own
real box -- this produced visibly wrong marks (right day, very wrong
horizontal position, sometimes the wrong VENUE ROW entirely, since the
merged block's height could itself span more than one row in extreme
cases). Caught by direct visual inspection of a generated, marked-up
PDF, and confirmed by checking the actual drawn rectangles underneath:
e.g. "QUINCE" and "HOTSPRING SHARKATTACK 2" are two adjacent but
genuinely separate boxes, same y-range, different x-ranges -- exactly
what a real chart grid should look like, and exactly what the OLD
text-block approach failed to recover.

So instead: find the real colored rectangles first (page.get_drawings()),
filter out borders/gridlines (too thin) and anything outside a
day-block's y-range (legends, headers), then for each remaining
rectangle, find every WORD that falls inside it and join them in
reading order. A rectangle can have NO words inside it (a decorative
band, or an empty slot) -- those are simply skipped, not emitted as
empty titles. Some screenings are drawn with TWO overlapping
rectangles (an outer decorative band plus an inner one closely fitted
to the text) -- when a word falls inside more than one rectangle, the
SMALLEST (by area) is used, since that's reliably the tightest, most
accurate box for that specific title (verified directly: the larger
rectangle in these pairs is consistently a looser background band, not
a second, different screening).

WHY PyMuPDF: confirmed against Pyodide's official package list to be
natively supported in-browser (this script itself runs LOCALLY though,
not in Pyodide -- but the SAME library is used browser-side later for
drawing marks onto the PDF, so using it here too for extraction avoids
a second dependency).
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass

import fitz

DAY_NAMES = {
    "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"
}

MONTH_ABBREVIATIONS = {
    "JANUARY": "01", "FEBRUARY": "02", "MARCH": "03", "APRIL": "04",
    "MAY": "05", "JUNE": "06", "JULY": "07", "AUGUST": "08",
    "SEPTEMBER": "09", "OCTOBER": "10", "NOVEMBER": "11", "DECEMBER": "12",
}

# NOTE: there is NO reliable size threshold that separates grid lines/
# borders from real screening boxes -- checked directly: filled
# rectangle heights are densely packed from 0.5pt all the way past
# 6pt, with no clean gap (a single line of text is itself only ~5pt
# tall, so a tightly-fitted real box can legitimately be just as short
# as some non-content rectangles). Filtering by size alone, even
# loosely, was incorrectly excluding some genuine title boxes (caught
# directly: "RRR" has TWO overlapping candidate rectangles, and an
# 8pt height filter excluded the smaller, CORRECT one, leaving only an
# outer decorative band as the sole survivor). Instead, rectangles are
# filtered purely by whether they contain at least one real word
# (verified directly: zero thin grid-line/border rectangles ever
# contain a word, so this is both simpler and more reliable than any
# size threshold).
# After extraction, drop any title whose text is PURELY digits -- this
# is always the grid's own hour-marker ruler (e.g. "09", "10", ... "02"),
# never a real movie title, and it can otherwise show up as a spurious
# (title, date) duplicate (the ruler text legitimately repeats per day).
PURELY_NUMERIC_RE = re.compile(r"^\d+$")


@dataclass(frozen=True)
class DayBlock:
    date: str  # "dd.mm", matching the main planner's date format
    page_num: int
    y_top: float  # this day-block's own header row's y0
    y_bottom: float  # next day-block's y_top, or the page bottom if last


@dataclass(frozen=True)
class TitleBlock:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    page_num: int
    date: str  # which DayBlock this falls within


def _parse_ordinal_date(ordinal_text: str, month_text: str) -> str | None:
    """Given e.g. ordinal_text="3RD", month_text="JULY", returns "03.07".
    Returns None if the text doesn't parse as expected (defensive -- this
    is scraped from a real-world PDF layout, not a guaranteed-stable
    data format)."""
    match = re.match(r"^(\d+)(?:ST|ND|RD|TH)$", ordinal_text.upper())
    if match is None:
        return None
    day_of_month = int(match.group(1))
    month_code = MONTH_ABBREVIATIONS.get(month_text.upper())
    if month_code is None:
        return None
    return f"{day_of_month:02d}.{month_code}"


def find_day_blocks(page, page_num: int) -> list[DayBlock]:
    """Finds every day-block header line ("FRIDAY JULY 3RD ...") on this
    page and returns them in top-to-bottom order, with each one's
    y_bottom set to the next one's y_top (or the page height for the
    last one on the page)."""
    words = page.get_text("words")  # (x0, y0, x1, y1, text, block, line, word)

    headers = []
    for w in words:
        if w[4] not in DAY_NAMES:
            continue
        same_line = sorted(
            (ww for ww in words if abs(ww[1] - w[1]) < 2 and ww[0] >= w[0]),
            key=lambda ww: ww[0],
        )
        if len(same_line) < 3:
            continue
        _day_name, month_text, ordinal_text = (
            same_line[0][4],
            same_line[1][4],
            same_line[2][4],
        )
        date = _parse_ordinal_date(ordinal_text, month_text)
        if date is not None:
            headers.append((w[1], date))  # (y0, date)

    headers.sort(key=lambda h: h[0])

    blocks = []
    for i, (y_top, date) in enumerate(headers):
        y_bottom = headers[i + 1][0] if i + 1 < len(headers) else page.rect.height
        blocks.append(DayBlock(date=date, page_num=page_num, y_top=y_top, y_bottom=y_bottom))
    return blocks


def _find_candidate_boxes(page, day_blocks: list[DayBlock]) -> list[fitz.Rect]:
    """Finds every filled rectangle within some day-block's y-range on
    this page -- a superset that still includes thin grid lines/borders,
    since there's no reliable size threshold to exclude them up front
    (see the module docstring). find_title_blocks() does the real
    filtering, by requiring at least one word to actually fall inside a
    candidate box."""
    boxes = []
    for d in page.get_drawings():
        if not d.get("fill"):
            continue
        rect = d["rect"]
        if not any(day.y_top <= rect.y0 < day.y_bottom for day in day_blocks):
            continue
        boxes.append(rect)
    return boxes


def find_title_blocks(page, page_num: int, day_blocks: list[DayBlock]) -> list[TitleBlock]:
    """Finds every real screening box on this page (see
    _find_candidate_boxes()) and, for each one, joins whatever words
    fall inside it (in reading order) into that box's title. Boxes with
    no words inside (decorative bands, empty slots) are skipped rather
    than emitted as empty titles.

    When a word falls inside more than one box (some screenings are
    drawn with an outer decorative band plus an inner, tightly-fitted
    box), only the SMALLEST (by area) containing box is used for that
    word -- verified directly against the real PDF that the larger box
    in these pairs is consistently a looser background band, not a
    second, different screening.
    """
    boxes = _find_candidate_boxes(page, day_blocks)
    words = page.get_text("words")  # (x0, y0, x1, y1, text, block, line, word)

    words_by_box: dict[int, list] = {i: [] for i in range(len(boxes))}
    for w in words:
        point = fitz.Point(w[0] + 1, w[1] + 1)  # nudge inside the glyph's own box
        containing_indices = [i for i, box in enumerate(boxes) if box.contains(point)]
        if not containing_indices:
            continue
        smallest_index = min(containing_indices, key=lambda i: boxes[i].width * boxes[i].height)
        words_by_box[smallest_index].append(w)

    title_blocks = []
    for i, box in enumerate(boxes):
        box_words = words_by_box[i]
        if not box_words:
            continue
        # Reading order: top-to-bottom (line), then left-to-right within
        # a line -- matters for boxes whose title wraps onto 2 lines.
        box_words.sort(key=lambda w: (round(w[1], 1), w[0]))
        text = " ".join(w[4] for w in box_words).strip()
        if not text:
            continue

        day = next((d for d in day_blocks if d.y_top <= box.y0 < d.y_bottom), None)
        if day is None:
            continue

        title_blocks.append(
            TitleBlock(
                text=text,
                x0=box.x0,
                y0=box.y0,
                x1=box.x1,
                y1=box.y1,
                page_num=page_num,
                date=day.date,
            )
        )
    return title_blocks


def extract_layout(pdf_path: str) -> list[TitleBlock]:
    """Top-level entry point: opens the PDF and returns every title block
    found across all pages, each tagged with its date -- with the purely-
    numeric hour-ruler noise filtered out."""
    doc = fitz.open(pdf_path)
    all_blocks = []
    for page_num, page in enumerate(doc):
        day_blocks = find_day_blocks(page, page_num)
        all_blocks.extend(find_title_blocks(page, page_num, day_blocks))
    return [b for b in all_blocks if not PURELY_NUMERIC_RE.match(b.text)]


def write_layout_json(blocks: list[TitleBlock], output_path: str) -> None:
    payload = [
        {
            "text": b.text,
            "date": b.date,
            "page": b.page_num,
            "x0": round(b.x0, 1),
            "y0": round(b.y0, 1),
            "x1": round(b.x1, 1),
            "y1": round(b.y1, 1),
        }
        for b in blocks
    ]
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _print_duplicate_check(blocks: list[TitleBlock]) -> None:
    """Sanity check, printed every run: confirms (title, date) is still
    a unique key in whatever PDF was just processed. If NIFFF's chart
    layout changes enough that this stops being true, this script will
    say so explicitly rather than silently shipping ambiguous data."""
    from collections import Counter

    counts = Counter((b.text, b.date) for b in blocks)
    duplicates = {k: c for k, c in counts.items() if c > 1}
    if duplicates:
        print(
            f"WARNING: {len(duplicates)} (title, date) pairs appear more than once "
            f"-- matching may be ambiguous for these:",
            file=sys.stderr,
        )
        for (text, date), count in duplicates.items():
            print(f"  {text!r} on {date}: {count}x", file=sys.stderr)
    else:
        print("OK: every (title, date) pair is unique.", file=sys.stderr)


def main() -> None:
    if len(sys.argv) != 3:
        print(
            "Usage: python3 extract_pdf_layout.py <input.pdf> <output.json>",
            file=sys.stderr,
        )
        sys.exit(1)

    input_path, output_path = sys.argv[1], sys.argv[2]
    blocks = extract_layout(input_path)
    _print_duplicate_check(blocks)
    write_layout_json(blocks, output_path)
    print(f"Wrote {len(blocks)} title blocks to {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
