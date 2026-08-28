import { state, escapeHtml } from "./state.js?v=5";
import { formatText } from "./strings.js?v=4";

let openViewer = () => {};

export function configureWorkflow(handlers) { openViewer = handlers.openViewer; }

function activeJob(datasetId, kind) {
  return state.jobs.find(job => job.kind === kind && job.dataset_ids.includes(datasetId) && !["completed", "cancelled"].includes(job.status) && !job.completed_dataset_ids.includes(datasetId));
}

function processState(dataset, kind) {
  const job = activeJob(dataset.id, kind);
  if (job?.status === "failed") return "failed";
  if (job?.status === "cancelling") return "cancelling";
  if (job?.status === "running" && job.current_dataset_id === dataset.id) return "processing";
  if (job) return "queued";
  return dataset[kind].completed ? "completed" : "idle";
}

function workflowStage(dataset) {
  if (dataset.tracking.completed) return "tracked";
  if (dataset.segmentation.completed) return "segmented-not-tracked";
  return "not-segmented";
}

function taskStates(dataset) {
  const states = new Set(["idle"]);
  for (const kind of ["segmentation", "tracking"]) {
    const status = processState(dataset, kind);
    if (["queued", "processing", "failed", "cancelling"].includes(status)) {
      states.delete("idle");
      states.add(status);
    }
  }
  return states;
}

export function badge(dataset, kind) {
  const job = activeJob(dataset.id, kind);
  const status = processState(dataset, kind);
  const completed = status === "completed";
  const label = completed ? formatText(kind === "segmentation" ? "completedSeg" : "completedTrack")
    : status === "idle" ? formatText(kind === "segmentation" ? "idleSeg" : "idleTrack")
    : status === "processing" && kind === "segmentation" ? `${job?.item_progress || 0}/${job?.item_total || dataset.image_count}`
    : status === "processing" ? formatText("processingTrack")
    : formatText(status);
  const icon = completed ? "circle-check" : status === "failed" ? "circle-alert" : status === "processing" || status === "cancelling" ? "loader-circle" : status === "queued" ? "clock-3" : "circle-dashed";
  if (completed) return `<button type="button" class="badge done result-badge" data-id="${dataset.id}" data-kind="${kind}"><i data-lucide="${icon}"></i>${label}</button>`;
  return `<span class="badge ${status}"><i data-lucide="${icon}"></i>${label}</span>`;
}

export function filteredDatasets() {
  if (!state.overview) return [];
  const query = state.query.trim().toLowerCase();
  return state.overview.datasets.filter(dataset => {
    const matchesText = `${dataset.name} ${dataset.group_path || ""} ${dataset.relative_path}`.toLowerCase().includes(query);
    const matchesStage = !state.filters.stages.size || state.filters.stages.has(workflowStage(dataset));
    const statuses = taskStates(dataset);
    const matchesTask = !state.filters.tasks.size || [...state.filters.tasks].some(status => statuses.has(status));
    return matchesText && matchesStage && matchesTask;
  });
}

const filterGroups = {
  stages: [["not-segmented", "notSegmented"], ["segmented-not-tracked", "segmentedNotTracked"], ["tracked", "tracked"]],
  tasks: [["idle", "idle"], ["queued", "waiting"], ["processing", "processing"], ["failed", "failed"], ["cancelling", "cancelling"]],
};

function filterLabel(group, value) {
  const option = filterGroups[group].find(([key]) => key === value);
  return formatText(option?.[1] || value);
}

function trackingUnavailableText(count) {
  return formatText(count === 1 ? "trackingNeedsSegmentationOne" : "trackingNeedsSegmentation", { count });
}

function renderFilters() {
  const menu = document.querySelector("#filterMenu");
  const wasOpen = menu.open;
  for (const [group, options] of Object.entries(filterGroups)) {
    const root = document.querySelector(group === "stages" ? "#stageFilters" : "#taskFilters");
    root.innerHTML = options.map(([value, key]) => `<label><input type="checkbox" data-filter-group="${group}" value="${value}" ${state.filters[group].has(value) ? "checked" : ""}><span>${formatText(key)}</span></label>`).join("");
  }
  menu.querySelectorAll("input").forEach(input => input.addEventListener("change", () => {
    input.checked ? state.filters[input.dataset.filterGroup].add(input.value) : state.filters[input.dataset.filterGroup].delete(input.value);
    renderWorkflow();
  }));

  const enabled = [...state.filters.stages].map(value => ["stages", value]).concat([...state.filters.tasks].map(value => ["tasks", value]));
  const count = document.querySelector("#filterCount");
  count.textContent = enabled.length;
  count.hidden = !enabled.length;
  const active = document.querySelector("#activeFilters");
  active.hidden = !enabled.length;
  active.innerHTML = enabled.map(([group, value]) => `<button class="filter-chip" type="button" data-filter-group="${group}" data-filter-value="${value}"><span>${filterLabel(group, value)}</span><i data-lucide="x"></i></button>`).join("") + (enabled.length ? `<button id="clearFilters" class="text-button" type="button">${formatText("clearAll")}</button>` : "");
  active.querySelectorAll(".filter-chip").forEach(button => button.addEventListener("click", () => { state.filters[button.dataset.filterGroup].delete(button.dataset.filterValue); renderWorkflow(); }));
  active.querySelector("#clearFilters")?.addEventListener("click", () => { state.filters.stages.clear(); state.filters.tasks.clear(); renderWorkflow(); });
  menu.open = wasOpen;
}

export function renderWorkflow() {
  if (!state.overview || state.tab === "analysis") return;
  renderFilters();

  const datasets = filteredDatasets();
  document.querySelector("#datasetList").innerHTML = datasets.map(dataset => `<div class="dataset-row ${state.selected.has(dataset.id) ? "selected" : ""}">
    <input class="dataset-check" type="checkbox" data-id="${dataset.id}" aria-label="${escapeHtml(dataset.name)}" ${state.selected.has(dataset.id) ? "checked" : ""}>
    <span class="dataset-name"><img src="${dataset.preview_url}" width="48" height="48" loading="lazy" alt=""><span><strong>${escapeHtml(dataset.name)}</strong><small>${escapeHtml(dataset.group_path || "Datasets")}</small></span></span>
    <span class="image-count">${dataset.image_count}</span><span class="status-stack">${badge(dataset, "segmentation")}${badge(dataset, "tracking")}</span></div>`).join("");
  document.querySelector("#datasetEmpty").hidden = datasets.length > 0;
  document.querySelectorAll(".dataset-check").forEach(input => input.addEventListener("change", () => { input.checked ? state.selected.add(input.dataset.id) : state.selected.delete(input.dataset.id); renderWorkflow(); }));
  document.querySelectorAll(".result-badge").forEach(button => button.addEventListener("click", () => openViewer(button.dataset.id, button.dataset.kind)));
  document.querySelector("#selectedCount").textContent = state.selected.size;
  const selected = state.overview.datasets.filter(dataset => state.selected.has(dataset.id));
  const unavailable = selected.filter(dataset => !dataset.segmentation.completed);
  document.querySelector("#runSegmentation").disabled = !selected.length;
  const trackingButton = document.querySelector("#runTracking");
  trackingButton.disabled = !selected.length || unavailable.length > 0;
  trackingButton.title = unavailable.length ? trackingUnavailableText(unavailable.length) : "";
  document.querySelector("#actionHint").textContent = !selected.length ? formatText("choose")
    : unavailable.length ? trackingUnavailableText(unavailable.length)
    : formatText("selectedCount", { count: selected.length });
  window.lucide?.createIcons({ attrs: { "stroke-width": 1.8 } });
}
