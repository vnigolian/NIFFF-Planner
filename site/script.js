/* NIFFF Planner -- page glue.
 *
 * This file's only job is: boot Pyodide, render the movie list, collect
 * the user's priority edits, hand them to Python, and render whatever
 * Python sends back. All scraping/parsing/scheduling logic lives in
 * ../py/app.py, ../py/planner_core.py, and ../py/planner_io.py -- this
 * file does not re-implement any of it.
 */

const statusLine = document.getElementById("status-line");
const statusBarFill = document.getElementById("status-bar-fill");
const statusSection = document.getElementById("status-section");
const moviesSection = document.getElementById("movies-section");
const moviesTbody = document.getElementById("movies-tbody");
const availabilityDaysContainer = document.getElementById("availability-days");
const movieFilterInput = document.getElementById("movie-filter");
const bulkPriorityValueInput = document.getElementById("bulk-priority-value");
const bulkPriorityApplyButton = document.getElementById("bulk-priority-apply");
const bulkPriorityFeedback = document.getElementById("bulk-priority-feedback");
const downloadPrioritiesButton = document.getElementById("download-priorities");
const uploadPrioritiesTriggerButton = document.getElementById("upload-priorities-trigger");
const uploadPrioritiesInput = document.getElementById("upload-priorities-input");
const priorityFileFeedback = document.getElementById("priority-file-feedback");
const runButton = document.getElementById("run-button");
const resultsSection = document.getElementById("results-section");
const resultsSummary = document.getElementById("results-summary");
const downloadScheduleButton = document.getElementById("download-schedule");
const warningsBlock = document.getElementById("warnings-block");
const scheduleList = document.getElementById("schedule-list");
const discardedList = document.getElementById("discarded-list");

let pyodide = null;
let movies = []; // [{title, categories, country, year, length, premiere, screenings}]
let lastScheduleResult = []; // populated after a successful run, used for the download button

function setStatus(text, percent) {
  statusLine.textContent = text;
  if (typeof percent === "number") {
    statusBarFill.style.width = `${percent}%`;
  }
}

function renderAvailabilityDays(festivalDays) {
  availabilityDaysContainer.innerHTML = "";

  for (const day of festivalDays) {
    const row = document.createElement("div");
    row.className = "availability-day";
    row.dataset.date = day.date;

    row.innerHTML = `
      <div class="availability-day__head">
        <span class="availability-day__date">${day.date}</span>
        <label class="availability-day__checkbox-label">
          <input type="checkbox" checked data-available-checkbox />
          available
        </label>
      </div>
      <div class="availability-day__times">
        <label for="begin-${day.date}">From</label>
        <input
          type="time"
          id="begin-${day.date}"
          value="${day.default_begin}"
          data-begin-input
        />
        <label for="end-${day.date}">To</label>
        <input
          type="time"
          id="end-${day.date}"
          value="${day.default_end}"
          data-end-input
        />
      </div>
    `;

    availabilityDaysContainer.appendChild(row);
  }

  // Unchecking "available" disables (but does not clear or hide) the time
  // fields, per the intended behaviour: what's shown stays put, only what
  // gets sent to the planner changes (see collectAvailability()).
  availabilityDaysContainer.querySelectorAll("[data-available-checkbox]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const row = checkbox.closest(".availability-day");
      const isAvailable = checkbox.checked;
      row.classList.toggle("availability-day--unavailable", !isAvailable);
      row.querySelector("[data-begin-input]").disabled = !isAvailable;
      row.querySelector("[data-end-input]").disabled = !isAvailable;
    });
  });
}

function collectAvailability() {
  const rows = [];
  availabilityDaysContainer.querySelectorAll(".availability-day").forEach((row) => {
    const available = row.querySelector("[data-available-checkbox]").checked;
    const begin = row.querySelector("[data-begin-input]").value;
    const end = row.querySelector("[data-end-input]").value;
    rows.push({ date: row.dataset.date, begin, end, available });
  });
  return rows;
}

function formatScreeningsCell(screenings) {
  if (screenings.length === 0) {
    return '<span class="screenings-list">&mdash;</span>';
  }
  const items = screenings
    .map((s) => `<li>${s.date} &middot; ${s.time} &middot; ${s.cinema}</li>`)
    .join("");
  return `<ul class="screenings-list">${items}</ul>`;
}

function setRowPriority(row, value) {
  const input = row.querySelector("[data-priority-input]");
  input.value = value;
  const stub = input.closest(".priority-stub");
  stub.classList.toggle("priority-stub--active", Number(value) > 0);
}

function csvEscape(value) {
  const text = value == null ? "" : String(value);
  if (/[",\n\r]/.test(text)) {
    return `"${text.replace(/"/g, '""')}"`;
  }
  return text;
}

function buildCsv(header, rows) {
  const lines = [header.map(csvEscape).join(",")];
  for (const row of rows) {
    lines.push(row.map(csvEscape).join(","));
  }
  return lines.join("\r\n") + "\r\n";
}

function downloadTextFile(filename, text) {
  const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

// Minimal CSV parser: handles quoted fields (with embedded commas/quotes/
// newlines) since movie titles in this dataset really do contain commas.
// Not a general-purpose parser, but sufficient for the simple two-column
// (or fixed-shape) files this page produces and consumes.
function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let inQuotes = false;
  let i = 0;

  while (i < text.length) {
    const char = text[i];

    if (inQuotes) {
      if (char === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i += 2;
          continue;
        }
        inQuotes = false;
        i += 1;
        continue;
      }
      field += char;
      i += 1;
      continue;
    }

    if (char === '"') {
      inQuotes = true;
      i += 1;
      continue;
    }
    if (char === ",") {
      row.push(field);
      field = "";
      i += 1;
      continue;
    }
    if (char === "\r") {
      i += 1;
      continue;
    }
    if (char === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
      i += 1;
      continue;
    }
    field += char;
    i += 1;
  }

  if (field.length > 0 || row.length > 0) {
    row.push(field);
    rows.push(row);
  }

  return rows.filter((r) => !(r.length === 1 && r[0] === ""));
}

function downloadPriorities() {
  const priorities = collectPriorities();
  const rows = Object.entries(priorities).map(([title, priority]) => [title, priority]);
  const csv = buildCsv(["Title", "Priority"], rows);
  downloadTextFile("priority.csv", csv);
}

function applyUploadedPriorities(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const rows = parseCsv(String(reader.result));
      if (rows.length === 0) {
        priorityFileFeedback.textContent = "That file looks empty.";
        return;
      }

      const header = rows[0].map((cell) => cell.trim().toLowerCase());
      const titleIdx = header.indexOf("title");
      const priorityIdx = header.indexOf("priority");
      if (titleIdx === -1 || priorityIdx === -1) {
        priorityFileFeedback.textContent = 'Expected a "Title,Priority" CSV \u2014 columns not found.';
        return;
      }

      const priorityByTitle = new Map();
      for (const row of rows.slice(1)) {
        const title = row[titleIdx];
        const priority = parseInt(row[priorityIdx], 10);
        if (title) {
          priorityByTitle.set(title, Number.isFinite(priority) ? priority : 0);
        }
      }

      let matched = 0;
      moviesTbody.querySelectorAll("tr").forEach((tableRow) => {
        if (priorityByTitle.has(tableRow.dataset.title)) {
          setRowPriority(tableRow, priorityByTitle.get(tableRow.dataset.title));
          matched += 1;
        }
      });

      const unmatched = priorityByTitle.size - matched;
      priorityFileFeedback.textContent =
        unmatched > 0
          ? `Applied ${matched} priorities (${unmatched} title(s) in the file weren't found in this year's list).`
          : `Applied ${matched} priorities.`;
    } catch (err) {
      console.error(err);
      priorityFileFeedback.textContent = "Couldn't read that file. Check the console for details.";
    }
  };
  reader.readAsText(file);
}

function downloadSchedule() {
  if (lastScheduleResult.length === 0) {
    return;
  }

  const header = [
    "Title", "Categories", "Country", "Year", "Length", "Premiere",
    "Date 1", "Cinema 1", "Time 1",
    "Date 2", "Cinema 2", "Time 2",
    "Date 3", "Cinema 3", "Time 3",
    "Date 4", "Cinema 4", "Time 4",
    "Date 5", "Cinema 5", "Time 5",
    "Date 6", "Cinema 6", "Time 6",
    "Date 7", "Cinema 7", "Time 7",
    "Date 8", "Cinema 8", "Time 8",
    "Date 9", "Cinema 9", "Time 9",
  ];

  const rows = lastScheduleResult.map((entry) => {
    const row = [
      entry.title, entry.categories, entry.country, entry.year, entry.length, entry.premiere,
      entry.date, entry.cinema, entry.time,
    ];
    // Slots 2-9 are always blank: each picked movie has exactly one chosen
    // screening, written into slot 1 above.
    while (row.length < header.length) {
      row.push("");
    }
    return row;
  });

  downloadTextFile("picked_movies.csv", buildCsv(header, rows));
}

function formatCountry(country) {
  // Browsers should already be able to wrap at "/" with no help (it's a
  // standard line-break opportunity, same as in a URL) -- but Chrome
  // specifically doesn't reliably do this inside a narrow fixed-width
  // table cell. A zero-width space right after each slash gives it an
  // explicit, INVISIBLE break point -- no inserted spaces, no visible
  // change to the text, it just lets the line wrap there if it needs to.
  return country ? country.replace(/\//g, "/\u200B") : "";
}

function renderMoviesTable(movieList) {
  moviesTbody.innerHTML = "";

  for (const movie of movieList) {
    const tr = document.createElement("tr");
    tr.dataset.title = movie.title;

    tr.innerHTML = `
      <td>
        <span class="priority-stub">
          <input
            type="number"
            min="0"
            step="1"
            value="0"
            inputmode="numeric"
            aria-label="Priority for ${movie.title}"
            data-priority-input
          />
        </span>
      </td>
      <td class="movie-title">${movie.title}</td>
      <td class="movie-meta">${movie.categories || "&mdash;"}</td>
      <td class="movie-meta">${formatCountry(movie.country) || "&mdash;"}</td>
      <td class="movie-meta">${movie.year || "&mdash;"}</td>
      <td>${formatScreeningsCell(movie.screenings)}</td>
    `;

    moviesTbody.appendChild(tr);
  }

  // Highlight the ticket stub once a real priority is set.
  moviesTbody.querySelectorAll("[data-priority-input]").forEach((input) => {
    input.addEventListener("input", () => {
      const stub = input.closest(".priority-stub");
      stub.classList.toggle("priority-stub--active", Number(input.value) > 0);
    });
  });
}

function applyMovieFilter() {
  const query = movieFilterInput.value.trim().toLowerCase();
  const rows = moviesTbody.querySelectorAll("tr");

  rows.forEach((row) => {
    const haystack = row.textContent.toLowerCase();
    row.hidden = query.length > 0 && !haystack.includes(query);
  });
}

function applyBulkPriority() {
  const value = parseInt(bulkPriorityValueInput.value, 10);
  const safeValue = Number.isFinite(value) && value >= 0 ? value : 0;

  const visibleRows = Array.from(moviesTbody.querySelectorAll("tr")).filter(
    (row) => !row.hidden
  );

  visibleRows.forEach((row) => setRowPriority(row, safeValue));

  const query = movieFilterInput.value.trim();
  bulkPriorityFeedback.textContent = query
    ? `Set priority ${safeValue} on ${visibleRows.length} movie(s) matching "${query}".`
    : `Set priority ${safeValue} on all ${visibleRows.length} movies (no filter active).`;
}

function collectPriorities() {
  const priorities = {};
  moviesTbody.querySelectorAll("tr").forEach((row) => {
    const input = row.querySelector("[data-priority-input]");
    const value = parseInt(input.value, 10);
    priorities[row.dataset.title] = Number.isFinite(value) ? value : 0;
  });
  return priorities;
}

function renderWarnings(warnings) {
  if (!warnings || warnings.length === 0) {
    warningsBlock.hidden = true;
    warningsBlock.innerHTML = "";
    return;
  }

  const items = warnings
    .map(
      (w) =>
        `<li>${w.first_title} (${w.first_time}) &rarr; ${w.second_title} (${w.second_time}) &mdash; under 5 minutes to get there.</li>`
    )
    .join("");

  warningsBlock.innerHTML = `
    <strong>Tight squeeze:</strong> these back-to-back picks leave you almost no time to move.
    <ul>${items}</ul>
  `;
  warningsBlock.hidden = false;
}

function renderSchedule(schedule) {
  scheduleList.innerHTML = "";

  if (schedule.length === 0) {
    scheduleList.innerHTML = `<p class="subsection-note">Nothing scheduled yet &mdash; set a few priorities above and build again.</p>`;
    return;
  }

  // The backend already returns `schedule` sorted by absolute start time,
  // so entries sharing the same `date` are already contiguous -- group
  // them in a single pass rather than re-sorting.
  const dayBlocks = [];
  let currentDate = null;
  let currentEntries = null;

  for (const entry of schedule) {
    if (entry.date !== currentDate) {
      currentDate = entry.date;
      currentEntries = [];
      dayBlocks.push({ date: currentDate, entries: currentEntries });
    }
    currentEntries.push(entry);
  }

  for (const block of dayBlocks) {
    const section = document.createElement("section");
    section.className = "schedule-day";

    const heading = document.createElement("h4");
    heading.className = "schedule-day__heading";
    heading.textContent = block.date;
    section.appendChild(heading);

    const list = document.createElement("ol");
    list.className = "ticket-list";

    for (const entry of block.entries) {
      const li = document.createElement("li");
      li.className = "ticket-item";
      li.innerHTML = `
        <div class="ticket-item__time">${entry.time}<span>${entry.date}</span></div>
        <div>
          <div class="ticket-item__title">${entry.title}</div>
          <div class="ticket-item__cinema">${entry.cinema}</div>
        </div>
        <div class="ticket-item__priority">priority ${entry.priority}</div>
      `;
      list.appendChild(li);
    }

    section.appendChild(list);
    scheduleList.appendChild(section);
  }
}

function formatConflictReason(conflict) {
  if (conflict.blocking == null) {
    // Pyodide converts Python None to JS `undefined` (not `null`) by
    // default, so this loose check intentionally catches both.
    return `<li class="reason-unavailable">${conflict.date} ${conflict.time} &mdash; not available</li>`;
  }
  if (conflict.blocking.length === 0) {
    return `<li>${conflict.date} ${conflict.time} &mdash; free, but outscored by the rest of the schedule</li>`;
  }
  const blockers = conflict.blocking.map((b) => `${b.title} (${b.time})`).join(", ");
  return `<li>${conflict.date} ${conflict.time} &mdash; clashes with ${blockers}</li>`;
}

function renderDiscarded(discarded) {
  discardedList.innerHTML = "";

  if (discarded.length === 0) {
    discardedList.innerHTML = `<li class="discard-item">Nothing left behind &mdash; everything you ranked made it in.</li>`;
    return;
  }

  for (const movie of discarded) {
    const li = document.createElement("li");
    li.className = "discard-item";
    const reasons = movie.conflicts.map(formatConflictReason).join("");
    li.innerHTML = `
      <div class="discard-item__head">
        <span class="discard-item__title">${movie.title}</span>
        <span class="discard-item__priority">priority ${movie.priority}</span>
      </div>
      <ul class="discard-item__reasons">${reasons}</ul>
    `;
    discardedList.appendChild(li);
  }
}

function renderResult(result) {
  lastScheduleResult = result.schedule;
  downloadScheduleButton.disabled = result.schedule.length === 0;

  const successRate =
    result.n_movies_with_priority > 0
      ? Math.round((100 * result.n_movies_selected) / result.n_movies_with_priority)
      : 0;

  resultsSummary.textContent =
    `Total priority: ${result.total_priority} \u00b7 ` +
    `${result.n_movies_selected}/${result.n_movies_with_priority} ranked movies scheduled (${successRate}%)`;

  renderWarnings(result.tight_transition_warnings);
  renderSchedule(result.schedule);
  renderDiscarded(result.discarded);

  resultsSection.hidden = false;
  resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function runPlan() {
  runButton.disabled = true;
  runButton.textContent = "Building\u2026";

  try {
    const priorities = collectPriorities();
    const availabilityRows = collectAvailability();
    const runPlanPy = pyodide.globals.get("run_plan");
    const resultPy = runPlanPy(pyodide.toPy(priorities), pyodide.toPy(availabilityRows), 0);
    const result = resultPy.toJs({ dict_converter: Object.fromEntries });
    resultPy.destroy();
    runPlanPy.destroy();
    renderResult(result);
  } catch (err) {
    console.error(err);
    resultsSummary.textContent = "Something went wrong while building the schedule. Check the console for details.";
    resultsSection.hidden = false;
  } finally {
    runButton.disabled = false;
    runButton.textContent = "Build my schedule";
  }
}

async function boot() {
  setStatus("Booting projector\u2026", 8);
  pyodide = await loadPyodide();

  setStatus("Loading the planner\u2026", 40);

  // Fetch the Python source files and write them into Pyodide's virtual
  // filesystem so they can be imported like normal local modules.
  const pyModules = ["planner_core.py", "planner_io.py", "app.py"];
  for (const filename of pyModules) {
    const response = await fetch(`../py/${filename}`);
    const source = await response.text();
    pyodide.FS.writeFile(filename, source);
  }

  setStatus("Reading the programme\u2026", 70);
  await pyodide.runPythonAsync(`
import app
movies_for_js = app.load_movies()
festival_days_for_js = app.get_festival_days()
run_plan = app.run_plan
  `);

  const moviesPy = pyodide.globals.get("movies_for_js");
  movies = moviesPy.toJs({ dict_converter: Object.fromEntries });
  moviesPy.destroy();

  const festivalDaysPy = pyodide.globals.get("festival_days_for_js");
  const festivalDays = festivalDaysPy.toJs({ dict_converter: Object.fromEntries });
  festivalDaysPy.destroy();

  setStatus(`Loaded ${movies.length} movies. Set your priorities below.`, 100);
  statusSection.hidden = true;
  moviesSection.hidden = false;

  renderAvailabilityDays(festivalDays);
  renderMoviesTable(movies);
}

movieFilterInput.addEventListener("input", () => {
  applyMovieFilter();
  bulkPriorityFeedback.textContent = "";
});
bulkPriorityApplyButton.addEventListener("click", applyBulkPriority);
runButton.addEventListener("click", runPlan);

downloadPrioritiesButton.addEventListener("click", downloadPriorities);
uploadPrioritiesTriggerButton.addEventListener("click", () => uploadPrioritiesInput.click());
uploadPrioritiesInput.addEventListener("change", () => {
  const file = uploadPrioritiesInput.files[0];
  if (file) {
    applyUploadedPriorities(file);
  }
  uploadPrioritiesInput.value = ""; // allow re-uploading the same filename later
});
downloadScheduleButton.addEventListener("click", downloadSchedule);

boot().catch((err) => {
  console.error(err);
  setStatus("Couldn't load the planner. Check the console for details.");
});
