# NIFFF Planner Online

Website available [here](https://vnigolian.github.io/NIFFF-Planner/site/)


A static, no-backend tool for planning a festival schedule: rank the movies
you want to see, and it builds a non-conflicting schedule for you. Runs
entirely in your browser via [Pyodide](https://pyodide.org) — nothing you
type leaves your machine, and there's no server involved at all.

## Contributing

Feel free to make suggestions using the Issues system, or directly with PRs.
A very appreciated improvement would be for some native speakers to provide better translations for Italian, German and Rumantsch.
The current text are automatically translated.

## Features

This is a **minimal first slice**: load the movie list, set priorities and
availability, build a schedule, see what got cut and why. Scheduling
works by running a fast random greedy assignment many times (however
many "simulations" you ask for) and keeping the best result — there's no
guarantee of finding the absolute best possible schedule, but each run
is a fraction of a millisecond even at full festival scale, so running
hundreds or thousands of simulations is still possible, given enough time. 
Although in practice, 1000 runs give good enough results.
The page also shows the min/mean/max total priority across all simulations, so
you can get a feel for how much variance there is and whether running
more would likely help.

There's also an **objective function** picker (Linear / Quadratic /
Exponential) controlling what "best" means when comparing simulations:
- **Linear** (default): just add up priorities. Two priority-5 picks are
  worth exactly as much as one priority-10 pick.
- **Quadratic**: weight(priority) = priority² — favors landing your
  single highest-priority picks over a pile of lower-priority ones.
- **Exponential**: weight(priority) = 10^priority — favors this much
  more aggressively (one extra point of priority is worth 10× more).
  Priorities above 10 are treated as exactly 10 for this calculation
  only (shown as an explicit warning when it applies) — without a cap,
  10^priority blows up to meaningless magnitudes for anything typed
  well above the festival's intended ~1-10 priority range.

Important: regardless of which objective is selected, the displayed
total/min/mean/max are always plain **Linear** sums — with a non-linear
objective, the schedule that wins isn't necessarily the one with the
highest linear sum (it's the one that scored highest under the chosen
weighting), so the displayed total can legitimately be *lower* than the
displayed max. The UI calls this out explicitly when it's the case.

(`planner_core.py` also has `solve()` — a single run, no statistics —
and `solve_optimal()` — an exact but potentially slow exhaustive search,
since this scheduling problem is NP-hard, and only supports the Linear
objective — kept as library functions for reference/comparison, but
they're not exposed in the UI.) 
The ultimate goal is to generate optimal schedules, but this requires an MILP solver, but importing one is not compatible with Pyodide.

Bulk-edit-by-category (beyond the free-text filter's "apply to filtered"
button, which has its own "don't overwrite already-set priorities"
checkbox) is intentionally not in this version yet.


## Translations

UI text lives in `site/lang/<code>.json` — one flat `key: "string"` map
per language, with `{placeholder}` substitution for dynamic values (e.g.
`"Loaded {count} movies."`). Movie data itself (titles, categories,
country codes) is never translated — only the site's own UI chrome.

**Editing an existing language**: open the `.json` file and edit the
strings directly — it's plain text, no code involved. Keep the
`{placeholder}` tokens exactly as they appear (same name, same curly
braces) since those get replaced with real values at runtime; everything
else is free text.

**Adding a new language**: a static site with no build step can't
discover files on its own, so this needs two small steps, not just
dropping in a file:
1. Copy `site/lang/en.json` to `site/lang/<code>.json` and translate its
   values (keep every key name exactly as-is).
2. Add a matching `<option value="<code>">Label</option>` to the
   `#language-select` dropdown in `site/index.html`.

**Known limitation**: the small `aria-label` on each movie row's
priority input (screen-reader-only, not visible text) is set once when
the row is first rendered and won't retroactively update if you switch
languages mid-session — everything else on the page does update live.

`site/`, `data/`, and `py/` are siblings under the repo root — `site/` is
NOT self-contained; its `index.html` reaches `data/movies.csv` and
`py/*.py` via `../` relative paths, which only resolve correctly when
GitHub Pages serves the **repo root**, not the `site/` folder itself (see
Deploying below).


## Repo layout

```
README.md
site/              What GitHub Pages actually serves (see Deploying below)
  index.html       Page structure
  style.css        All styling
  script.js        Pyodide bootstrap + DOM rendering/event wiring (no
                    planning logic lives here -- see py/)
  lang/
    en.json        UI text, English (the reference translation)
    fr.json        UI text, French
    de.json        UI text, German (machine-translated, unreviewed --
                    see "Translations" below)
    it.json        UI text, Italian (machine-translated, unreviewed --
                    see "Translations" below)
    rm.json        UI text, Romansh (Rumantsch Grischun, the
                    standardized supra-regional written form --
                    machine-translated, unreviewed, and lower-confidence
                    than the others since Romansh is a much smaller,
                    less-resourced language)
data/
  movies.csv       This year's scraped programme
py/
  app.py           The only Python module that knows it's in a browser:
                    fetches movies.csv, exposes load_movies()/run_plan()/
                    get_festival_days() for script.js to call
  planner_core.py  Pure combinatorial solvers: solve() (fast, random,
                    always-feasible but not optimal), solve_best_of_n()
                    (runs solve() many times, keeps the best -- a cheap
                    middle ground), and solve_optimal() (exact branch-and-
                    bound, NP-hard problem so no runtime guarantee -- see
                    its docstring)
  clique_bound.py  A tight, validated upper bound used by
                    solve_optimal()'s pruning (per-day clique-aware
                    bipartite matching)
  planner_io.py    Glue: CSV-shaped data <-> the solver's data model, time
                    handling, the discarded-movies report
```

## Running it locally

Browsers won't run Pyodide (or fetch local files) from a plain `file://`
URL, so you need a tiny local server. From the **repo root** (not `site/`):

```
python3 -m http.server 8000
```

then open `http://localhost:8000/site/`.


## Updating the movie list

`data/movies.csv` is generated once a year by `extract_programme_as_csv.py`
(in the companion scraper project) — copy the fresh CSV over this one and
commit it. The site itself never scrapes anything; it only ever reads this
file.

## Known limitations (intentional, for this slice)

- **No true optimum guarantee.** "Simulations" is a heuristic (best-of-N
  random greedy assignment) — running more simulations tends to improve
  the result, but improvement can plateau early for some priority/
  availability combinations while still improving at N=1000+ for others.
  There's no way to know, from the UI alone, how close a given result is
  to the true best possible schedule.
- **A large simulation count can briefly freeze the page.** Pyodide runs
  on the main browser thread here, so while it's solving, the page can't
  repaint or respond to clicks — this is a known Pyodide tradeoff (see
  their docs on Web Workers), not a bug. In practice this is brief even
  at thousands of simulations, since each run is sub-millisecond.




## TODOS
* implement the optimal, MILP-based solver