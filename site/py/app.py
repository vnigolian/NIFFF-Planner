"""Browser-facing entrypoint, run inside Pyodide.

This is the ONLY module that knows it's running in a browser: it reads
movies.csv (fetched from the same site, no CORS issues since it's
same-origin), and exposes plain-data (JSON-serializable) functions for
script.js to call. planner_core.py and planner_io.py stay regular,
reusable Python with no Pyodide-specific code in them.
"""

from __future__ import annotations

import csv
import io

from pyodide.http import open_url

from planner_io import (
    DEFAULT_DAY_BEGIN,
    DEFAULT_DAY_END,
    FESTIVAL_NUM_DAYS,
    build_availability_from_rows,
    day_index_to_ddmm,
    plan,
)

MOVIES_CSV_PATH = "data/movies.csv"


class Screening:
    def __init__(self, date: str, cinema: str, time: str):
        self.date = date
        self.cinema = cinema
        self.time = time


class Movie:
    def __init__(self, title: str, categories: str, country: str, year: str,
                 length: str, premiere: str, screenings: list):
        self.title = title
        self.categories = categories
        self.country = country
        self.year = year
        self.length = length
        self.premiere = premiere
        self.screenings = screenings


def _load_movies_csv(csv_text: str) -> list[Movie]:
    movies = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        screenings = []
        for i in range(1, 10):
            date = row.get(f"Date {i}", "")
            cinema = row.get(f"Cinema {i}", "")
            time = row.get(f"Time {i}", "")
            if date and time:
                screenings.append(Screening(date, cinema, time))
        movies.append(
            Movie(
                title=row["Title"],
                categories=row["Categories"],
                country=row["Country"],
                year=row["Year"],
                length=row["Length"],
                premiere=row["Premiere"],
                screenings=screenings,
            )
        )
    return movies


# Module-level cache: movies.csv is fetched once per page load, then reused
# across every run_plan() call (the user only edits priorities, not the
# underlying movie list).
_movies_cache: list[Movie] | None = None


def load_movies() -> list[dict]:
    """Fetches and parses movies.csv, returns a JSON-friendly list of dicts
    for rendering the movie table in JS. Caches the parsed Movie objects
    for later use by run_plan()."""
    global _movies_cache

    csv_text = open_url(MOVIES_CSV_PATH).read()
    _movies_cache = _load_movies_csv(csv_text)

    return [
        {
            "title": m.title,
            "categories": m.categories,
            "country": m.country,
            "year": m.year,
            "length": m.length,
            "premiere": m.premiere,
            "screenings": [
                {"date": s.date, "cinema": s.cinema, "time": s.time} for s in m.screenings
            ],
        }
        for m in _movies_cache
    ]


def get_festival_days() -> list[dict]:
    """Returns one entry per festival day, for rendering the per-day
    availability controls: the real calendar date, and the default
    begin/end values (so the UI can show them as the visible, editable
    defaults rather than blank fields)."""
    return [
        {"date": day_index_to_ddmm(i), "default_begin": DEFAULT_DAY_BEGIN, "default_end": DEFAULT_DAY_END}
        for i in range(FESTIVAL_NUM_DAYS)
    ]


# Sentinel begin/end used for a day the user has marked "not available":
# a 1-minute window that no real screening can ever fall inside (every
# screening on this page starts well outside :00-:01 of the shifted day),
# so the day is excluded entirely from the solver without the UI's visible
# Begin/End fields needing to change.
UNAVAILABLE_BEGIN = "05:00"
UNAVAILABLE_END = "05:01"


def run_plan(priorities: dict, availability_rows: list, min_break_minutes: int = 0) -> dict:
    """Runs the planner against the cached movie list (see load_movies()).

    `availability_rows` is a list of {date, begin, end, available} dicts,
    one per festival day, as currently shown/edited in the UI:
    - `available` False means "exclude this day entirely" -- this
      function substitutes the UNAVAILABLE_BEGIN/END sentinel for that
      day's window rather than whatever begin/end the UI happens to be
      showing, since unchecking "available" deliberately does NOT alter
      the displayed fields (see script.js).
    - `available` True uses `begin`/`end` as given (blank meaning "use
      the default for that field", same as build_availability_from_rows).
    """
    if _movies_cache is None:
        raise RuntimeError("load_movies() must be called before run_plan()")

    rows = []
    for day in availability_rows:
        is_available = bool(day.get("available", True))
        if is_available:
            rows.append((day["date"], day.get("begin", ""), day.get("end", "")))
        else:
            rows.append((day["date"], UNAVAILABLE_BEGIN, UNAVAILABLE_END))

    availability = build_availability_from_rows(rows)

    # priorities arrives from JS as a JsProxy map; int(...) guards against
    # values coming through as JS numbers (floats) rather than Python ints.
    clean_priorities = {title: int(value) for title, value in priorities.items()}

    result = plan(_movies_cache, clean_priorities, availability, min_break_minutes)

    return {
        "total_priority": result.total_priority,
        "n_movies_with_priority": result.n_movies_with_priority,
        "n_movies_selected": result.n_movies_selected,
        "schedule": [
            {
                "title": entry.movie.title,
                "priority": entry.movie.priority,
                "date": entry.screening.date,
                "cinema": entry.screening.cinema,
                "time": entry.screening.time,
            }
            for entry in result.schedule
        ],
        "discarded": [
            {
                "title": d.movie.title,
                "priority": d.movie.priority,
                "conflicts": [
                    {
                        "date": c.screening.date,
                        "time": c.screening.time,
                        # None means "outside availability"; [] means
                        # "eligible but simply outscored"; otherwise a list
                        # of blocking schedule entries.
                        "blocking": (
                            None
                            if c.blocking is None
                            else [
                                {"title": b.movie.title, "time": b.screening.time}
                                for b in c.blocking
                            ]
                        ),
                    }
                    for c in d.conflicts
                ],
            }
            for d in result.discarded
        ],
        "tight_transition_warnings": [
            {
                "first_title": prev_entry.movie.title,
                "first_time": prev_entry.screening.time,
                "second_title": cur_entry.movie.title,
                "second_time": cur_entry.screening.time,
            }
            for prev_entry, cur_entry in result.tight_transition_warnings
        ],
    }
