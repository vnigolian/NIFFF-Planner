# NIFFF Planner (web)

A static, no-backend tool for planning a festival schedule: rank the movies
you want to see, and it builds a non-conflicting schedule for you. Runs
entirely in your browser via [Pyodide](https://pyodide.org) — nothing you
type leaves your machine, and there's no server involved at all.

This is a **minimal first slice**: load the movie list, set priorities,
build a schedule, see what got cut and why. Availability windows, weighting
schemes, and bulk-edit-by-category are intentionally not in this version
yet.

## Running it locally

Browsers won't run Pyodide (or fetch local files) from a plain `file://`
URL, so you need a tiny local server. From this directory:

```
python3 -m http.server 8000
```

then open `http://localhost:8000`.

## Deploying

This is a plain static site — push this folder to a GitHub repo and enable
GitHub Pages on it (Settings → Pages → deploy from branch). No build step.

## Updating the movie list

`data/movies.csv` is generated once a year by `extract_programme_as_csv.py`
(in the companion scraper project) — copy the fresh CSV over this one and
commit it. The site itself never scrapes anything; it only ever reads this
file.

## File layout

```
index.html        Page structure
style.css         All styling
script.js         Pyodide bootstrap + DOM rendering/event wiring (no
                   planning logic lives here -- see py/)
data/
  movies.csv       This year's scraped programme
py/
  app.py           The only Python module that knows it's in a browser:
                   fetches movies.csv, exposes load_movies()/run_plan() for
                   script.js to call
  planner_core.py  Pure combinatorial solver (currently a random, always-
                   feasible PLACEHOLDER -- see its docstring)
  planner_io.py    Glue: CSV-shaped data <-> the solver's data model, time
                   handling, the discarded-movies report
```

## Known limitations (intentional, for this slice)

- **The scheduler is a placeholder.** `planner_core.solve()` currently
  picks a random feasible schedule, not an optimal one — building the real
  optimizer is a separate piece of work. `planner_core.solve_optimal_bruteforce()`
  exists as a correctness reference but is too slow for full-size use.
- **No weighting-scheme picker yet.** There's exactly one scoring rule:
  maximize total priority.
- **No bulk-edit by category/country/year yet** — though the filter box's
  "Apply to filtered" control covers a lot of the same ground already,
  since it works against any text in the table, not just a fixed field.

All of the above are designed for, in `planner_io.py`'s data model — they
just don't have UI yet.
