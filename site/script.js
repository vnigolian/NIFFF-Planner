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
const selectedCount = document.getElementById("selected-count");
const availabilityDaysContainer = document.getElementById("availability-days");
const movieFilterInput = document.getElementById("movie-filter");
const bulkPriorityValueInput = document.getElementById("bulk-priority-value");
const bulkPriorityOverwriteCheckbox = document.getElementById("bulk-priority-overwrite");
const bulkPriorityApplyButton = document.getElementById("bulk-priority-apply");
const bulkPriorityFeedback = document.getElementById("bulk-priority-feedback");
const downloadPrioritiesButton = document.getElementById("download-priorities");
const uploadPrioritiesTriggerButton = document.getElementById("upload-priorities-trigger");
const uploadPrioritiesInput = document.getElementById("upload-priorities-input");
const priorityFileFeedback = document.getElementById("priority-file-feedback");
const runButton = document.getElementById("run-button");
const runSpinner = document.getElementById("run-spinner");
const nSimulationsInput = document.getElementById("n-simulations-input");
const objectiveSelect = document.getElementById("objective-select");
const minBreakInput = document.getElementById("min-break-input");
const cappedPriorityWarning = document.getElementById("capped-priority-warning");
const resultsSection = document.getElementById("results-section");
const resultsSummary = document.getElementById("results-summary");
const downloadScheduleCsvButton = document.getElementById("download-schedule-csv");
const downloadSchedulePdfButton = document.getElementById("download-schedule-pdf");
const highlightOfficialPdfButton = document.getElementById("highlight-official-pdf");
const pdfExportFeedback = document.getElementById("pdf-export-feedback");
const warningsBlock = document.getElementById("warnings-block");
const scheduleList = document.getElementById("schedule-list");
const discardedList = document.getElementById("discarded-list");
const languageSelect = document.getElementById("language-select");

let pyodide = null;
let movies = []; // [{title, categories, country, year, length, premiere, screenings}]
let lastScheduleResult = []; // populated after a successful run, used for the download button
let lastRenderedResult = null; // the full result object, used to re-render on language switch

/* ------------------------------------------------------------- i18n --- */
/* Translations live in lang/<code>.json -- one flat key->string map per
 * language, with {placeholder} substitution for dynamic values. Adding a
 * new language: drop a new lang/<code>.json file (copy lang/en.json as a
 * starting point) AND add a matching <option> to #language-select in
 * index.html -- a static site with no build step can't discover files on
 * its own, so this one extra step is unavoidable, but nothing else needs
 * to change.
 */

const LANGUAGE_STORAGE_KEY = "nifff-planner-language";
const DEFAULT_LANGUAGE = "en";

let currentTranslations = {};

async function loadLanguage(code) {
  const response = await fetch(`lang/${code}.json?v=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Couldn't load language file for "${code}" (HTTP ${response.status})`);
  }
  currentTranslations = await response.json();
}

function t(key, substitutions) {
  let text = currentTranslations[key];
  if (text == null) {
    console.warn(`Missing translation for key "${key}"`);
    return key;
  }
  if (substitutions) {
    for (const [name, value] of Object.entries(substitutions)) {
      text = text.replace(new RegExp(`\\{${name}\\}`, "g"), value);
    }
  }
  return text;
}

function applyStaticTranslations() {
  document.title = t("page_title");

  document.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
  document.querySelectorAll("[data-i18n-aria-label]").forEach((el) => {
    el.setAttribute("aria-label", t(el.dataset.i18nAriaLabel));
  });
}

async function setLanguage(code) {
  await loadLanguage(code);
  localStorage.setItem(LANGUAGE_STORAGE_KEY, code);
  applyStaticTranslations();

  // Re-render anything currently on screen that has its own dynamic text,
  // without re-fetching movies.csv or re-booting Pyodide.
  if (selectedCount.textContent) {
    updateSelectedCount();
  }
  if (!resultsSection.hidden && lastRenderedResult != null) {
    renderResult(lastRenderedResult);
  }
}

function getStoredLanguage() {
  return localStorage.getItem(LANGUAGE_STORAGE_KEY) || DEFAULT_LANGUAGE;
}

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
          <span data-i18n="availability_available_label">${t("availability_available_label")}</span>
        </label>
      </div>
      <div class="availability-day__times">
        <label for="begin-${day.date}" data-i18n="availability_from_label">${t("availability_from_label")}</label>
        <input
          type="time"
          id="begin-${day.date}"
          value="${day.default_begin}"
          data-begin-input
        />
        <label for="end-${day.date}" data-i18n="availability_to_label">${t("availability_to_label")}</label>
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
  updateSelectedCount();
}

function updateSelectedCount() {
  const rows = moviesTbody.querySelectorAll("tr");
  let selected = 0;
  rows.forEach((row) => {
    const value = Number(row.querySelector("[data-priority-input]").value) || 0;
    if (value > 0) {
      selected += 1;
    }
  });
  selectedCount.textContent = t("selected_count", { selected, total: rows.length });
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
  downloadBlob(filename, new Blob([text], { type: "text/csv;charset=utf-8" }));
}

function downloadBinaryFile(filename, bytes, mimeType) {
  // `bytes` arrives from Pyodide as a Uint8Array (a Python `bytes`
  // object converted via .toJs()) -- Blob accepts that directly.
  downloadBlob(filename, new Blob([bytes], { type: mimeType }));
}

function downloadBlob(filename, blob) {
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
        priorityFileFeedback.textContent = t("upload_priorities_empty");
        return;
      }

      const header = rows[0].map((cell) => cell.trim().toLowerCase());
      const titleIdx = header.indexOf("title");
      const priorityIdx = header.indexOf("priority");
      if (titleIdx === -1 || priorityIdx === -1) {
        priorityFileFeedback.textContent = t("upload_priorities_bad_columns");
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
          ? t("upload_priorities_applied_with_unmatched", { matched, unmatched })
          : t("upload_priorities_applied", { matched });
    } catch (err) {
      console.error(err);
      priorityFileFeedback.textContent = t("upload_priorities_error");
    }
  };
  reader.readAsText(file);
}

function downloadScheduleCsv() {
  if (lastScheduleResult.length === 0) {
    return;
  }

  const header = [
    "Title", "Date", "Cinema", "Time",
    "Categories", "Country", "Year", "Length", "Premiere",
  ];

  const rows = lastScheduleResult.map((entry) => [
    entry.title, entry.date, entry.cinema, entry.time,
    entry.categories, entry.country, entry.year, entry.length, entry.premiere,
  ]);

  downloadTextFile("picked_movies.csv", buildCsv(header, rows));
}

let pyMuPdfLoadPromise = null;

async function ensurePyMuPdfLoaded() {
  // PyMuPDF is NOT needed for normal use of the site (building a
  // schedule, the CSV export) -- it's only fetched the first time
  // someone actually uses one of the PDF-export buttons, so everyone
  // else's page load stays exactly as fast as before this feature
  // existed. Cached in a module-level promise so a second click (either
  // PDF button) doesn't re-fetch/re-install it.
  if (pyMuPdfLoadPromise == null) {
    pyMuPdfLoadPromise = (async () => {
      await pyodide.loadPackage("micropip");
      const micropip = pyodide.pyimport("micropip");
      await micropip.install("pymupdf");
    })();
  }
  await pyMuPdfLoadPromise;
}

let officialPdfAssetsLoadPromise = null;

async function ensureOfficialPdfAssetsLoaded() {
  // build_highlighted_official_pdf() (unlike build_picked_movies_pdf())
  // needs two extra files that, like the .py modules, must be fetched
  // and written into Pyodide's virtual filesystem before Python can
  // read them as local paths -- fitz.open() and plain open()/json.load()
  // both expect a real local path, not a URL, unlike
  // pyodide.http.open_url() (used elsewhere for movies.csv). Only
  // fetched once, lazily, since most visits never need these at all.
  //
  // The fetch() URLs below are real HTTP paths relative to this page
  // (site/index.html), so "../data/..." is correct there -- but the
  // writeFile() DESTINATIONS are virtual-filesystem paths, a completely
  // different namespace with no "data" directory node in it (nothing
  // ever creates one) -- those must be flat filenames, matching the
  // same convention already used for the .py modules. Mixing these two
  // path spaces up caused a real ENOENT at runtime before this was
  // caught and fixed; see OFFICIAL_PDF_PATH/PDF_LAYOUT_JSON_PATH in
  // app.py for the Python side of this same fix.
  if (officialPdfAssetsLoadPromise == null) {
    officialPdfAssetsLoadPromise = (async () => {
      const [pdfResponse, layoutResponse] = await Promise.all([
        fetch(`../data/GRILLE-HORAIRE_NIFFF2026.pdf?v=${Date.now()}`, { cache: "no-store" }),
        fetch(`../data/pdf_layout.json?v=${Date.now()}`, { cache: "no-store" }),
      ]);
      const pdfBytes = new Uint8Array(await pdfResponse.arrayBuffer());
      const layoutText = await layoutResponse.text();
      pyodide.FS.writeFile("GRILLE-HORAIRE_NIFFF2026.pdf", pdfBytes);
      pyodide.FS.writeFile("pdf_layout.json", layoutText);
    })();
  }
  await officialPdfAssetsLoadPromise;
}

async function downloadSchedulePdf() {
  if (lastScheduleResult.length === 0) {
    return;
  }

  pdfExportFeedback.textContent = t("pdf_export_loading");
  try {
    await ensurePyMuPdfLoaded();
    pdfExportFeedback.textContent = t("pdf_export_building");

    const buildPdf = pyodide.globals.get("build_picked_movies_pdf");
    const pdfBytesPy = buildPdf(pyodide.toPy(lastScheduleResult));
    const pdfBytes = pdfBytesPy.toJs();
    pdfBytesPy.destroy();
    buildPdf.destroy();

    downloadBinaryFile("picked_movies.pdf", pdfBytes, "application/pdf");
    pdfExportFeedback.textContent = "";
  } catch (err) {
    console.error(err);
    pdfExportFeedback.textContent = t("pdf_export_error");
  }
}

async function highlightOfficialPdf() {
  if (lastScheduleResult.length === 0) {
    return;
  }

  pdfExportFeedback.textContent = t("pdf_export_loading");
  try {
    await Promise.all([ensurePyMuPdfLoaded(), ensureOfficialPdfAssetsLoaded()]);
    pdfExportFeedback.textContent = t("pdf_export_building");

    const buildHighlight = pyodide.globals.get("build_highlighted_official_pdf");
    const resultPy = buildHighlight(pyodide.toPy(lastScheduleResult));
    const result = resultPy.toJs({ dict_converter: Object.fromEntries });
    resultPy.destroy();
    buildHighlight.destroy();

    downloadBinaryFile(
      "GRILLE-HORAIRE_NIFFF2026_highlighted.pdf",
      result.pdf_bytes,
      "application/pdf"
    );

    let summary = t("pdf_highlight_summary", {
      matched: result.matched_count,
      total: result.total_count,
    });
    const unmatchedCount = result.total_count - result.matched_count;
    if (unmatchedCount > 0) {
      summary += t("pdf_highlight_unmatched_note", {
        unmatched: unmatchedCount,
        titles: result.unmatched_titles.join(", "),
      });
    }
    pdfExportFeedback.textContent = summary;
  } catch (err) {
    console.error(err);
    pdfExportFeedback.textContent = t("pdf_export_error");
  }
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
            aria-label="${t("priority_input_aria_label", { title: movie.title })}"
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
      updateSelectedCount();
    });
  });

  updateSelectedCount();
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
  const overwrite = bulkPriorityOverwriteCheckbox.checked;

  const visibleRows = Array.from(moviesTbody.querySelectorAll("tr")).filter(
    (row) => !row.hidden
  );

  let updatedCount = 0;
  let skippedCount = 0;

  visibleRows.forEach((row) => {
    const currentValue = Number(row.querySelector("[data-priority-input]").value) || 0;
    if (!overwrite && currentValue > 0) {
      skippedCount += 1;
      return;
    }
    setRowPriority(row, safeValue);
    updatedCount += 1;
  });

  const query = movieFilterInput.value.trim();
  const matchDescription = query
    ? t("bulk_priority_match_filtered", { query })
    : t("bulk_priority_match_all");
  const skippedNote =
    skippedCount > 0 ? t("bulk_priority_skipped_note", { count: skippedCount }) : "";
  bulkPriorityFeedback.textContent = t("bulk_priority_feedback", {
    value: safeValue,
    updated: updatedCount,
    match_description: matchDescription,
    skipped_note: skippedNote,
  });
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
        `<li>${t("warnings_item", {
          first_title: w.first_title,
          first_time: w.first_time,
          second_title: w.second_title,
          second_time: w.second_time,
        })}</li>`
    )
    .join("");

  warningsBlock.innerHTML = `
    <strong>${t("warnings_heading")}</strong> ${t("warnings_note")}
    <ul>${items}</ul>
  `;
  warningsBlock.hidden = false;
}

const SHIFT_MINUTES = 5 * 60;
const MINUTES_PER_DAY = 24 * 60;

function parseHhMmToMinutes(hhmm) {
  const [hours, minutes] = hhmm.split(":").map(Number);
  return hours * 60 + minutes;
}

function shiftedMinutesOfDay(hhmm) {
  // Same 5-hour shift used throughout the Python backend (see
  // planner_io.py): a screening listed as "00:30" is really late at
  // night, not early in the morning -- shifting every clock time 5
  // hours earlier (wrapping within the day) puts it in the right
  // relative order for gap math. Without this, a late-night movie
  // followed by a past-midnight one produces a large NEGATIVE "gap"
  // (e.g. 23:45 + 90min vs. a 00:30 start), since plain clock-time
  // arithmetic has no idea 00:30 is actually LATER than 23:45 here.
  return ((parseHhMmToMinutes(hhmm) - SHIFT_MINUTES) % MINUTES_PER_DAY + MINUTES_PER_DAY) % MINUTES_PER_DAY;
}

function parseLengthToMinutes(length) {
  // Format is "NNN'" (e.g. "137'"); some entries (a couple of
  // role-playing-game events in the real catalog) have no listed
  // length at all -- return null rather than guessing.
  if (!length) {
    return null;
  }
  const minutes = parseInt(length.replace("'", ""), 10);
  return Number.isFinite(minutes) ? minutes : null;
}

function formatBreakDuration(minutes) {
  if (minutes < 60) {
    return `${minutes} min`;
  }
  const hours = Math.floor(minutes / 60);
  const remaining = minutes % 60;
  return remaining === 0 ? `${hours}h` : `${hours}h${remaining.toString().padStart(2, "0")}`;
}

function renderSchedule(schedule) {
  scheduleList.innerHTML = "";

  if (schedule.length === 0) {
    scheduleList.innerHTML = `<p class="subsection-note">${t("schedule_empty")}</p>`;
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

    for (let i = 0; i < block.entries.length; i++) {
      const entry = block.entries[i];
      const nextEntry = i + 1 < block.entries.length ? block.entries[i + 1] : null;

      const li = document.createElement("li");
      li.className = "ticket-item";
      li.innerHTML = `
        <div class="ticket-item__time">${entry.time}<span>${entry.date}</span></div>
        <div>
          <div class="ticket-item__title">${entry.title}<span class="ticket-item__category">${entry.categories || ""}</span></div>
          <div class="ticket-item__cinema">${entry.cinema}</div>
        </div>
        <div class="ticket-item__priority">${t("discarded_priority", { priority: entry.priority })}</div>
      `;

      // Break time before the NEXT movie, shown at the end of THIS one --
      // only meaningful between two movies on the SAME day (the last
      // movie of a day has no "next" in any scheduling-relevant sense),
      // and only when this movie's length is actually known (a couple of
      // real catalog entries, like RPG events, have no listed length).
      const lengthMinutes = parseLengthToMinutes(entry.length);
      if (nextEntry != null && lengthMinutes != null) {
        const endMinutes = shiftedMinutesOfDay(entry.time) + lengthMinutes;
        const breakMinutes = shiftedMinutesOfDay(nextEntry.time) - endMinutes;
        const breakRow = document.createElement("div");
        breakRow.className = "ticket-item__break";
        if (breakMinutes <= 5) {
          breakRow.classList.add("ticket-item__break--tight");
        }
        breakRow.textContent = t("break_time_label", { duration: formatBreakDuration(breakMinutes) });
        li.appendChild(breakRow);
      }

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
    return `<li class="reason-unavailable">${t("discarded_reason_unavailable", {
      date: conflict.date,
      time: conflict.time,
    })}</li>`;
  }
  if (conflict.blocking.length === 0) {
    return `<li>${t("discarded_reason_outscored", { date: conflict.date, time: conflict.time })}</li>`;
  }
  const blockers = conflict.blocking.map((b) => `${b.title} (${b.time})`).join(", ");
  return `<li>${t("discarded_reason_blocked", {
    date: conflict.date,
    time: conflict.time,
    blockers,
  })}</li>`;
}

function renderDiscarded(discarded) {
  discardedList.innerHTML = "";

  if (discarded.length === 0) {
    discardedList.innerHTML = `<li class="discard-item">${t("discarded_empty")}</li>`;
    return;
  }

  for (const movie of discarded) {
    const li = document.createElement("li");
    li.className = "discard-item";
    const reasons = movie.conflicts.map(formatConflictReason).join("");
    li.innerHTML = `
      <div class="discard-item__head">
        <span class="discard-item__title-group">
          <span class="discard-item__title">${movie.title}</span>
          <span class="discard-item__category">${movie.categories || ""}</span>
        </span>
        <span class="discard-item__priority">${t("discarded_priority", { priority: movie.priority })}</span>
      </div>
      <ul class="discard-item__reasons">${reasons}</ul>
    `;
    discardedList.appendChild(li);
  }
}

function renderResult(result) {
  lastScheduleResult = result.schedule;
  lastRenderedResult = result;
  const hasSchedule = result.schedule.length > 0;
  downloadScheduleCsvButton.disabled = !hasSchedule;
  downloadSchedulePdfButton.disabled = !hasSchedule;
  highlightOfficialPdfButton.disabled = !hasSchedule;

  const successRate =
    result.n_movies_with_priority > 0
      ? Math.round((100 * result.n_movies_selected) / result.n_movies_with_priority)
      : 0;

  let summaryText = t("results_summary", {
    total: result.total_priority,
    selected: result.n_movies_selected,
    total_with_priority: result.n_movies_with_priority,
    rate: successRate,
    elapsed: result.elapsed_seconds,
  });

  const stats = result.simulation_stats;
  if (stats != null && stats.n > 1) {
    const mean = Math.round(stats.mean * 10) / 10;
    summaryText += t("results_summary_with_stats", {
      n: stats.n,
      min: stats.min,
      mean,
      max: stats.max,
    });
    if (result.objective_used && result.objective_used !== "linear") {
      // With a non-linear objective, the schedule that WON wasn't
      // necessarily the one with the highest linear sum -- "Total
      // priority" above can legitimately be lower than "max" here.
      const objectiveLabel = t(`objective_${result.objective_used}`);
      summaryText += t("results_summary_objective_note", { objective: objectiveLabel });
    }
  }

  resultsSummary.textContent = summaryText;

  const hasCappedPriorities = result.n_capped_priorities > 0;
  cappedPriorityWarning.hidden = !hasCappedPriorities;
  if (hasCappedPriorities) {
    cappedPriorityWarning.textContent = t("capped_priority_warning", {
      count: result.n_capped_priorities,
      cap: result.exponential_priority_cap,
    });
  }

  renderWarnings(result.tight_transition_warnings);
  renderSchedule(result.schedule);
  renderDiscarded(result.discarded);

  resultsSection.hidden = false;
  resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function waitForNextPaint() {
  // Pyodide's solve call is synchronous from JS's point of view, so
  // nothing repaints once it starts -- this is the standard trick to
  // GUARANTEE one real paint happens first: requestAnimationFrame fires
  // right before the browser's next paint, and the nested setTimeout(0)
  // yields once more so that paint actually lands before we resume,
  // rather than risking it getting batched with whatever runs next.
  await new Promise((resolve) => requestAnimationFrame(resolve));
  await new Promise((resolve) => setTimeout(resolve, 0));
}

const MAX_N_SIMULATIONS = 10000;
const DEFAULT_N_SIMULATIONS = 1000;

async function runPlan() {
  const parsedNSimulations = parseInt(nSimulationsInput.value, 10);
  const nSimulations =
    Number.isFinite(parsedNSimulations) && parsedNSimulations >= 1
      ? Math.min(parsedNSimulations, MAX_N_SIMULATIONS)
      : DEFAULT_N_SIMULATIONS;
  const objective = objectiveSelect.value;

  const parsedMinBreak = parseInt(minBreakInput.value, 10);
  const minBreak = Number.isFinite(parsedMinBreak) && parsedMinBreak >= 0 ? parsedMinBreak : 0;

  // NOTE: Pyodide runs on the main thread here, so a large number of
  // simulations can take a noticeable moment, during which the page
  // genuinely freezes (no repaints, no further UI updates) -- there's
  // no way to show LIVE progress without moving Pyodide into a Web
  // Worker, which this site doesn't do yet. What we CAN do is guarantee
  // the spinner below actually gets painted before the freeze starts
  // (see waitForNextPaint), so at least the click feels acknowledged
  // rather than silently doing nothing.
  runButton.disabled = true;
  runSpinner.hidden = false;

  await waitForNextPaint();

  try {
    const priorities = collectPriorities();
    const availabilityRows = collectAvailability();
    const runPlanPy = pyodide.globals.get("run_plan");
    const resultPy = runPlanPy(
      pyodide.toPy(priorities),
      pyodide.toPy(availabilityRows),
      minBreak,
      "simulations",
      nSimulations,
      objective
    );
    const result = resultPy.toJs({ dict_converter: Object.fromEntries });
    resultPy.destroy();
    runPlanPy.destroy();
    renderResult(result);
  } catch (err) {
    console.error(err);
    resultsSummary.textContent = t("error_generic");
    resultsSection.hidden = false;
  } finally {
    runButton.disabled = false;
    runSpinner.hidden = true;
  }
}

function syncTableHeightToAvailabilityPanel() {
  // CSS alone can't make one element's height track a SIBLING's natural
  // content height when neither has an explicit size -- grid/flex
  // "stretch" only matches items to whichever is tallest, which becomes
  // a circular blow-up once the table's own content is also unbounded.
  // So this is done in JS instead: measure the availability panel's
  // real rendered height and cap table-wrap so that, TOGETHER WITH the
  // top-panels-row sitting above it in the same column, the combined
  // height matches the sidebar -- letting the table scroll internally
  // past that point instead of growing the page.
  //
  // Only applies in the wide, side-by-side layout (see the 880px
  // breakpoint in style.css) -- in the stacked mobile layout the
  // sidebar sits ABOVE everything else, so capping the table to its
  // height wouldn't make visual sense there.
  const availabilityPanel = document.querySelector(".availability-panel");
  const topPanelsRow = document.querySelector(".top-panels-row");
  const tableWrap = document.querySelector(".table-wrap");
  if (availabilityPanel == null || topPanelsRow == null || tableWrap == null) {
    return;
  }

  const isWideLayout = window.matchMedia("(min-width: 881px)").matches;
  if (!isWideLayout) {
    tableWrap.style.maxHeight = "";
    return;
  }

  const availabilityPanelHeight = availabilityPanel.getBoundingClientRect().height;
  const topPanelsRowHeight = topPanelsRow.getBoundingClientRect().height;
  const topPanelsRowMarginBottom = parseFloat(getComputedStyle(topPanelsRow).marginBottom) || 0;
  const remainingHeight = availabilityPanelHeight - topPanelsRowHeight - topPanelsRowMarginBottom;
  tableWrap.style.maxHeight = `${Math.max(remainingHeight, 0)}px`;
}

async function boot() {
  const t0 = performance.now();
  await setLanguage(getStoredLanguage());
  languageSelect.value = getStoredLanguage();

  setStatus(t("status_booting"), 8);
  pyodide = await loadPyodide();
  const tAfterLoadPyodide = performance.now();
  console.info(`[boot] loadPyodide(): ${(tAfterLoadPyodide - t0).toFixed(0)}ms`);

  setStatus(t("status_loading_planner"), 40);

  // Fetch the Python source files and write them into Pyodide's virtual
  // filesystem so they can be imported like normal local modules.
  //
  // cache: "no-store" + a cache-busting query param: these files change
  // frequently during development, and a stale cached copy of even ONE
  // of them (e.g. an old app.py with a different run_plan() signature)
  // produces confusing errors that look like a code bug rather than a
  // caching issue -- this has bitten us more than once, so don't rely
  // on the browser's default caching behavior here.
  //
  // Fetched CONCURRENTLY rather than one at a time: nothing depends on
  // these being WRITTEN to the virtual filesystem in any particular
  // order (only IMPORT order matters later, via the single `import app`
  // statement below), so there's no reason to pay for 4 sequential
  // round trips when one parallel batch does the same job faster.
  const cacheBuster = Date.now();
  const pyModules = ["clique_bound.py", "planner_core.py", "planner_io.py", "app.py"];
  await Promise.all(
    pyModules.map(async (filename) => {
      const response = await fetch(`../py/${filename}?v=${cacheBuster}`, { cache: "no-store" });
      const source = await response.text();
      pyodide.FS.writeFile(filename, source);
    })
  );
  const tAfterPyFetch = performance.now();
  console.info(`[boot] fetch .py modules: ${(tAfterPyFetch - tAfterLoadPyodide).toFixed(0)}ms`);

  setStatus(t("status_reading_programme"), 70);
  await pyodide.runPythonAsync(`
import app
movies_for_js = app.load_movies()
festival_days_for_js = app.get_festival_days()
run_plan = app.run_plan
build_picked_movies_pdf = app.build_picked_movies_pdf
build_highlighted_official_pdf = app.build_highlighted_official_pdf
  `);
  const tAfterPythonImport = performance.now();
  console.info(
    `[boot] import app + load_movies(): ${(tAfterPythonImport - tAfterPyFetch).toFixed(0)}ms`
  );

  const moviesPy = pyodide.globals.get("movies_for_js");
  movies = moviesPy.toJs({ dict_converter: Object.fromEntries });
  moviesPy.destroy();

  const festivalDaysPy = pyodide.globals.get("festival_days_for_js");
  const festivalDays = festivalDaysPy.toJs({ dict_converter: Object.fromEntries });
  festivalDaysPy.destroy();

  setStatus(t("status_loaded", { count: movies.length }), 100);
  statusSection.hidden = true;
  moviesSection.hidden = false;

  console.info(`[boot] TOTAL: ${(performance.now() - t0).toFixed(0)}ms`);

  renderAvailabilityDays(festivalDays);
  renderMoviesTable(movies);
  syncTableHeightToAvailabilityPanel();
}

window.addEventListener("resize", syncTableHeightToAvailabilityPanel);

movieFilterInput.addEventListener("input", () => {
  applyMovieFilter();
  bulkPriorityFeedback.textContent = "";
});
bulkPriorityApplyButton.addEventListener("click", applyBulkPriority);
runButton.addEventListener("click", runPlan);

nSimulationsInput.addEventListener("change", () => {
  const parsed = parseInt(nSimulationsInput.value, 10);
  if (Number.isFinite(parsed) && parsed > MAX_N_SIMULATIONS) {
    nSimulationsInput.value = MAX_N_SIMULATIONS;
  }
});

languageSelect.addEventListener("change", () => {
  setLanguage(languageSelect.value).catch((err) => console.error(err));
});

downloadPrioritiesButton.addEventListener("click", downloadPriorities);
uploadPrioritiesTriggerButton.addEventListener("click", () => uploadPrioritiesInput.click());
uploadPrioritiesInput.addEventListener("change", () => {
  const file = uploadPrioritiesInput.files[0];
  if (file) {
    applyUploadedPriorities(file);
  }
  uploadPrioritiesInput.value = ""; // allow re-uploading the same filename later
});
downloadScheduleCsvButton.addEventListener("click", downloadScheduleCsv);
downloadSchedulePdfButton.addEventListener("click", downloadSchedulePdf);
highlightOfficialPdfButton.addEventListener("click", highlightOfficialPdf);

boot().catch((err) => {
  console.error(err);
  // currentTranslations may not have loaded at all if THIS is what
  // failed -- fall back to a hardcoded English string rather than risk
  // t() itself throwing or silently returning a raw key here.
  const message =
    currentTranslations.status_load_failed ||
    "Couldn't load the planner. Check the console for details.";
  setStatus(message);
});
