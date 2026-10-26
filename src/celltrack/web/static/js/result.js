import { api } from "./api.js?v=2";
import { initializeFigureEditor } from "./figure-editor.js?v=2";
import { escapeHtml } from "./state.js?v=6";
import { formatText } from "./strings.js?v=8";

function formatParameters(parameters) {
  parameters = parameters || {};
  const scalarKeys = [
    "long_track_min_observations", "fallback_to_all_tracks", "random_directionality_max", "directed_directionality_min",
    "msd_max_lag", "msd_fit_points", "msd_min_fit_points", "angle_bins", "representatives_per_type"
  ];
  const lines = [formatText("parameters")];
  scalarKeys.forEach(key => lines.push(`${key}: ${parameters[key]}`));
  lines.push(`frame_interval_minutes: ${parameters.frame_interval_minutes ?? formatText("notSet")}`);
  lines.push(`microns_per_pixel: ${parameters.microns_per_pixel ?? formatText("notSet")}`);
  lines.push(`figure_types: ${(parameters.figure_types || []).join(", ")}`);
  lines.push(`temporal_metrics: ${(parameters.temporal_metrics || []).join(", ")}`);
  lines.push(`summary_metrics: ${(parameters.summary_metrics || []).join(", ")}`);
  return lines.join("\n");
}

function figureWarnings(image) {
  const warnings = image.warnings || [];
  if (!warnings.length) return "";
  return `<div class="figure-warnings" role="note"><strong>${formatText("figureWarnings")}</strong><ul>${warnings.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>`;
}

function showTaskMetadata(task) {
  const groups = task.groups || [];
  const summary = document.querySelector("#groupSummary");
  summary.textContent = groups.map(group => {
    const count = group.dataset_count ?? group.dataset_ids?.length ?? 0;
    const label = count === 1 ? "dataset" : "datasets";
    return `${group.name}: ${count} ${label}`;
  }).join(" | ");
  summary.hidden = !groups.length;
  const parameterText = document.querySelector("#parameterText");
  parameterText.textContent = formatParameters(task.parameters);
  parameterText.hidden = false;
}

function setLoading(message, error = false) {
  const loading = document.querySelector("#loading");
  loading.querySelector("span").textContent = message;
  loading.classList.toggle("error", error);
  const icon = loading.querySelector("svg, i");
  if (icon) icon.hidden = error;
}

async function monitorTask(taskId) {
  document.querySelector("#resultId").textContent = taskId === "pending" ? "" : `/analysis/tasks/${taskId}`;
  if (taskId === "pending") {
    setLoading(formatText("submitting"));
    return;
  }
  while (true) {
    const task = await api(`/api/analysis/tasks/${taskId}`);
    showTaskMetadata(task);
    document.querySelector("#resultMeta").textContent = task.message;
    if (task.status === "completed" && task.result?.result_url) {
      location.replace(task.result.result_url);
      return;
    }
    if (task.status === "failed") throw new Error(task.error || formatText("resultFailed"));
    setLoading(task.status === "queued" ? formatText("analysisQueued") : formatText("analysisRunning"));
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
}

async function renderResult(artifactId) {
  setLoading(formatText("loadingAnalysis"));
  const result = await api(`/api/analysis/${artifactId}`);
  const created = new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "medium" }).format(new Date(result.created_at));
  document.title = `Analysis ${result.id}`;
  document.querySelector("#resultId").textContent = `/analysis/${result.id}`;
  const meta = document.querySelector("#resultMeta");
  meta.textContent = `${formatText("generated")} ${created}`;
  if (result.derived_from) {
    meta.append(" | ", formatText("derivedFrom"), " ");
    const link = document.createElement("a");
    link.href = `/analysis/${result.derived_from}`;
    link.textContent = `/analysis/${result.derived_from}`;
    meta.append(link);
  }
  document.querySelector("#csvLink").href = result.csv_url;
  document.querySelector("#csvLink span").textContent = formatText("csv");
  const statisticsLink = document.querySelector("#statisticsLink");
  statisticsLink.hidden = !result.statistics_url;
  if (result.statistics_url) {
    statisticsLink.href = result.statistics_url;
    statisticsLink.querySelector("span").textContent = formatText("statistics");
  }
  document.querySelector("#downloadLink").href = result.download_url;
  document.querySelector("#downloadLink span").textContent = formatText("downloadAll");
  document.querySelector("#resultActions").hidden = false;
  const summary = document.querySelector("#groupSummary");
  summary.textContent = result.groups.map(group => `${group.name}: ${group.tracks} ${formatText("tracks")}`).join(" | ");
  summary.hidden = false;
  const parameterText = document.querySelector("#parameterText");
  parameterText.textContent = formatParameters(result.parameters);
  parameterText.hidden = false;
  document.querySelector("#imageList").innerHTML = result.images.map((image, index) => {
    const extra = Object.entries(image.downloads || {}).filter(([format]) => format !== "png")
      .map(([format, url]) => `<a class="text-button" href="${url}">${format.toUpperCase()}</a>`).join("");
    return `<figure class="result-figure"><figcaption><h2>${escapeHtml(image.title)}</h2><span class="figure-downloads">${extra}<a class="icon-button" href="${image.download_url}" aria-label="${formatText("figure")}" title="${formatText("figure")}"><i data-lucide="download"></i></a></span></figcaption>${figureWarnings(image)}<img src="${image.url}" alt="${escapeHtml(image.title)}" ${index ? 'loading="lazy"' : ""}></figure>`;
  }).join("");
  document.querySelector("#loading").hidden = true;
  try {
    const options = await api("/api/analysis/options");
    if (options.style) initializeFigureEditor(result, options.style);
  } catch (error) {
    // The figures are already on screen; the editor is optional.
  }
  window.lucide?.createIcons({ attrs: { "stroke-width": 1.8 } });
}

async function init() {
  document.querySelector("#backLink").ariaLabel = formatText("back");
  document.querySelector("#backLink").title = formatText("back");
  window.lucide?.createIcons({ attrs: { "stroke-width": 1.8 } });
  const parts = location.pathname.split("/").filter(Boolean);
  try {
    if (parts[1] === "tasks") await monitorTask(parts[2]);
    else await renderResult(parts[1]);
  } catch (error) {
    setLoading(error.message || formatText("resultFailed"), true);
  }
}

document.addEventListener("DOMContentLoaded", init);
