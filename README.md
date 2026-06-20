# NIFFF Planner (web)

A static, no-backend tool for planning a festival schedule: rank the movies
you want to see, and it builds a non-conflicting schedule for you. Runs
entirely in your browser via [Pyodide](https://pyodide.org) — nothing you
type leaves your machine, and there's no server involved at all.

This is a **minimal first slice**: load the movie list, set priorities and
availability, build a schedule, see what got cut and why. Weighting
schemes and bulk-edit-by-category (beyond the free-text filter's "apply to
filtered" button) are intentionally not in this version yet, and the
scheduler itself is still a placeholder (see below).

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
  planner_core.py  Pure combinatorial solver (currently a random, always-
                   feasible PLACEHOLDER -- see its docstring)
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
