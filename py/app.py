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

from planner_core import EXPONENTIAL_PRIORITY_CAP
from planner_io import (
    DEFAULT_DAY_BEGIN,
    DEFAULT_DAY_END,
    FESTIVAL_NUM_DAYS,
    build_availability_from_rows,
    day_index_to_ddmm,
    plan,
)

MOVIES_CSV_PATH = "../data/movies.csv"

# Unlike MOVIES_CSV_PATH (fetched via pyodide.http.open_url(), a real
# HTTP relative URL resolved by the browser), these two are read by
# fitz.open()/json.load() as VIRTUAL FILESYSTEM paths -- script.js
# writes them as flat filenames (pyodide.FS.writeFile("GRILLE-...",
# bytes), no directory prefix), matching the same convention already
# used for the .py modules themselves. A "../data/..." path here would
# need a real "data" directory node to exist in the virtual filesystem,
# which nothing creates -- that mismatch caused a real ENOENT at runtime
# before this was caught and fixed.
OFFICIAL_PDF_PATH = "GRILLE-HORAIRE_NIFFF2026.pdf"
PDF_LAYOUT_JSON_PATH = "pdf_layout.json"


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

    # Cache-busting query param: open_url() has no cache-control option of
    # its own, and a stale cached movies.csv would silently show last
    # year's catalog with no obvious error -- not worth the risk.
    import time

    csv_text = open_url(f"{MOVIES_CSV_PATH}?v={int(time.time() * 1000)}").read()
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


def run_plan(
    priorities: dict,
    availability_rows: list,
    min_break_minutes: int = 0,
    algorithm: str = "simulations",
    n_simulations: int = 200,
    objective: str = "linear",
) -> dict:
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

    `algorithm`: "simulations" (default), "fast", or "optimal" -- see
    planner_io.plan() for what each means; "optimal" can take several
    seconds or longer. `n_simulations`/`objective` only apply to
    "simulations". `objective`: "linear" (default), "quadratic", or
    "exponential" -- see planner_core.WEIGHT_FUNCTIONS.
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

    # The exponential objective internally clamps any priority above
    # EXPONENTIAL_PRIORITY_CAP -- surface that as an explicit message
    # rather than silently changing behavior with no explanation. The
    # actual user-facing TEXT is built in JS (script.js's t() helper),
    # since this module has no notion of the current UI language -- only
    # the plain count is returned here.
    n_capped_priorities = 0
    if objective == "exponential":
        n_capped_priorities = sum(
            1 for p in clean_priorities.values() if p > EXPONENTIAL_PRIORITY_CAP
        )

    import time

    started_at = time.time()
    result = plan(
        _movies_cache,
        clean_priorities,
        availability,
        min_break_minutes,
        algorithm,
        n_simulations,
        objective,
    )
    elapsed_seconds = time.time() - started_at

    movies_by_title = {m.title: m for m in _movies_cache}

    return {
        "algorithm_used": algorithm,
        "objective_used": objective,
        "n_capped_priorities": n_capped_priorities,
        "exponential_priority_cap": EXPONENTIAL_PRIORITY_CAP,
        "n_simulations_used": n_simulations if algorithm == "simulations" else None,
        "simulation_stats": result.simulation_stats,
        "elapsed_seconds": round(elapsed_seconds, 2),
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
                # Full movie metadata, for the "download picked movies"
                # export -- looked up here (rather than carried through
                # planner_io's PlannerMovie) since this is purely a
                # CSV-shape concern specific to this browser-facing layer.
                "categories": movies_by_title[entry.movie.title].categories,
                "country": movies_by_title[entry.movie.title].country,
                "year": movies_by_title[entry.movie.title].year,
                "length": movies_by_title[entry.movie.title].length,
                "premiere": movies_by_title[entry.movie.title].premiere,
            }
            for entry in result.schedule
        ],
        "discarded": [
            {
                "title": d.movie.title,
                "priority": d.movie.priority,
                "categories": movies_by_title[d.movie.title].categories,
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


def _normalize_title_for_matching(title: str) -> str:
    """Uppercases, strips a leading "CONF. " prefix (the official PDF
    prefixes conference/talk entries this way; our own scraped catalog
    doesn't), strips a leading ceremony prefix (the official PDF
    combines the ceremony and the film into one block, e.g. "OPENING
    CEREMONY + NIGHTBORN"; our own catalog splits this into two
    separate, independently-pickable entries -- "Cérémonie + Nightborn"
    and plain "Nightborn" -- see extract_programme_as_csv.py's ceremony-
    split logic -- so BOTH of our split entries need to normalize down
    to just the plain film title to match the PDF's single combined
    block), and strips trailing ellipsis/punctuation -- normalizing both
    sides enough that an exact-or-prefix comparison (see _titles_match())
    can handle the official PDF's occasional mid-phrase truncation (e.g.
    the chart shows "ALICE AU PAYS…" for the full title "Alice au pays
    des merveilles")."""
    text = title.strip().upper().rstrip(".\u2026 ")
    if text.startswith("CONF. "):
        text = text[len("CONF. ") :]
    for ceremony_prefix in ("OPENING CEREMONY + ", "CLOSING CEREMONY + ", "CÉRÉMONIE + "):
        if text.startswith(ceremony_prefix):
            text = text[len(ceremony_prefix) :]
            break
    return text


def _titles_match(normalized_schedule_title: str, normalized_pdf_title: str) -> bool:
    """True if either normalized title is a prefix of the other -- the
    official PDF truncates some longer titles with an ellipsis, and
    after _normalize_title_for_matching() strips that ellipsis, what's
    left is a genuine PREFIX of the real, full title (verified directly
    against the real PDF: "ALICE AU PAYS" is a clean prefix of "ALICE AU
    PAYS DES MERVEILLES", not a different wording or abbreviation)."""
    if not normalized_schedule_title or not normalized_pdf_title:
        return False
    return normalized_schedule_title.startswith(
        normalized_pdf_title
    ) or normalized_pdf_title.startswith(normalized_schedule_title)


def match_schedule_to_pdf_layout(schedule: list[dict]) -> dict:
    """Matches each entry in `schedule` (the planner's picked-movies
    list, same shape as run_plan()'s "schedule" field) against the
    official PDF's precomputed layout (see ../data/pdf_layout.json,
    produced by the standalone extract_pdf_layout.py script -- run that
    script again, locally, whenever the official PDF changes).

    Matching key: (normalized title, date) -- see
    _normalize_title_for_matching() for why this isn't exact string
    equality, and extract_pdf_layout.py's module docstring for why no
    further disambiguation (e.g. by venue) is needed: checked directly
    against the real PDF, no title repeats within the same day anywhere
    in the document.

    Returns {"matched": [...], "unmatched_titles": [...]}, where matched
    entries are {"title", "date", "page", "x0", "y0", "x1", "y1"} (the
    schedule entry's own title/date plus its PDF position), and
    unmatched_titles lists any schedule entries that couldn't be found
    in the layout at all (e.g. the official PDF doesn't cover that
    movie, or its title differs more than the truncation-tolerant
    normalization accounts for).
    """
    import json

    with open(PDF_LAYOUT_JSON_PATH, encoding="utf-8") as f:
        layout = json.load(f)

    layout_by_date: dict[str, list[dict]] = {}
    for entry in layout:
        layout_by_date.setdefault(entry["date"], []).append(entry)

    matched = []
    unmatched_titles = []
    for entry in schedule:
        normalized_schedule_title = _normalize_title_for_matching(entry["title"])
        candidates = layout_by_date.get(entry["date"], [])
        layout_entry = next(
            (
                c
                for c in candidates
                if _titles_match(normalized_schedule_title, _normalize_title_for_matching(c["text"]))
            ),
            None,
        )
        if layout_entry is None:
            unmatched_titles.append(entry["title"])
            continue
        matched.append(
            {
                "title": entry["title"],
                "date": entry["date"],
                "page": layout_entry["page"],
                "x0": layout_entry["x0"],
                "y0": layout_entry["y0"],
                "x1": layout_entry["x1"],
                "y1": layout_entry["y1"],
            }
        )

    return {"matched": matched, "unmatched_titles": unmatched_titles}


def build_picked_movies_pdf(schedule: list[dict]) -> bytes:
    """Builds a plain PDF table of the picked schedule -- same fields and
    order as the "download as CSV" export (Title, Date, Cinema, Time,
    Categories, Country, Year, Length, Premiere), just rendered as a
    readable document instead of a CSV file. Uses PyMuPDF, imported
    HERE (not at module level) so loading app.py at boot doesn't require
    fetching PyMuPDF -- it's only needed once this specific export is
    actually used.
    """
    import fitz

    columns = [
        ("Title", 150),
        ("Date", 45),
        ("Cinema", 65),
        ("Time", 38),
        ("Categories", 130),
        ("Country", 75),
        ("Year", 38),
        ("Length", 42),
        ("Premiere", 90),
    ]

    doc = fitz.open()

    def start_page():
        page = doc.new_page(width=792, height=612)  # US Letter, landscape
        page.insert_text((margin_x, header_y), "Picked Movies", fontsize=14, fontname="hebo")
        x = margin_x
        for label, width in columns:
            page.insert_text((x, header_y + 24), label, fontsize=9, fontname="hebo")
            x += width
        page.draw_line(
            (margin_x, header_y + 28), (margin_x + sum(w for _, w in columns), header_y + 28)
        )
        return page, header_y + 42

    margin_x = 36
    row_height = 16
    header_y = 40

    page, y = start_page()
    for entry in schedule:
        if y > 612 - 40:
            page, y = start_page()

        x = margin_x
        values = [
            entry["title"], entry["date"], entry["cinema"], entry["time"],
            entry["categories"], entry["country"], entry["year"],
            entry["length"], entry["premiere"],
        ]
        for value, (_label, width) in zip(values, columns):
            text = str(value) if value else ""
            # Truncate long values rather than overflow into the next
            # column -- this is a simple fixed-width table, not a real
            # layout engine.
            max_chars = max(int(width / 4.5), 4)
            if len(text) > max_chars:
                text = text[: max_chars - 1] + "\u2026"
            page.insert_text((x, y), text, fontsize=8, fontname="helv")
            x += width
        y += row_height

    return doc.tobytes()


def build_highlighted_official_pdf(schedule: list[dict]) -> dict:
    """Draws a small mark next to each picked movie's title on the
    official program-chart PDF, using the precomputed layout (see
    match_schedule_to_pdf_layout()). Uses PyMuPDF, imported HERE (not at
    module level) for the same lazy-loading reason as
    build_picked_movies_pdf().

    Returns {"pdf_bytes": bytes, "matched_count": int, "total_count":
    int, "unmatched_titles": [...]} -- the caller (script.js) is
    responsible for surfacing the matched/unmatched counts to the
    person, since not every picked movie is guaranteed to be found (see
    match_schedule_to_pdf_layout()'s docstring for why a match can
    fail).
    """
    import fitz

    match_result = match_schedule_to_pdf_layout(schedule)

    doc = fitz.open(OFFICIAL_PDF_PATH)
    disc_color = (176/255, 87/255, 249/255)
    for m in match_result["matched"]:
        page = doc[m["page"]]
        # A small red circle just to the left of the title's own
        # bounding box -- positioned so it doesn't overlap the text
        # itself, on either page (titles never start right at the
        # grid's left edge, so there's always a little room to its
        # left within the same row).
        center_x = m["x0"] - 8
        center_y = (m["y0"] + m["y1"]) / 2
        page.draw_circle((center_x, center_y), radius=4, color=disc_color, fill=disc_color)

    return {
        "pdf_bytes": doc.tobytes(),
        "matched_count": len(match_result["matched"]),
        "total_count": len(schedule),
        "unmatched_titles": match_result["unmatched_titles"],
    }
