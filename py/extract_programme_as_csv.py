"""Download the NIFFF festival programme page and export the movie/event
listing as a CSV file.

This is a once-a-year personal utility: the site's markup is stable for the
whole festival run, so this script is meant to be run manually, not as part
of any ongoing pipeline. Output feeds into a (future) browser-based planner,
so the CSV is the source of truth -- this script does not do any planning.

Usage: just run it.
    python extract_programme_as_csv.py

Edit URL / OUTPUT_CSV_PATH below to change input/output.
"""

from __future__ import annotations

import csv
import re
import sys
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup, Comment

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

URL = "https://nifff.ch/programme/"
OUTPUT_CSV_PATH = "../data/movies.csv"

# Titles to skip entirely -- entries that show up under "Movies item" but
# aren't actually screenings (e.g. multi-day exhibitions with no fixed
# showtime, only opening hours). Add to this list if next year's programme
# has similar entries; matched against the cleaned-up title text.
EXCLUDED_TITLES = {
    "Maison d'Ailleurs : Le Regard dans les Univers de Frederik Peeters",
}

REQUEST_TIMEOUT_SECONDS = 30
REQUEST_HEADERS = {
    # Some sites behave differently (or reject requests) without a
    # plausible browser-like User-Agent.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Screening:
    date: str = ""    # "dd.mm", start date only (ranges collapse to start)
    cinema: str = ""
    time: str = ""    # "hh:mm", start time only (ranges collapse to start)


@dataclass
class Movie:
    title: str
    categories: str
    country: str = ""
    year: str = ""
    length: str = ""      # "NNN'", e.g. "137'"
    premiere: str = ""    # e.g. "Swiss Premiere", "World Premiere", ...
    screenings: list[Screening] = None  # up to 3, see MAX_SCREENINGS

    def __post_init__(self):
        if self.screenings is None:
            self.screenings = []


MAX_SCREENINGS = 9  # the festival runs for 9 days (03.07-11.07); a single
                     # entry's date range can expand to at most that many
                     # Screening rows (see _expand_date_range)


# ---------------------------------------------------------------------------
# HTML comment marker helpers
# ---------------------------------------------------------------------------
#
# The page marks up each section with HTML comments, e.g.:
#
#   <!-- Movies list -->
#     <!-- Movies item -->
#       <!-- Categories --> ... <!-- / Categories -->
#       <!-- Title --> ... <!-- / Title -->
#       <!-- Information left --> ... <!-- / Information left -->
#       <!-- Information right --> ... <!-- / Information right -->
#     <!-- / Movies item -->
#     ...
#   <!-- / Movies list -->
#
# Rather than relying on CSS classes (which are more likely to change for
# styling reasons), we walk comment-to-comment, which mirrors how the site's
# own template is structured and should be more stable.


def find_comment(soup_or_tag, label: str):
    """Finds the first comment node whose stripped text equals `label`."""
    target = label.strip()
    return soup_or_tag.find(
        string=lambda node: isinstance(node, Comment) and node.strip() == target
    )


def nodes_between_comments(open_comment, close_label: str):
    """Yields sibling nodes after `open_comment` up to (not including) the
    comment node whose text equals `close_label`.

    This walks `next_sibling` rather than doing a recursive search, since the
    content of a labeled section is always a flat run of siblings between
    two same-level comment markers (see the structure above).
    """
    target = close_label.strip()
    node = open_comment.next_sibling
    while node is not None:
        if isinstance(node, Comment) and node.strip() == target:
            return
        yield node
        node = node.next_sibling


def extract_labeled_text(scope_tag, label: str) -> str:
    """Returns the flattened text content between "<!-- {label} -->" and
    "<!-- / {label} -->" within `scope_tag`, or "" if the label isn't found.

    Some entries legitimately omit a section (e.g. no Categories, no
    Information left), so a missing label is not an error.
    """
    open_comment = find_comment(scope_tag, f"{label}")
    if open_comment is None:
        return ""

    texts = []
    for node in nodes_between_comments(open_comment, f"/ {label}"):
        if isinstance(node, Comment):
            continue
        texts.append(node.get_text() if hasattr(node, "get_text") else str(node))

    return "".join(texts)


def clean_single_line(text: str) -> str:
    """Collapses all whitespace (including newlines) into single spaces and
    strips the result. Used for fields that should stay on one line."""
    return " ".join(text.split())


def clean_multi_line(text: str) -> str:
    """Cleans each line individually (collapsing internal whitespace),
    drops blank lines, and re-joins with '\\n'. Used for fields where we
    deliberately want to preserve line breaks (e.g. several screenings, or
    "country, year, runtime" plus a premiere-status line)."""
    lines = (clean_single_line(line) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# Per-item field extraction
# ---------------------------------------------------------------------------


def extract_title(item_tag) -> str:
    """The Title section wraps a <div> whose *direct* text is the movie
    title, with an optional nested <div class="d-block ..."> holding the
    director's name. We only want the title, not the director.
    """
    open_comment = find_comment(item_tag, "Title")
    if open_comment is None:
        return ""

    # The wrapper <div> is the first tag-like sibling after the comment.
    wrapper = open_comment.find_next_sibling()
    if wrapper is None:
        return ""

    # `wrapper.find(string=True, recursive=False)` would only see direct
    # text children, but BeautifulSoup's NavigableString iteration already
    # only yields the wrapper's *own* text nodes when we filter out child
    # tags below -- so collect direct string children explicitly.
    direct_text = "".join(
        child for child in wrapper.children if isinstance(child, str)
    )
    return clean_single_line(direct_text)


def extract_categories(item_tag) -> str:
    return clean_single_line(extract_labeled_text(item_tag, "Categories"))


def _extract_information_left_raw(item_tag) -> str:
    """Raw content uses a literal <br> between the technical info line
    (e.g. "TH, 2025, 137'") and a premiere-status line (e.g. "Swiss
    Premiere"). BeautifulSoup's get_text() drops <br> tags silently, which
    would merge those two lines together, so we replace <br> with a
    newline-yielding marker before extracting text.

    Returns up to two lines, e.g.:
        "TH, 2025, 137'\\nSwiss Premiere"   (full info + premiere status)
        "FR, 2021, 129'"                    (full info, no premiere status)
        "90'"                               (runtime only, e.g. conferences)
        ""                                  (no info at all)
    """
    open_comment = find_comment(item_tag, "Information left")
    if open_comment is None:
        return ""

    texts = []
    for node in nodes_between_comments(open_comment, "/ Information left"):
        if isinstance(node, Comment):
            continue
        if hasattr(node, "get_text"):
            # Insert a real newline wherever a <br> appears.
            texts.append(node.get_text(separator="\n"))
        else:
            texts.append(str(node))

    return clean_multi_line("".join(texts))


def _extract_information_right_raw(item_tag) -> str:
    """Raw content is a sequence of <p>...</p> elements, one per screening,
    with no separator between them. We extract each <p> separately so each
    screening ends up on its own line, e.g.:
        "08.07, Passage 1, 16:30\\n11.07, Arcades, 18:45"
    """
    open_comment = find_comment(item_tag, "Information right")
    if open_comment is None:
        return ""

    lines = []
    for node in nodes_between_comments(open_comment, "/ Information right"):
        if isinstance(node, Comment):
            continue
        if hasattr(node, "find_all"):
            paragraphs = node.find_all("p") if node.name != "p" else [node]
            if paragraphs:
                lines.extend(p.get_text() for p in paragraphs)
            else:
                lines.append(node.get_text())
        else:
            lines.append(str(node))

    return clean_multi_line("\n".join(lines))


# ---------------------------------------------------------------------------
# Structured parsing of "Information left" / "Information right"
# ---------------------------------------------------------------------------
#
# "Information left" is always some subset of:
#   "<COUNTRY(/COUNTRY...)>, <YEAR>, <LENGTH>'"   (technical info line)
#   "<PREMIERE STATUS>"                            (e.g. "Swiss Premiere")
# Either line (or both) may be absent -- e.g. conferences only show a
# runtime ("90'"), and not every film has a premiere-status line.
#
# "Information right" is one screening per line, each of the form:
#   "<DATE>, <CINEMA>, <TIME>"
# where DATE is "dd.mm" or "dd.mm - dd.mm", and TIME is "hh:mm" or
# "hh:mm - hh:mm". When a screening lists a time range and the movie has no
# explicit Length yet, we derive the Length (in minutes) from that range,
# since this only occurs for workshops/installations whose only stated
# "runtime" *is* that time window.

_TECHNICAL_INFO_RE = re.compile(r"^(?P<country>[A-Za-z/]+),\s*(?P<year>\d{4}),\s*(?P<length>\d+)'$")
_RUNTIME_ONLY_RE = re.compile(r"^(?P<length>\d+)'$")

_SCREENING_RE = re.compile(
    r"^(?P<date>\d{2}\.\d{2})(?:\s*-\s*(?P<date_end>\d{2}\.\d{2}))?,\s*"
    r"(?P<cinema>[^,]+),\s*"
    r"(?P<time>\d{2}:\d{2})(?:\s*-\s*(?P<time_end>\d{2}:\d{2}))?$"
)


def parse_information_left(raw: str) -> tuple[str, str, str, str]:
    """Splits the raw "Information left" text into (country, year, length,
    premiere), any of which may be "" if not present.
    """
    lines = raw.split("\n") if raw else []

    country = year = length = premiere = ""

    for line in lines:
        match = _TECHNICAL_INFO_RE.match(line)
        if match:
            country = match.group("country")
            year = match.group("year")
            length = f"{match.group('length')}'"
            continue

        match = _RUNTIME_ONLY_RE.match(line)
        if match:
            length = f"{match.group('length')}'"
            continue

        # Anything else on its own line is a premiere-status line (e.g.
        # "Swiss Premiere", "World Premiere", "International Premiere",
        # "European Premiere", "Romandie Premiere").
        premiere = line

    return country, year, length, premiere


def _minutes_between(start_hhmm: str, end_hhmm: str) -> int:
    """Returns the number of minutes between two "hh:mm" times, assuming
    `end_hhmm` is later the same day (true for every observed case: these
    are same-day opening-hour windows, not overnight spans)."""
    start_h, start_m = (int(part) for part in start_hhmm.split(":"))
    end_h, end_m = (int(part) for part in end_hhmm.split(":"))
    return (end_h * 60 + end_m) - (start_h * 60 + start_m)


def _expand_date_range(date_start: str, date_end: str) -> list[str]:
    """Expands a "dd.mm - dd.mm" range into a list of "dd.mm" strings, one
    per day, inclusive of both endpoints.

    All dates observed on this page fall within a single month (the
    festival runs for at most a couple of weeks in July), so this only
    handles day-of-month arithmetic within one month -- it does not handle
    a range crossing a month boundary (e.g. "30.06 - 02.07"). If that ever
    occurs, this will raise rather than silently producing wrong dates.
    """
    start_day, start_month = (int(part) for part in date_start.split("."))
    end_day, end_month = (int(part) for part in date_end.split("."))

    if start_month != end_month:
        raise ValueError(
            f"Date range '{date_start} - {date_end}' crosses a month boundary; "
            "_expand_date_range does not support this."
        )

    return [f"{day:02d}.{start_month:02d}" for day in range(start_day, end_day + 1)]


def parse_information_right(raw: str) -> tuple[list[Screening], str]:
    """Splits the raw "Information right" text into a list of Screening
    objects (date/cinema/time), plus a `derived_length` string ("NNN'") to
    use as a fallback Length when the movie's own Information left didn't
    specify one (this only happens for workshops/installations whose
    "runtime" is really just their listed opening-hours window).

    A screening listing a DATE RANGE (e.g. an ongoing installation open
    "04.07 - 11.07") is expanded into one Screening per day in that range,
    all sharing the same cinema and start time -- rather than collapsing to
    a single Screening on the start date, which would silently lose the
    fact that it's available on every one of those days. A TIME range
    (e.g. "13:00 - 19:00") is NOT expanded the same way -- only its start
    time is kept, since that's the actual opening time of that single
    day's session.
    """
    lines = raw.split("\n") if raw else []

    screenings = []
    derived_length = ""

    for line in lines:
        match = _SCREENING_RE.match(line)
        if not match:
            # Should not happen given the site's consistent formatting; skip
            # rather than crash, so one unexpected entry doesn't break the
            # whole scrape.
            continue

        cinema = clean_single_line(match.group("cinema"))
        time = match.group("time")
        date_end = match.group("date_end")

        if date_end:
            dates = _expand_date_range(match.group("date"), date_end)
        else:
            dates = [match.group("date")]

        for date in dates:
            screenings.append(Screening(date=date, cinema=cinema, time=time))

        time_end = match.group("time_end")
        if time_end and not derived_length:
            derived_length = f"{_minutes_between(time, time_end)}'"

    return screenings, derived_length




def parse_movie_item(item_tag) -> Movie:
    country, year, length, premiere = parse_information_left(
        _extract_information_left_raw(item_tag)
    )
    screenings, derived_length = parse_information_right(
        _extract_information_right_raw(item_tag)
    )

    return Movie(
        title=extract_title(item_tag),
        categories=extract_categories(item_tag),
        country=country,
        year=year,
        length=length or derived_length,
        premiere=premiere,
        screenings=screenings,
    )


# ---------------------------------------------------------------------------
# Top-level parsing
# ---------------------------------------------------------------------------


CEREMONY_EXTRA_MINUTES = 60  # opening remarks/awards before the film itself


def _add_minutes_to_length(length: str, extra_minutes: int) -> str:
    """Adds `extra_minutes` to a "NNN'" length string, returning the same
    format. Returns the input unchanged if it's blank or not parseable
    (defensive: some movie entries have no listed length at all)."""
    if not length:
        return length
    try:
        minutes = int(length.rstrip("'"))
    except ValueError:
        return length
    return f"{minutes + extra_minutes}'"


def _split_ceremony_movies(movies: list[Movie]) -> list[Movie]:
    """The festival lists ceremony screenings as part of a regular movie
    entry: a movie whose Categories include "Ceremonies" has its FIRST
    listed screening actually be the ceremony itself (with the film
    screening immediately after, as part of the ceremony), while any
    remaining screenings are genuinely separate, standalone showings of
    the same film.

    To represent this correctly for scheduling purposes (so a user can
    independently prioritize "attend the ceremony" vs. "just watch the
    film another time"), any such movie is split into two entries:
      - "Cérémonie + {title}": same metadata, ONLY the first screening,
        with CEREMONY_EXTRA_MINUTES added to its length (the ceremony
        itself -- remarks, awards -- runs before the film and isn't
        reflected in the film's own listed runtime).
      - "{title}" (unchanged): same metadata, all REMAINING screenings.

    If there's only one screening to begin with, only the ceremony
    entry is created (there's nothing left for a second entry).
    """
    result = []
    for movie in movies:
        categories = [c.strip() for c in movie.categories.split(",")]
        if "Ceremonies" not in categories or not movie.screenings:
            result.append(movie)
            continue

        ceremony_screening, *remaining_screenings = movie.screenings

        result.append(
            Movie(
                title=f"Cérémonie + {movie.title}",
                categories=movie.categories,
                country=movie.country,
                year=movie.year,
                length=_add_minutes_to_length(movie.length, CEREMONY_EXTRA_MINUTES),
                premiere=movie.premiere,
                screenings=[ceremony_screening],
            )
        )

        if remaining_screenings:
            result.append(
                Movie(
                    title=movie.title,
                    categories=movie.categories,
                    country=movie.country,
                    year=movie.year,
                    length=movie.length,
                    premiere=movie.premiere,
                    screenings=remaining_screenings,
                )
            )

    return result


def parse_movies(html: str) -> list[Movie]:
    soup = BeautifulSoup(html, "lxml")

    list_open = find_comment(soup, "Movies list")
    if list_open is None:
        raise ValueError(
            "Could not find '<!-- Movies list -->' in the downloaded page. "
            "The site's markup may have changed."
        )

    # The "Movies list" comment sits just *before* the wrapper <div> that
    # contains all the items -- it is NOT a direct parent of the item
    # comments. So we descend into that wrapper <div> and search within it,
    # rather than walking flat siblings of the "Movies list" comment itself
    # (which would only see the wrapper div and the closing comment).
    wrapper = list_open.find_next_sibling()
    if wrapper is None:
        raise ValueError(
            "Found '<!-- Movies list -->' but no following element to "
            "search within. The site's markup may have changed."
        )

    movies = []
    for item_open in wrapper.find_all(
        string=lambda node: isinstance(node, Comment) and node.strip() == "Movies item"
    ):
        item_tag = item_open.find_next_sibling()
        if item_tag is None:
            continue

        title = extract_title(item_tag)
        if title in EXCLUDED_TITLES:
            continue

        movies.append(parse_movie_item(item_tag))

    return _split_ceremony_movies(movies)


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------


def _csv_header() -> list[str]:
    header = ["Title", "Categories", "Country", "Year", "Length", "Premiere"]
    for i in range(1, MAX_SCREENINGS + 1):
        header += [f"Date {i}", f"Cinema {i}", f"Time {i}"]
    return header


def _csv_row(movie: Movie) -> list[str]:
    row = [movie.title, movie.categories, movie.country, movie.year, movie.length, movie.premiere]
    for i in range(MAX_SCREENINGS):
        if i < len(movie.screenings):
            screening = movie.screenings[i]
            row += [screening.date, screening.cinema, screening.time]
        else:
            row += ["", "", ""]
    return row


def write_movies_csv(movies: list[Movie], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(_csv_header())
        for movie in movies:
            if len(movie.screenings) > MAX_SCREENINGS:
                print(
                    f"Warning: {movie.title!r} has {len(movie.screenings)} screenings, "
                    f"only the first {MAX_SCREENINGS} will be written to CSV.",
                    file=sys.stderr,
                )
            writer.writerow(_csv_row(movie))


def write_priority_csv(movies: list[Movie], path: str) -> None:
    """Writes a blank priority template (Title, Priority) for the user to
    fill in by hand. Every movie starts at "0" (neutral); leaving it as-is
    just means "no opinion yet" rather than "actively don't want to see
    this".
    """
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Title", "Priority"])
        for movie in movies:
            writer.writerow([movie.title, "0"])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    print(f"Downloading {URL} ...", file=sys.stderr)
    response = requests.get(URL, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()

    print("Parsing movies ...", file=sys.stderr)
    movies = parse_movies(response.text)
    print(f"Found {len(movies)} entries.", file=sys.stderr)

    print(f"Writing CSV to {OUTPUT_CSV_PATH} ...", file=sys.stderr)
    write_movies_csv(movies, OUTPUT_CSV_PATH)

    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
