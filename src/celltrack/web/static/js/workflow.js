import { state, escapeHtml } from "./state.js?v=3";
import { t } from "./i18n.js?v=5";

let openViewer = () => {};

export function configureWorkflow(handlers) { openViewer = handlers.openViewer; }

function activeJob(datasetId, kind) {
  return state.jobs.find(job => job.kind === kind && job.dataset_ids.includes(datasetId) && !["completed", "cancelled"].includes(job.status) && !job.completed_dataset_ids.includes(datasetId));
}

function processState(dataset, kind = state.tab) {
  const job = activeJob(dataset.id, kind);
  if (job?.status === "failed") return "failed";
  if (job?.status === "cancelling") return "cancelling";
  if (job?.status === "running" && job.current_dataset_id === dataset.id) return "processing";
  if (job) return "queued";
  return dataset[kind].completed ? "completed" : "idle";
}

export function badge(dataset, kind) {
  const job = activeJob(dataset.id, kind);
  const status = processState(dataset, kind);
  const completed = status === "completed";
  const label = completed ? t(kind === "segmentation" ? "completedSeg" : "completedTrack")
    : status === "idle" ? t(kind === "segmentation" ? "idleSeg" : "idleTrack")
    : status === "processing" && kind === "segmentation" ? `${job?.item_progress || 0}/${job?.item_total || dataset.image_count}`
    : status === "processing" ? t("processingTrack")
    : t(status);
  const icon = completed ? "circle-check" : status === "failed" ? "circle-alert" : status === "processing" || status === "cancelling" ? "loader-circle" : status === "queued" ? "clock-3" : "circle-dashed";
  if (completed) return `<button type="button" class="badge done result-badge" data-id="${dataset.id}" data-kind="${kind}"><i data-lucide="${icon}"></i>${label}</button>`;
  return `<span class="badge ${status}"><i data-lucide="${icon}"></i>${label}</span>`;
}

export function filteredDatasets() {
  if (!state.overview) return [];
  const query = state.query.trim().toLowerCase();
  return state.overview.datasets.filter(dataset => {
    const matchesText = `${dataset.name} ${dataset.relative_path}`.toLowerCase().includes(query);
    return matchesText && (state.filter === "all" || processState(dataset) === state.filter);
  });
}

export function renderWorkflow() {
  if (!state.overview || state.tab === "compare") return;
  const tracking = state.tab === "tracking";
  document.querySelector("#stageTitle").textContent = t(tracking ? "tracking" : "segmentation");
  const filters = [
    ["all", "all"], ["idle", tracking ? "idleTrack" : "idleSeg"], ["queued", "queued"],
    ["processing", tracking ? "processingTrack" : "processingSeg"], ["completed", tracking ? "completedTrack" : "completedSeg"], ["failed", "failed"], ["cancelling", "cancelling"]
  ];
  const filterRoot = document.querySelector("#statusFilters");
  filterRoot.innerHTML = filters.map(([value, key]) => `<button type="button" data-filter="${value}" class="${state.filter === value ? "active" : ""}" aria-pressed="${state.filter === value}">${t(key)}</button>`).join("");
  filterRoot.querySelectorAll("button").forEach(button => button.addEventListener("click", () => { state.filter = button.dataset.filter; renderWorkflow(); }));

  const datasets = filteredDatasets();
  document.querySelector("#datasetList").innerHTML = datasets.map(dataset => `<div class="dataset-row ${state.selected.has(dataset.id) ? "selected" : ""}">
    <input class="dataset-check" type="checkbox" data-id="${dataset.id}" aria-label="${escapeHtml(dataset.name)}" ${state.selected.has(dataset.id) ? "checked" : ""}>
    <span class="dataset-name"><img src="${dataset.preview_url}" width="48" height="48" loading="lazy" alt=""><span><strong>${escapeHtml(dataset.name)}</strong><small>${escapeHtml(dataset.group_path || "Datasets")}</small></span></span>
    <span class="image-count">${dataset.image_count}</span><span class="status-stack">${badge(dataset, "segmentation")}${badge(dataset, "tracking")}</span></div>`).join("");
  document.querySelector("#datasetEmpty").hidden = datasets.length > 0;
  document.querySelectorAll(".dataset-check").forEach(input => input.addEventListener("change", () => { input.checked ? state.selected.add(input.dataset.id) : state.selected.delete(input.dataset.id); renderWorkflow(); }));
  document.querySelectorAll(".result-badge").forEach(button => button.addEventListener("click", () => openViewer(button.dataset.id, button.dataset.kind)));
  document.querySelector("#selectedCount").textContent = state.selected.size;
  document.querySelector("#runButton").disabled = state.selected.size === 0;
  document.querySelector("#runButton span").textContent = t(tracking ? "runTrack" : "runSeg");
  document.querySelector("#actionHint").textContent = state.selected.size ? t("runCount", { count: state.selected.size }) : t("choose");
  window.lucide?.createIcons({ attrs: { "stroke-width": 1.8 } });
}
