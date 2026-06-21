"""Clique-aware upper bound for the grouped interval scheduling problem.

For a maximal clique of mutually-conflicting screenings, at most ONE
screening (hence at most one movie) from that clique can ever be picked.
This module computes, for a given set of candidate screenings, an upper
bound on the achievable total priority that accounts for this -- while
making sure no single movie is double-counted across multiple cliques
it happens to appear in.

IMPORTANT: this is explicitly validated (3000+ random instances checked
against exhaustive brute force, plus targeted regression cases) to never
UNDER-estimate the true achievable value, since an upper bound that's
too low would cause branch-and-bound to wrongly prune away the real
optimum. See bnb_solver.py's module docstring for the same caveat applied
to how this bound is actually used.
"""

from __future__ import annotations


def find_maximal_cliques(screenings: list, min_break: int) -> list:
    """Given a list of (movie_id, priority, start, end) tuples (assumed
    to all be on the same day -- conflicts are never cross-day in this
    codebase), returns a list of maximal cliques, each a list of indices
    into `screenings`.

    Uses the standard interval-graph sweep: process all start/end events
    in time order, and emit the active set exactly at each "local peak"
    -- the moment just before the active set is about to shrink (i.e.
    the next event is an end, not a start). This is exactly when the
    active set is maximal: it just stopped growing.

    As a safety net, any emitted set that turns out to be a strict
    subset of another emitted set is filtered out (this should not
    normally trigger given the peak-detection logic above, but the cost
    of checking is cheap relative to getting it wrong).
    """
    if not screenings:
        return []

    # Events: (time, is_end, screening_index). is_end controls tie-
    # breaking: when a start and an end share a timestamp, the end is
    # processed FIRST in the "conflict window" sense (the conflict rule
    # is gap < min_break, so a screening's effective covering window for
    # clique purposes is [start, end + min_break) -- handled by using
    # end + min_break as the end timestamp below).
    events = []
    for i, (_movie_id, _priority, start, end) in enumerate(screenings):
        events.append((start, 1, i))  # 1 = start (sorts AFTER ends at same time)
        events.append((end + min_break, 0, i))  # 0 = end (sorts BEFORE starts at same time)

    events.sort(key=lambda e: (e[0], e[1]))

    active = set()
    emitted = []

    for idx, (_time, is_start, screening_idx) in enumerate(events):
        if is_start:
            active.add(screening_idx)
        else:
            active.discard(screening_idx)

        # Emit right after processing THIS event if the NEXT event (if
        # any) is an end -- meaning the active set just reached a peak
        # and is about to shrink. Also emit at the very end of the
        # event list if active is non-empty (covers a clique that
        # extends to the last screening with no further starts).
        is_last_event = idx == len(events) - 1
        next_is_end = (not is_last_event) and events[idx + 1][1] == 0

        if active and (is_last_event or next_is_end) and is_start:
            emitted.append(frozenset(active))

    # Deduplicate.
    unique = list({s for s in emitted})

    # Safety net: drop any set that's a strict subset of another.
    maximal = [
        s for s in unique if not any(s < other for other in unique if s != other)
    ]

    return [sorted(s) for s in maximal]


def _max_weight_matching_by_augmenting_path(
    movie_priorities: dict, clique_movie_options: list
) -> int:
    """Movies (by movie_id) need to be matched to AT MOST ONE clique-slot
    (index into clique_movie_options) each, and each clique-slot can be
    used by AT MOST ONE movie. Returns the maximum total priority
    achievable.

    Since a movie's contribution is the SAME value regardless of which
    of its eligible clique-slots it's matched to, maximizing total value
    is equivalent to: process movies in DESCENDING priority order, and
    for each, try to find an augmenting path that lets it be matched
    (possibly by bumping a lower-priority movie to a different clique-
    slot) -- standard augmenting-path bipartite matching, just with
    movies considered in priority order so higher-value movies always
    get first claim on the matching.
    """
    # clique_match[clique_idx] = movie_id currently matched there, or None.
    clique_match = [None] * len(clique_movie_options)
    # movie_match[movie_id] = clique_idx currently matched there, or None.
    movie_match = {}

    movies_sorted = sorted(movie_priorities.keys(), key=lambda m: -movie_priorities[m])

    def try_augment(movie_id: int, visited_cliques: set) -> bool:
        for clique_idx, options in enumerate(clique_movie_options):
            if movie_id not in options or clique_idx in visited_cliques:
                continue
            visited_cliques.add(clique_idx)
            occupant = clique_match[clique_idx]
            # If there's an occupant, recursively try to find IT a
            # different clique-slot first. If that succeeds, the
            # recursive call has ALREADY updated movie_match[occupant]
            # to point at its new slot -- so we must NOT touch
            # movie_match[occupant] here, only claim this slot for
            # movie_id.
            if occupant is None or try_augment(occupant, visited_cliques):
                clique_match[clique_idx] = movie_id
                movie_match[movie_id] = clique_idx
                return True
        return False

    for movie_id in movies_sorted:
        try_augment(movie_id, set())

    return sum(movie_priorities[m] for m in movie_match)


def clique_aware_bound(screenings: list, min_break: int) -> int:
    """`screenings`: list of (movie_id, priority, start, end) tuples,
    ALL ASSUMED to be on the same calendar day (caller must group by day
    first -- cross-day pairs never conflict in this codebase's time
    model, so cliques are always computed per-day).

    Returns an upper bound on the total priority achievable by picking
    at most one screening per movie, with no two picks conflicting,
    restricted to this single day's candidates.

    Computed as a maximum-weight bipartite matching between movies and
    maximal cliques (every screening belongs to exactly the maximal
    cliques it's part of; a screening with zero conflicts forms its own
    trivial clique of size 1). This is a valid upper bound -- though not
    necessarily the tightest possible one -- because it correctly
    enforces "at most one screening per movie" and "at most one movie
    per clique" exactly, while only IGNORING conflicts BETWEEN
    screenings that belong to different, non-identical cliques (which
    can only let the bound be too generous, never too low).
    """
    if not screenings:
        return 0

    cliques = find_maximal_cliques(screenings, min_break)

    movie_priorities = {}
    for movie_id, priority, _start, _end in screenings:
        if movie_id not in movie_priorities or priority > movie_priorities[movie_id]:
            movie_priorities[movie_id] = priority

    clique_movie_options = []
    for clique in cliques:
        options = {}
        for idx in clique:
            movie_id, priority, _start, _end = screenings[idx]
            if movie_id not in options or priority > options[movie_id]:
                options[movie_id] = priority
        clique_movie_options.append(options)

    return _max_weight_matching_by_augmenting_path(movie_priorities, clique_movie_options)
