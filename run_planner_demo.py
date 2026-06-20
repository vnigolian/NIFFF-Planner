"""Example: runs the (placeholder) planner end-to-end against movies.csv,
a priority list, and an optional availability list, and prints the result.

This is for testing the pipeline locally, NOT the final interface -- the
real version of this will run in-browser via Pyodide, reading the same kind
of data from the page's in-memory state instead of CSV files.

Usage:
    python run_planner_demo.py movies.csv priority.csv [availability.csv] [min_break_minutes]

priority.csv: "Title,Priority" (as produced by extract_programme_as_csv.py
  and then hand-edited).
availability.csv (optional): "Date,Begin,End" with dd.mm dates and hh:mm
  times; rows for days not listed get the default (full-day) availability.
  Begin/End may be left blank in a row to use the default for that field.
min_break_minutes (optional, default 0): minimum gap required between two
  consecutive selected screenings, in minutes.
"""

from __future__ import annotations

import csv
import sys

from planner_core import solve  # noqa: F401  (re-exported for convenience)
from planner_io import (
    build_availability_from_rows,
    build_default_availability,
    plan,
    unshift_absolute_minutes,
)


class Screening:
    def __init__(self, date: str, cinema: str, time: str):
        self.date = date
        self.cinema = cinema
        self.time = time


class Movie:
    def __init__(self, title: str, length: str, screenings: list):
        self.title = title
        self.length = length
        self.screenings = screenings


def load_movies_csv(path: str) -> list[Movie]:
    movies = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            screenings = []
            for i in range(1, 10):
                date = row.get(f"Date {i}", "")
                cinema = row.get(f"Cinema {i}", "")
                time = row.get(f"Time {i}", "")
                if date and time:
                    screenings.append(Screening(date, cinema, time))
            movies.append(Movie(title=row["Title"], length=row["Length"], screenings=screenings))
    return movies


def load_priorities_csv(path: str) -> dict[str, int]:
    priorities = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            priorities[row["Title"]] = int(row["Priority"])
    return priorities


def load_availability_csv(path: str) -> list[tuple[str, str, str]]:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append((row["Date"], row.get("Begin", ""), row.get("End", "")))
    return rows


def format_screening(date: str, time: str) -> str:
    return f"{date} {time}"


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    movies_path = sys.argv[1]
    priority_path = sys.argv[2]
    availability_path = sys.argv[3] if len(sys.argv) > 3 else None
    min_break_minutes = int(sys.argv[4]) if len(sys.argv) > 4 else 0

    movies = load_movies_csv(movies_path)
    priorities = load_priorities_csv(priority_path)
    availability = (
        build_availability_from_rows(load_availability_csv(availability_path))
        if availability_path
        else build_default_availability()
    )

    result = plan(movies, priorities, availability, min_break_minutes)

    n_discarded = len(result.discarded)
    success_rate = (
        100 * result.n_movies_selected / result.n_movies_with_priority
        if result.n_movies_with_priority
        else 0.0
    )
    print("=== Summary ===")
    print(f"  Movies with priority > 0: {result.n_movies_with_priority}")
    print(f"  Discarded (priority > 0 but not scheduled): {n_discarded}")
    print(
        f"  Selected: {result.n_movies_selected}/{result.n_movies_with_priority} "
        f"({success_rate:.1f}%)"
    )
    print()

    print(f"=== Schedule (total priority: {result.total_priority}) ===")
    for entry in result.schedule:
        print(
            f"  {entry.screening.date} {entry.screening.time}  "
            f"{entry.movie.title}  (priority {entry.movie.priority}, {entry.screening.cinema})"
        )

    if result.tight_transition_warnings:
        print()
        print("=== Warnings: very tight back-to-back transitions (<5 min) ===")
        for prev_entry, cur_entry in result.tight_transition_warnings:
            print(
                f"  {prev_entry.movie.title} ({prev_entry.screening.time}) -> "
                f"{cur_entry.movie.title} ({cur_entry.screening.time})"
            )

    if result.discarded:
        print()
        print("=== Discarded (priority > 0, not scheduled), highest priority first ===")
        for discarded_movie in result.discarded:
            print(f"  {discarded_movie.movie.title}  (priority {discarded_movie.movie.priority})")
            for conflict in discarded_movie.conflicts:
                if conflict.blocking is None:
                    print(
                        f"    {conflict.screening.date} {conflict.screening.time}: not available"
                    )
                elif conflict.blocking:
                    blockers = ", ".join(
                        f"{b.movie.title} ({b.screening.time})" for b in conflict.blocking
                    )
                    print(f"    {conflict.screening.date} {conflict.screening.time}: blocked by {blockers}")
                else:
                    print(
                        f"    {conflict.screening.date} {conflict.screening.time}: "
                        f"not blocked by anything selected (just outscored)"
                    )


if __name__ == "__main__":
    main()
