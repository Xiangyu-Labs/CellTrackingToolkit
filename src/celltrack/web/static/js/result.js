import { api } from "./api.js?v=2";
import { escapeHtml } from "./state.js?v=2";

const language = localStorage.getItem("celltrack-language") || (navigator.language.toLowerCase().startsWith("zh") ? "zh" : "en");
const copy = language === "zh" ? {
  back:"返回比较", submitting:"正在提交比较任务", queued:"比较任务正在排队", running:"正在生成比较结果", loading:"正在加载分析结果", generated:"生成于", tracks:"条轨迹", csv:"下载数据", statistics:"下载统计", all:"下载全部", figure:"下载图片", failed:"无法加载分析结果", parameters:"比较参数", notSet:"未设置"
} : {
  back:"Back to compare", submitting:"Submitting comparison", queued:"Comparison is queued", running:"Generating comparison", loading:"Loading analysis result", generated:"Generated", tracks:"tracks", csv:"Download data", statistics:"Download statistics", all:"Download all", figure:"Download image", failed:"Could not load analysis result", parameters:"Compare parameters", notSet:"not set"
};

function formatParameters(parameters) {
  parameters = parameters || {};
  const scalarKeys = [
    "long_track_min_observations", "fallback_to_all_tracks", "random_directionality_max", "directed_directionality_min",
    "msd_max_lag", "msd_fit_points", "msd_min_fit_points", "angle_bins", "representatives_per_type"
  ];
  const lines = [copy.parameters];
  scalarKeys.forEach(key => lines.push(`${key}: ${parameters[key]}`));
  lines.push(`frame_interval_minutes: ${parameters.frame_interval_minutes ?? copy.notSet}`);
  lines.push(`microns_per_pixel: ${parameters.microns_per_pixel ?? copy.notSet}`);
  lines.push(`figure_types: ${(parameters.figure_types || []).join(", ")}`);
  lines.push(`temporal_metrics: ${(parameters.temporal_metrics || []).join(", ")}`);
  lines.push(`summary_metrics: ${(parameters.summary_metrics || []).join(", ")}`);
  return lines.join("\n");
}

function showTaskMetadata(task) {
  const groups = task.groups || [];
  const summary = document.querySelector("#groupSummary");
  summary.textContent = groups.map(group => {
    const count = group.dataset_count ?? group.dataset_ids?.length ?? 0;
    const label = language === "zh" ? "个数据集" : count === 1 ? "dataset" : "datasets";
    return `${group.name}: ${count} ${label}`;
  }).join(" · ");
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
    setLoading(copy.submitting);
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
    if (task.status === "failed") throw new Error(task.error || copy.failed);
    setLoading(task.status === "queued" ? copy.queued : copy.running);
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
}

async function renderResult(artifactId) {
  setLoading(copy.loading);
  const result = await api(`/api/analysis/${artifactId}`);
    const locale = language === "zh" ? "zh-CN" : "en";
    const created = new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "medium" }).format(new Date(result.created_at));
    document.title = `Analysis ${result.id}`;
    document.querySelector("#resultId").textContent = `/analysis/${result.id}`;
    document.querySelector("#resultMeta").textContent = `${copy.generated} ${created}`;
    document.querySelector("#csvLink").href = result.csv_url;
    document.querySelector("#csvLink span").textContent = copy.csv;
    const statisticsLink = document.querySelector("#statisticsLink");
    statisticsLink.hidden = !result.statistics_url;
    if (result.statistics_url) {
      statisticsLink.href = result.statistics_url;
      statisticsLink.querySelector("span").textContent = copy.statistics;
    }
    document.querySelector("#downloadLink").href = result.download_url;
    document.querySelector("#downloadLink span").textContent = copy.all;
    document.querySelector("#resultActions").hidden = false;
    const summary = document.querySelector("#groupSummary");
    summary.textContent = result.groups.map(group => `${group.name}: ${group.tracks} ${copy.tracks}`).join(" · ");
    summary.hidden = false;
    const parameterText = document.querySelector("#parameterText");
    parameterText.textContent = formatParameters(result.parameters);
    parameterText.hidden = false;
    document.querySelector("#imageList").innerHTML = result.images.map((image, index) => `<figure class="result-figure"><figcaption><h2>${escapeHtml(image.title)}</h2><a class="icon-button" href="${image.download_url}" aria-label="${copy.figure}" title="${copy.figure}"><i data-lucide="download"></i></a></figcaption><img src="${image.url}" alt="${escapeHtml(image.title)}" ${index ? 'loading="lazy"' : ""}></figure>`).join("");
    document.querySelector("#loading").hidden = true;
    window.lucide?.createIcons({ attrs: { "stroke-width": 1.8 } });
}

async function init() {
  document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
  document.querySelector("#backLink").ariaLabel = copy.back;
  document.querySelector("#backLink").title = copy.back;
  window.lucide?.createIcons({ attrs: { "stroke-width": 1.8 } });
  const parts = location.pathname.split("/").filter(Boolean);
  try {
    if (parts[1] === "tasks") await monitorTask(parts[2]);
    else await renderResult(parts[1]);
  } catch (error) {
    setLoading(error.message || copy.failed, true);
  }
}

document.addEventListener("DOMContentLoaded", init);
