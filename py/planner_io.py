"""Glue layer between the scraped/edited CSV data (movies, priorities,
availability) and the pure combinatorial solver in planner_core.py.

Handles: converting dd.mm/hh:mm strings into a single linear "minutes since
festival start" axis, building per-day availability windows, filtering
screenings against priority/availability, running the solver, and producing
a human-readable report of what got discarded and why.

TIME SHIFT: every clock time (screening times, availability begin/end) gets
shifted 5 hours EARLIER, wrapping within its own calendar day, before any
comparison happens -- e.g. "17:00" becomes "12:00", "01:00" becomes "20:00".
The calendar date itself is never touched. This is purely so that a
festival day's natural span (mid-morning through the small hours of the
next calendar date) fits inside one ordinary [00:00, 23:59] window, with no
day-rollover logic needed anywhere else in this module. Values are shifted
back (+5h) only when producing user-facing output.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from planner_core import (
    MovieOpt,
    ScreeningOpt,
    _conflicts,
    solve,
    solve_best_of_n,
    solve_optimal,
)

# The festival's first day, used as day index 0 for the minutes-since-start
# axis. Must match the actual festival dates for the year movies.csv was
# scraped from (see extract_programme_as_csv.py's day-range assumptions).
FESTIVAL_START_DDMM = "03.07"
FESTIVAL_NUM_DAYS = 9

DEFAULT_DAY_BEGIN = "05:00"
DEFAULT_DAY_END = "04:59"

# Always-on sanity-check threshold: any two selected screenings with less
# than this many minutes between them get flagged as a warning, regardless
# of what min_break the user chose for the optimization itself.
TIGHT_TRANSITION_WARNING_MINUTES = 5

SHIFT_MINUTES = 5 * 60
MINUTES_PER_DAY = 24 * 60


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _ddmm_to_day_index(ddmm: str) -> int:
    """Converts "dd.mm" to a 0-based day offset from FESTIVAL_START_DDMM.
    Assumes the whole festival falls within a single month (true for NIFFF,
    which runs entirely in early July)."""
    day, _month = (int(part) for part in ddmm.split("."))
    start_day, _start_month = (int(part) for part in FESTIVAL_START_DDMM.split("."))
    return day - start_day


def day_index_to_ddmm(day_index: int) -> str:
    """Inverse of _ddmm_to_day_index: converts a 0-based day offset back
    into "dd.mm", for labeling UI elements with real calendar dates."""
    start_day, start_month = (int(part) for part in FESTIVAL_START_DDMM.split("."))
    return f"{start_day + day_index:02d}.{start_month:02d}"


def _hhmm_to_minutes(hhmm: str) -> int:
    hours, minutes = (int(part) for part in hhmm.split(":"))
    return hours * 60 + minutes


def _minutes_to_hhmm(minutes_of_day: int) -> str:
    return f"{minutes_of_day // 60:02d}:{minutes_of_day % 60:02d}"


def shifted_absolute_minutes(day_index: int, hhmm: str) -> int:
    """Converts (day_index, "hh:mm") into absolute SHIFTED minutes since
    the festival's start: the clock time is shifted 5 hours earlier
    (wrapping within the same day), then placed on day_index unchanged."""
    shifted_minutes_of_day = (_hhmm_to_minutes(hhmm) - SHIFT_MINUTES) % MINUTES_PER_DAY
    return day_index * MINUTES_PER_DAY + shifted_minutes_of_day


def unshift_absolute_minutes(absolute_shifted_minutes: int) -> tuple[int, str]:
    """Inverse of shifted_absolute_minutes, for producing user-facing
    output: returns (day_index, "hh:mm") in real (unshifted) terms."""
    day_index, shifted_minutes_of_day = divmod(absolute_shifted_minutes, MINUTES_PER_DAY)
    real_minutes_of_day = (shifted_minutes_of_day + SHIFT_MINUTES) % MINUTES_PER_DAY
    return day_index, _minutes_to_hhmm(real_minutes_of_day)


def _length_to_minutes(length_str: str) -> int:
    """Parses the "NNN'" format used by extract_programme_as_csv.py's
    Length column. Returns 0 for an empty/missing length."""
    if not length_str:
        return 0
    return int(length_str.rstrip("'"))


# ---------------------------------------------------------------------------
# Availability windows
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AvailabilityWindow:
    day_index: int
    begin_minutes: int  # absolute SHIFTED minutes since festival start
    end_minutes: int    # absolute SHIFTED minutes since festival start, same day_index

    def contains(self, start_minutes: int, end_minutes: int) -> bool:
        return self.begin_minutes <= start_minutes and end_minutes <= self.end_minutes


def build_default_availability() -> dict[int, AvailabilityWindow]:
    """Returns the default availability (every day, 06:00 to 05:00, which
    after shifting spans the whole shifted day) for all festival days."""
    return {
        day_index: AvailabilityWindow(
            day_index=day_index,
            begin_minutes=shifted_absolute_minutes(day_index, DEFAULT_DAY_BEGIN),
            end_minutes=shifted_absolute_minutes(day_index, DEFAULT_DAY_END),
        )
        for day_index in range(FESTIVAL_NUM_DAYS)
    }


def build_availability_from_rows(
    rows: list[tuple[str, str, str]]
) -> dict[int, AvailabilityWindow]:
    """Builds availability windows from (date, begin, end) rows, e.g. as
    read from the user's availability CSV. `date` is "dd.mm"; `begin`/`end`
    are "hh:mm" or "" (blank, meaning use the default for that field).

    Days not present in `rows` at all get the fully-default window.
    """
    windows = build_default_availability()

    for date_str, begin_str, end_str in rows:
        day_index = _ddmm_to_day_index(date_str)
        begin = begin_str.strip() or DEFAULT_DAY_BEGIN
        end = end_str.strip() or DEFAULT_DAY_END
        windows[day_index] = AvailabilityWindow(
            day_index=day_index,
            begin_minutes=shifted_absolute_minutes(day_index, begin),
            end_minutes=shifted_absolute_minutes(day_index, end),
        )

    return windows


# ---------------------------------------------------------------------------
# Movie -> MovieOpt conversion
# ---------------------------------------------------------------------------


@dataclass
class PlannerScreening:
    """A screening annotated with its computed absolute (shifted) time
    range and human-readable original fields, for reporting purposes."""

    date: str
    cinema: str
    time: str
    start_minutes: int  # absolute SHIFTED minutes since festival start
    end_minutes: int     # absolute SHIFTED minutes since festival start
    eligible: bool       # False if outside that day's availability window


@dataclass
class PlannerMovie:
    """A movie annotated with ALL of its screenings (not just eligible
    ones) and its priority. Used for building the discarded-movies report;
    planner_core only ever sees the eligible subset, wrapped as
    MovieOpt/ScreeningOpt.
    """

    movie_id: int
    title: str
    priority: int
    screenings: list[PlannerScreening] = field(default_factory=list)

    @property
    def eligible_screenings(self) -> list[PlannerScreening]:
        return [s for s in self.screenings if s.eligible]


def build_planner_movies(
    movies: list,  # list[extract_programme_as_csv.Movie]-shaped objects
    priorities: dict[str, int],
    availability: dict[int, AvailabilityWindow],
) -> list[PlannerMovie]:
    """Converts scraped Movie rows + a {title: priority} map into
    PlannerMovie objects, computing absolute (shifted) start/end times for
    every screening and flagging which ones fall within the user's
    availability.

    Movies not present in `priorities` are treated as priority 0 (excluded).
    """
    planner_movies = []

    for movie_id, movie in enumerate(movies):
        priority = priorities.get(movie.title, 0)
        length_minutes = _length_to_minutes(movie.length)

        planner_screenings = []
        for screening in movie.screenings:
            if not screening.date or not screening.time:
                continue  # unused screening slot (e.g. slots 2-9 when empty)

            day_index = _ddmm_to_day_index(screening.date)
            start_minutes = shifted_absolute_minutes(day_index, screening.time)
            end_minutes = start_minutes + length_minutes

            window = availability.get(day_index)
            eligible = window is not None and window.contains(start_minutes, end_minutes)

            planner_screenings.append(
                PlannerScreening(
                    date=screening.date,
                    cinema=screening.cinema,
                    time=screening.time,
                    start_minutes=start_minutes,
                    end_minutes=end_minutes,
                    eligible=eligible,
                )
            )

        planner_movies.append(
            PlannerMovie(
                movie_id=movie_id,
                title=movie.title,
                priority=priority,
                screenings=planner_screenings,
            )
        )

    return planner_movies


def to_movie_opts(planner_movies: list[PlannerMovie]) -> list[MovieOpt]:
    """Converts PlannerMovie objects into the MovieOpt/ScreeningOpt shape
    that planner_core.solve() understands: priority-0 movies and
    ineligible screenings are dropped entirely here."""
    movie_opts = []
    for pm in planner_movies:
        if pm.priority <= 0:
            continue
        eligible = pm.eligible_screenings
        if not eligible:
            continue
        screening_opts = tuple(
            ScreeningOpt(movie_id=pm.movie_id, start=s.start_minutes, end=s.end_minutes)
            for s in eligible
        )
        movie_opts.append(
            MovieOpt(movie_id=pm.movie_id, priority=pm.priority, screenings=screening_opts)
        )
    return movie_opts


# ---------------------------------------------------------------------------
# Result reporting
# ---------------------------------------------------------------------------


@dataclass
class ScheduledEntry:
    movie: PlannerMovie
    screening: PlannerScreening


@dataclass
class ConflictingPick:
    """Describes one of a discarded movie's screenings (ALL of them, not
    just the eligible ones), and why it didn't make it into the schedule:

    - blocking is None: this screening fell outside the user's availability
      (PlannerScreening.eligible is False) -- it was never even offered to
      the solver.
    - blocking == []: this screening WAS eligible and didn't conflict with
      anything selected, but the movie still wasn't chosen (it simply
      scored lower than what filled the rest of the schedule).
    - blocking == [...]: this screening WAS eligible but conflicts with one
      or more selected screenings listed here.
    """

    screening: PlannerScreening
    blocking: list[ScheduledEntry] | None


@dataclass
class DiscardedMovie:
    movie: PlannerMovie
    conflicts: list[ConflictingPick]  # one entry per screening this movie had (eligible or not)


@dataclass
class PlanResult:
    total_priority: int
    schedule: list[ScheduledEntry]  # sorted by start time
    discarded: list[DiscardedMovie]  # sorted by priority desc
    tight_transition_warnings: list[tuple[ScheduledEntry, ScheduledEntry]]
    n_movies_with_priority: int   # how many movies had priority > 0
    n_movies_selected: int        # how many of those were actually scheduled
    simulation_stats: dict | None = None  # {min, mean, max, n}; only set for algorithm="simulations"


def plan(
    movies: list,
    priorities: dict[str, int],
    availability: dict[int, AvailabilityWindow],
    min_break_minutes: int,
    algorithm: str = "simulations",
    n_simulations: int = 200,
) -> PlanResult:
    """`algorithm`:
      - "simulations" (default): solve_best_of_n() -- runs solve()
        `n_simulations` times with different random seeds and keeps the
        best result. Fast (each run is a fraction of a millisecond even
        at full festival scale), with no guarantee of finding the true
        optimum -- but tends to find a meaningfully better schedule the
        more simulations are run. `n_simulations` is ignored for other
        algorithm values.
      - "fast": solve() -- a single quick, always-feasible but not-
        necessarily-optimal random assignment (equivalent to
        "simulations" with n_simulations=1, kept as a distinct option
        for callers that don't need statistics).
      - "optimal": solve_optimal() -- an exact branch-and-bound search
        that's guaranteed to find the best possible schedule, but can
        take several seconds (or, in the worst case, much longer -- this
        problem is NP-hard) on realistic festival-scale inputs.
    See planner_core.py for details on all three.
    """
    planner_movies = build_planner_movies(movies, priorities, availability)
    movie_opts = to_movie_opts(planner_movies)

    simulation_stats = None
    if algorithm == "optimal":
        total_priority, chosen = solve_optimal(movie_opts, min_break_minutes)
    elif algorithm == "simulations":
        total_priority, chosen, simulation_stats = solve_best_of_n(
            movie_opts, min_break_minutes, n_simulations
        )
    elif algorithm == "fast":
        total_priority, chosen = solve(movie_opts, min_break_minutes)
    else:
        raise ValueError(
            f"Unknown algorithm {algorithm!r}; expected 'fast', 'simulations', or 'optimal'"
        )

    planner_movies_by_id = {pm.movie_id: pm for pm in planner_movies}

    # Build the schedule (selected entries), sorted by start time.
    schedule: list[ScheduledEntry] = []
    for movie_id, screening_opt in chosen.items():
        pm = planner_movies_by_id[movie_id]
        # Find the matching PlannerScreening (same start/end) to recover its
        # human-readable date/cinema/time fields.
        matching = next(
            s
            for s in pm.eligible_screenings
            if s.start_minutes == screening_opt.start and s.end_minutes == screening_opt.end
        )
        schedule.append(ScheduledEntry(movie=pm, screening=matching))

    schedule.sort(key=lambda entry: entry.screening.start_minutes)

    # Build the discarded list: every priority > 0 movie not in `chosen`.
    discarded: list[DiscardedMovie] = []
    for pm in planner_movies:
        if pm.priority <= 0 or pm.movie_id in chosen:
            continue

        conflicts: list[ConflictingPick] = []
        for screening in pm.screenings:
            if not screening.eligible:
                conflicts.append(ConflictingPick(screening=screening, blocking=None))
                continue

            blocking = [
                entry
                for entry in schedule
                if _conflicts(
                    ScreeningOpt(pm.movie_id, screening.start_minutes, screening.end_minutes),
                    ScreeningOpt(
                        entry.movie.movie_id,
                        entry.screening.start_minutes,
                        entry.screening.end_minutes,
                    ),
                    min_break_minutes,
                )
            ]
            conflicts.append(ConflictingPick(screening=screening, blocking=blocking))

        discarded.append(DiscardedMovie(movie=pm, conflicts=conflicts))

    discarded.sort(key=lambda d: d.movie.priority, reverse=True)

    # Always-on tight-transition warning, independent of min_break_minutes.
    warnings: list[tuple[ScheduledEntry, ScheduledEntry]] = []
    for i in range(1, len(schedule)):
        prev_entry = schedule[i - 1]
        cur_entry = schedule[i]
        gap = cur_entry.screening.start_minutes - prev_entry.screening.end_minutes
        if 0 <= gap < TIGHT_TRANSITION_WARNING_MINUTES:
            warnings.append((prev_entry, cur_entry))

    n_movies_with_priority = sum(1 for pm in planner_movies if pm.priority > 0)

    return PlanResult(
        total_priority=total_priority,
        schedule=schedule,
        discarded=discarded,
        tight_transition_warnings=warnings,
        n_movies_with_priority=n_movies_with_priority,
        n_movies_selected=len(schedule),
        simulation_stats=simulation_stats,
    )
