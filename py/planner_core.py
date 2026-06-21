"""Festival schedule optimizer.

`solve()` is a fast, always-feasible random greedy assignment -- NOT
optimal, but useful when speed matters more than quality, or as a
sanity check while testing the rest of the pipeline.

`solve_best_of_n()` runs solve() many times with different random seeds
and keeps the best result -- a cheap, simple way to improve on a single
solve() call's quality (often substantially) without the worst-case
runtime risk of solve_optimal(). No guarantee of finding the true
optimum, but solve() is cheap enough that hundreds or thousands of
"simulations" still run in well under a second.

`solve_optimal()` is a real, exact branch-and-bound solver (see its
own docstring, and clique_bound.py, for the algorithm and validation
details). This problem (grouped interval scheduling / JISP) is NP-hard
in general, so solve_optimal()'s worst-case runtime is exponential --
it's intended for the "I'm willing to wait a few seconds for the exact
best schedule" use case, with solve()/solve_best_of_n() as faster
alternatives.

Time is represented as integer minutes since some fixed epoch (e.g. minutes
since the start of the festival), so this module has no knowledge of actual
dates/clock times -- that conversion happens at the edges.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass

from clique_bound import clique_aware_bound


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


def solve_best_of_n(
    movies: list[MovieOpt], min_break: int, n_simulations: int
) -> tuple[int, dict, dict]:
    """Runs solve() `n_simulations` times, each with a different random
    seed, and returns (best_total_priority, best_chosen, stats), where
    stats is {"min": ..., "mean": ..., "max": ..., "n": n_simulations} --
    the max here is always equal to best_total_priority, included for
    convenience so callers don't need to recompute it.

    Since solve() is cheap (a fraction of a millisecond per call even at
    full festival scale -- a few hundred screenings), running it many
    times and keeping the best result is a practical middle ground
    between solve()'s speed and solve_optimal()'s exactness: more
    simulations tend to find better schedules, but there's NO guarantee
    of finding the true optimum, and improvement can plateau early for
    some inputs while still improving at N=1000 for others -- it
    depends on the specific priorities/conflicts in play, not just N.
    """
    if n_simulations < 1:
        raise ValueError("n_simulations must be at least 1")

    best_total = 0
    best_chosen: dict[int, ScreeningOpt] = {}
    all_totals: list[int] = []

    for seed in range(n_simulations):
        total, chosen = solve(movies, min_break, rng=random.Random(seed))
        all_totals.append(total)
        if total > best_total:
            best_total = total
            best_chosen = chosen

    stats = {
        "min": min(all_totals),
        "mean": sum(all_totals) / len(all_totals),
        "max": max(all_totals),
        "n": n_simulations,
    }

    return best_total, best_chosen, stats


def solve_optimal(movies: list[MovieOpt], min_break: int) -> tuple[int, dict]:
    """Returns (best_total_priority, {movie_id: chosen_screening}).

    Movies not present as a key in the returned dict were not selected.
    Finds a GLOBALLY OPTIMAL solution via exhaustive branch-and-bound
    search with a two-tier pruning bound:
      1. A cheap, O(n)-ish per-node bound (per-movie reachability,
         ignoring conflicts among remaining movies) is tried first.
      2. If that's not tight enough to prune, a more expensive but much
         tighter per-day clique-aware bipartite-matching bound (see
         clique_bound.py) is computed as a fallback.

    Both bounds are empirically validated (3000+ random instances against
    exhaustive brute force) to never underestimate the true achievable
    value -- the property branch-and-bound correctness depends on.

    This problem is NP-hard in general (Job Interval Selection Problem;
    even the 2-screenings-per-movie special case is NP-complete -- see
    Spieksma 1999), so there's no guarantee on worst-case runtime. In
    practice, realistic festival-scale instances (a few dozen movies
    with nonzero priority) tend to solve in well under a minute on
    ordinary hardware, but pathological inputs could take much longer.
    There is currently no timeout/fallback built into this function --
    callers who need a time bound should add one (e.g. running this in
    a way that can be cancelled, falling back to solve()'s fast
    approximate result if it doesn't finish in time).
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

    def cheap_bound_for_remaining(index: int) -> int:
        """Fast O(remaining * avg_screenings * selected) upper bound: for
        each remaining movie, its priority if reachable (at least one
        non-conflicting screening), ignoring conflicts AMONG remaining
        movies. Much looser than tight_bound_for_remaining, but cheap
        enough to compute at every node as a first check."""
        bound = 0
        for movie in movies_sorted[index:]:
            for screening in movie.screenings:
                if not any(_conflicts(screening, s, min_break) for s in selected_screenings):
                    bound += movie.priority
                    break
        return bound

    def tight_bound_for_remaining(index: int) -> int:
        """Tighter, more expensive upper bound via per-day clique-aware
        bipartite matching: for each calendar day, builds the candidate
        screenings still reachable on that day and computes the max-
        weight matching between movies and maximal conflict-cliques.
        Decomposed per day since conflicts never cross day boundaries in
        this codebase's time model (every screening's shifted interval
        stays within its own day's 1440-minute block). Only called when
        the cheap bound isn't enough to prune, since this is meaningfully
        more expensive per call.
        """
        candidates_by_day = defaultdict(list)
        for movie in movies_sorted[index:]:
            for screening in movie.screenings:
                if not any(_conflicts(screening, s, min_break) for s in selected_screenings):
                    day_index = screening.start // (24 * 60)
                    candidates_by_day[day_index].append(
                        (movie.movie_id, movie.priority, screening.start, screening.end)
                    )
        return sum(
            clique_aware_bound(day_screenings, min_break)
            for day_screenings in candidates_by_day.values()
        )

    def search(index: int, current_score: int) -> None:
        nonlocal best_score, best_choice

        if current_score > best_score:
            best_score = current_score
            best_choice = dict(current_choice)

        if index == n:
            return

        # Two-tier pruning: try the CHEAP bound first. If it's already
        # not enough to beat the best solution found so far, prune
        # without ever touching the expensive bound. Only fall back to
        # the tight bound when the cheap one leaves room for doubt --
        # this is purely a performance choice (both bounds are
        # independently valid upper bounds), it cannot change correctness.
        if current_score + cheap_bound_for_remaining(index) <= best_score:
            return

        if current_score + tight_bound_for_remaining(index) <= best_score:
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
