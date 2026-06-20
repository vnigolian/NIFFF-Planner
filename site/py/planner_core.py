"""Festival schedule optimizer.

`solve()` is currently a PLACEHOLDER: a fast, always-feasible random
greedy assignment, used to validate the rest of the pipeline (data shapes,
filtering, discarded-movie reporting, warnings) end-to-end before the real
optimizer is built. It is intentionally NOT optimal -- don't read anything
into the quality of its output yet.

`solve_optimal_bruteforce()` is a real, verified-correct (but exponential
time) solver, kept around as a small-instance reference oracle for
validating whatever faster algorithm eventually replaces `solve()`. It is
NOT suitable for production use at the scale of a full festival shortlist
(tens of movies): see its docstring for measured timings.

Time is represented as integer minutes since some fixed epoch (e.g. minutes
since the start of the festival), so this module has no knowledge of actual
dates/clock times -- that conversion happens at the edges.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class ScreeningOpt:
    movie_id: int
    start: int  # minutes since epoch
    end: int    # minutes since epoch


@dataclass(frozen=True)
class MovieOpt:
    movie_id: int
    priority: int
    screenings: tuple  # tuple[ScreeningOpt, ...], all referencing this movie's id


def _conflicts(a: ScreeningOpt, b: ScreeningOpt, min_break: int) -> bool:
    """True if screenings a and b cannot both be attended, i.e. the gap
    between them (in whichever order they fall) is less than min_break."""
    if a.start <= b.start:
        earlier, later = a, b
    else:
        earlier, later = b, a
    return later.start - earlier.end < min_break


def solve(
    movies: list[MovieOpt], min_break: int, rng: random.Random | None = None
) -> tuple[int, dict]:
    """PLACEHOLDER optimizer: returns a random FEASIBLE schedule, not an
    optimal one. See module docstring.

    Returns (total_priority, {movie_id: chosen_screening}); movies not
    present as a key were not selected (either skipped by chance, or none
    of their screenings were free given what was already picked).

    `rng` can be passed an explicit random.Random instance for
    reproducible/testable output; defaults to the module-level random
    instance otherwise.
    """
    if rng is None:
        rng = random.Random()

    shuffled_movies = list(movies)
    rng.shuffle(shuffled_movies)

    chosen: dict[int, ScreeningOpt] = {}
    selected_screenings: list[ScreeningOpt] = []
    total_priority = 0

    for movie in shuffled_movies:
        candidate_screenings = list(movie.screenings)
        rng.shuffle(candidate_screenings)

        for screening in candidate_screenings:
            if not any(_conflicts(screening, s, min_break) for s in selected_screenings):
                chosen[movie.movie_id] = screening
                selected_screenings.append(screening)
                total_priority += movie.priority
                break  # this movie is placed; move on to the next movie

    return total_priority, chosen


def solve_optimal_bruteforce(movies: list[MovieOpt], min_break: int) -> tuple[int, dict]:
    """Returns (best_total_priority, {movie_id: chosen_screening}).

    Movies not present as a key in the returned dict were not selected.
    Finds a GLOBALLY OPTIMAL solution via exhaustive backtracking search
    with upper-bound pruning.

    NOT suitable for production use yet: measured on realistic-shaped
    random instances (1-3 screenings/movie, 9 festival days), this took
    roughly 1.3s at 30 movies and 12s at 40 movies on ordinary hardware,
    and didn't finish within 30s at 50+ movies for at least one tested
    seed. Use only for small instances / as a correctness oracle when
    validating a faster replacement algorithm.
    """
    if not movies:
        return 0, {}

    # Process movies highest-priority-first: tends to find a near-optimal
    # (often optimal) solution very early, which makes pruning effective
    # from the start (a tight current best lets us cut off more branches).
    movies_sorted = sorted(movies, key=lambda m: m.priority, reverse=True)
    n = len(movies_sorted)

    best_score = 0
    best_choice: dict[int, ScreeningOpt] = {}

    current_choice: dict[int, ScreeningOpt] = {}
    selected_screenings: list[ScreeningOpt] = []

    def upper_bound_for_remaining(index: int) -> int:
        """Optimistic bound on how much MORE score is still achievable from
        movies_sorted[index:], given what's already selected so far.

        For each remaining movie, count its priority only if at least one
        of its screenings doesn't conflict with anything already selected
        (ignoring conflicts AMONG remaining, not-yet-decided movies -- this
        is what keeps it an upper bound rather than an exact value, but
        it's much tighter than ignoring conflicts altogether, since dense
        festival schedules mean a lot of remaining movies become genuinely
        unselectable once a few slots are filled).
        """
        bound = 0
        for movie in movies_sorted[index:]:
            for screening in movie.screenings:
                if not any(_conflicts(screening, s, min_break) for s in selected_screenings):
                    bound += movie.priority
                    break
        return bound

    def search(index: int, current_score: int) -> None:
        nonlocal best_score, best_choice

        if current_score > best_score:
            best_score = current_score
            best_choice = dict(current_choice)

        if index == n:
            return

        if current_score + upper_bound_for_remaining(index) <= best_score:
            return

        movie = movies_sorted[index]

        # Branch 1: skip this movie entirely.
        search(index + 1, current_score)

        # Branch 2: try each of this movie's screenings that doesn't
        # conflict with anything already selected.
        for screening in movie.screenings:
            if any(_conflicts(screening, s, min_break) for s in selected_screenings):
                continue
            current_choice[movie.movie_id] = screening
            selected_screenings.append(screening)

            search(index + 1, current_score + movie.priority)

            selected_screenings.pop()
            del current_choice[movie.movie_id]

    search(0, 0)

    return best_score, best_choice
