import { api } from "./api.js?v=2";
import { applyStaticTranslations, t } from "./i18n.js?v=5";
import { state, escapeHtml, setTabInUrl } from "./state.js?v=3";
import { configureWorkflow, filteredDatasets, renderWorkflow } from "./workflow.js?v=5";
import { addGroup, analysisRequest, initializeCompare, removeActiveGroup, renderCompare, visibleGroupDatasets } from "./compare.js?v=6";

const $ = selector => document.querySelector(selector);
const icons = () => window.lucide?.createIcons({ attrs: { "stroke-width": 1.8 } });

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.remove("show"), 4000);
}

function showTab(tab, updateUrl = true) {
  state.tab = tab;
  state.selected.clear();
  state.filter = "all";
  $("#workflowView").hidden = tab === "compare";
  $("#compareView").hidden = tab !== "compare";
  document.querySelectorAll(".stage-button").forEach(button => {
    const active = button.dataset.tab === tab;
    button.classList.toggle("active", active);
    active ? button.setAttribute("aria-current", "page") : button.removeAttribute("aria-current");
  });
  if (updateUrl) setTabInUrl(tab);
  if (tab === "compare") { renderCompare(); loadHistory(); } else renderWorkflow();
  icons();
}

function applyLanguage() {
  applyStaticTranslations();
  $("#languageToggle").checked = state.language === "en";
  renderWorkflow();
  renderCompare();
  renderHistory();
  renderJobs();
  icons();
}

async function loadJobs() {
  try { state.jobs = await api("/api/jobs"); renderWorkflow(); renderJobs(); }
  catch (_error) { state.jobs = []; }
  const active = state.jobs.some(job => ["queued", "running", "cancelling"].includes(job.status));
  if (active && !state.poller) pollJobs();
}

async function pollJobs() {
  state.poller = setTimeout(async () => {
    state.poller = null;
    await Promise.all([loadJobs(), loadOverview(true)]);
  }, 1000);
}

function renderJobs() {
  const active = state.jobs.filter(job => ["queued", "running", "cancelling"].includes(job.status) && (state.tab === "compare" || job.kind === state.tab));
  const root = $("#jobBar");
  root.hidden = !active.length || state.tab === "compare";
  root.innerHTML = active.map(job => `<div class="job-item"><span><strong>${escapeHtml(job.current_dataset || t("queued"))}</strong> · ${job.progress}/${job.total}</span><button class="button secondary cancel-batch" data-id="${job.id}" type="button"><i data-lucide="square"></i>${t("cancelBatch")}</button></div>`).join("");
  root.querySelectorAll(".cancel-batch").forEach(button => button.addEventListener("click", async () => {
    if (!confirm(t("confirmCancel"))) return;
    button.disabled = true;
    try { await api(`/api/jobs/${button.dataset.id}/cancel`, { method: "POST" }); await loadJobs(); }
    catch (error) { toast(error.message); }
  }));
  icons();
}

async function loadOverview(quiet = false) {
  try { state.overview = await api("/api/overview"); renderWorkflow(); renderCompare(); }
  catch (error) { if (!quiet) toast(error.message); }
}

async function startJob() {
  const ids = [...state.selected];
  if (!ids.length) return;
  if (state.tab === "tracking") {
    const unavailable = state.overview.datasets.filter(dataset => ids.includes(dataset.id) && !dataset.segmentation.completed);
    if (unavailable.length) { toast(`${unavailable.length} datasets need segmentation`); return; }
  }
  $("#runButton").disabled = true;
  try {
    const force = state.overview.datasets.some(dataset => ids.includes(dataset.id) && dataset[state.tab].completed);
    await api(`/api/jobs/${state.tab}`, { method: "POST", body: JSON.stringify({ dataset_ids: ids, force }) });
    state.selected.clear();
    await loadJobs();
  } catch (error) { toast(error.message); renderWorkflow(); }
}

function openViewer(datasetId, kind) {
  const dataset = state.overview.datasets.find(item => item.id === datasetId);
  if (!dataset) return;
  state.viewer = { datasetId, kind, index: 1, total: dataset.image_count, name: dataset.name };
  $("#resultKind").textContent = t(kind === "segmentation" ? "segResult" : "trackResult");
  $("#resultTitle").textContent = dataset.name;
  $("#frameSlider").max = dataset.image_count;
  $("#resultDialog").showModal();
  loadFrame(1);
}

function loadFrame(index) {
  if (!state.viewer) return;
  const viewer = state.viewer;
  viewer.index = Math.max(1, Math.min(viewer.total, Number(index)));
  $("#frameSlider").value = viewer.index;
  $("#frameCounter").textContent = `${viewer.index} / ${viewer.total}`;
  $("#previousFrame").disabled = viewer.index === 1;
  $("#nextFrame").disabled = viewer.index === viewer.total;
  $("#viewerLoading").hidden = false;
  const image = $("#resultImage");
  image.onload = image.onerror = () => { $("#viewerLoading").hidden = true; };
  image.src = `/api/datasets/${viewer.datasetId}/results/${viewer.kind}/frames/${viewer.index}`;
  image.alt = `${viewer.name} ${viewer.index}`;
}

async function createComparison() {
  const taskWindow = window.open("/analysis/tasks/pending", "_blank");
  try {
    const task = await api("/api/analysis", { method: "POST", body: JSON.stringify(analysisRequest()) });
    const taskUrl = `/analysis/tasks/${task.id}`;
    if (taskWindow) {
      taskWindow.location.replace(taskUrl);
      taskWindow.opener = null;
    } else {
      location.assign(taskUrl);
    }
    refreshHistoryWhenComplete(task.id);
  } catch (error) {
    taskWindow?.close();
    toast(error.message || t("comparisonFailed"));
  }
}

async function refreshHistoryWhenComplete(taskId) {
  try {
    let task;
    do {
      task = await api(`/api/analysis/tasks/${taskId}`);
      if (["queued", "running"].includes(task.status)) {
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
    } while (["queued", "running"].includes(task.status));
    if (task.status === "completed") await loadHistory();
    else toast(task.error || t("comparisonFailed"));
  } catch (error) {
    toast(error.message || t("comparisonFailed"));
  }
}

async function loadHistory() {
  try { state.history = await api("/api/analysis"); renderHistory(); }
  catch (error) { toast(error.message); }
}

function renderHistory() {
  const root = $("#historyList");
  if (!root) return;
  if (!state.history.length) { root.innerHTML = `<p class="empty-state">${t("noHistory")}</p>`; return; }
  const locale = state.language === "zh" ? "zh-CN" : "en";
  root.innerHTML = state.history.map(result => {
    const created = new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "medium" }).format(new Date(result.created_at));
    const groups = result.groups.map(group => `${escapeHtml(group.name)} (${group.datasets.length})`).join(" · ");
    const url = new URL(result.result_url, location.origin).href;
    return `<div class="history-item"><a href="${result.result_url}" target="_blank" rel="noopener"><strong>${created}</strong><small>${groups}</small><code>${escapeHtml(url)}</code></a><button class="icon-button danger delete-analysis" data-id="${result.id}" aria-label="${t("deleteResult")}" title="${t("deleteResult")}"><i data-lucide="trash-2"></i></button></div>`;
  }).join("");
  root.querySelectorAll(".delete-analysis").forEach(button => button.addEventListener("click", async () => {
    if (!confirm(t("confirmDelete"))) return;
    button.disabled = true;
    try { await api(`/api/analysis/${button.dataset.id}`, { method: "DELETE" }); state.history = state.history.filter(result => result.id !== button.dataset.id); renderHistory(); }
    catch (error) { toast(error.message || t("deleteFailed")); button.disabled = false; }
  }));
  icons();
}

function bindEvents() {
  document.querySelectorAll(".stage-button").forEach(button => button.addEventListener("click", () => showTab(button.dataset.tab)));
  $("#sidebarToggle").addEventListener("click", () => { state.sidebarCollapsed = !state.sidebarCollapsed; $("#sidebar").classList.toggle("collapsed", state.sidebarCollapsed); localStorage.setItem("celltrack-sidebar-collapsed", state.sidebarCollapsed); });
  $("#languageToggle").addEventListener("change", event => { state.language = event.target.checked ? "en" : "zh"; localStorage.setItem("celltrack-language", state.language); applyLanguage(); });
  $("#datasetSearch").addEventListener("input", event => { state.query = event.target.value; renderWorkflow(); });
  $("#selectVisible").addEventListener("click", () => { filteredDatasets().forEach(dataset => state.selected.add(dataset.id)); renderWorkflow(); });
  $("#clearVisible").addEventListener("click", () => { filteredDatasets().forEach(dataset => state.selected.delete(dataset.id)); renderWorkflow(); });
  $("#runButton").addEventListener("click", startJob);
  $("#addGroup").addEventListener("click", () => { if (!addGroup()) toast(t("groupLimit")); });
  $("#removeGroup").addEventListener("click", removeActiveGroup);
  $("#groupSearch").addEventListener("input", event => {
    state.groupQuery = event.target.value;
    const query = state.groupQuery.trim().toLowerCase();
    document.querySelectorAll(".group-dataset-option").forEach(option => { option.hidden = !option.dataset.search.includes(query); });
  });
  $("#selectGroupVisible").addEventListener("click", () => { const group = state.groups[state.activeGroup]; visibleGroupDatasets().forEach(dataset => group.ids.add(dataset.id)); renderCompare(); });
  $("#clearGroupVisible").addEventListener("click", () => { const group = state.groups[state.activeGroup]; visibleGroupDatasets().forEach(dataset => group.ids.delete(dataset.id)); renderCompare(); });
  $("#analyzeButton").addEventListener("click", createComparison);
  $("#closeViewer").addEventListener("click", () => $("#resultDialog").close());
  $("#previousFrame").addEventListener("click", () => loadFrame(state.viewer.index - 1));
  $("#nextFrame").addEventListener("click", () => loadFrame(state.viewer.index + 1));
  $("#frameSlider").addEventListener("input", event => loadFrame(event.target.value));
  $("#resultDialog").addEventListener("close", () => { state.viewer = null; $("#resultImage").removeAttribute("src"); });
  addEventListener("popstate", () => { const tab = new URL(location.href).searchParams.get("tab"); showTab(["segmentation", "tracking", "compare"].includes(tab) ? tab : "segmentation", false); });
}

async function init() {
  bindEvents();
  configureWorkflow({ openViewer });
  $("#sidebar").classList.toggle("collapsed", state.sidebarCollapsed);
  applyStaticTranslations();
  $("#languageToggle").checked = state.language === "en";
  try {
    const [overview, options, jobs, history] = await Promise.all([api("/api/overview"), api("/api/analysis/options"), api("/api/jobs"), api("/api/analysis")]);
    state.overview = overview; state.jobs = jobs; state.history = history; initializeCompare(options);
    setTabInUrl(state.tab, true);
    showTab(state.tab, false);
    renderJobs(); renderHistory();
    if (jobs.some(job => ["queued", "running", "cancelling"].includes(job.status))) pollJobs();
  } catch (error) { toast(error.message || t("requestFailed")); }
  icons();
}

document.addEventListener("DOMContentLoaded", init);
