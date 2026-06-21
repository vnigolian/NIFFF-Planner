# NIFFF Planner (web)

Website available [here](https://vnigolian.github.io/NIFFF-Planner/site/)

## TODOS

* add the break time as a field on the web UI
* implement the optimal, MILP-based solver

A static, no-backend tool for planning a festival schedule: rank the movies
you want to see, and it builds a non-conflicting schedule for you. Runs
entirely in your browser via [Pyodide](https://pyodide.org) — nothing you
type leaves your machine, and there's no server involved at all.

This is a **minimal first slice**: load the movie list, set priorities and
availability, build a schedule, see what got cut and why. Scheduling
works by running a fast random greedy assignment many times (however
many "simulations" you ask for) and keeping the best result — there's no
guarantee of finding the absolute best possible schedule, but each run
is a fraction of a millisecond even at full festival scale, so running
hundreds or thousands of simulations is still essentially instant, and
tends to find a noticeably better schedule than just one run. The page
also shows the min/mean/max total priority across all simulations, so
you can get a feel for how much variance there is and whether running
more would likely help.

(`planner_core.py` also has `solve()` — a single run, no statistics —
and `solve_optimal()` — an exact but potentially slow exhaustive search,
since this scheduling problem is NP-hard — kept as library functions for
reference/comparison, but they're not exposed in the UI.)

Weighting schemes and bulk-edit-by-category (beyond the free-text
filter's "apply to filtered" button, which now has its own "don't
overwrite already-set priorities" checkbox) are intentionally not in
this version yet.

## Repo layout

```
README.md
site/              What GitHub Pages actually serves (see Deploying below)
  index.html       Page structure
  style.css        All styling
  script.js        Pyodide bootstrap + DOM rendering/event wiring (no
                    planning logic lives here -- see py/)
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

`site/`, `data/`, and `py/` are siblings under the repo root — `site/` is
NOT self-contained; its `index.html` reaches `data/movies.csv` and
`py/*.py` via `../` relative paths, which only resolve correctly when
GitHub Pages serves the **repo root**, not the `site/` folder itself (see
Deploying below).

## Running it locally

Browsers won't run Pyodide (or fetch local files) from a plain `file://`
URL, so you need a tiny local server. From the **repo root** (not `site/`):

```
python3 -m http.server 8000
```

then open `http://localhost:8000/site/`.

## Deploying

This is a plain static site, no build step — but the folder structure
matters for GitHub Pages specifically:

1. Push this whole repo (with `site/`, `data/`, `py/`, `README.md` at the
   root) to GitHub.
2. In the repo: **Settings → Pages → Source → Deploy from a branch**, pick
   your branch and **`/ (root)`** as the folder (NOT `/site`) — Pages needs
   to serve the repo root so the `../data` and `../py` relative paths in
   `site/index.html` resolve correctly.
3. Your site will be live at `https://<username>.github.io/<repo>/site/`
   (note the `/site/` at the end — that's expected, since `index.html`
   lives there, not at the repo root).

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
- **No weighting-scheme picker yet.** There's exactly one scoring rule:
  maximize total priority.
- **No bulk-edit by category/country/year yet** — though the filter box's
  "Apply to filtered" control covers a lot of the same ground already,
  since it works against any text in the table, not just a fixed field.

All of the above are designed for, in `planner_io.py`'s data model — they
just don't have UI yet.
