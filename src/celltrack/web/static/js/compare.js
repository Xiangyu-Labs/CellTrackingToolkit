import { state, escapeHtml } from "./state.js?v=3";
import { formatText } from "./strings.js?v=1";

const numericFields = [
  ["long_track_min_observations", "Long-track observations", 1],
  ["random_directionality_max", "Random directionality max", 0.01],
  ["directed_directionality_min", "Directed directionality min", 0.01],
  ["msd_max_lag", "MSD maximum lag", 1],
  ["msd_fit_points", "MSD fit points", 1],
  ["msd_min_fit_points", "MSD minimum fit points", 1],
  ["angle_bins", "Direction-angle bins", 1],
  ["representatives_per_type", "Representatives per type", 1],
];
const optionalCalibrationFields = [
  ["frame_interval_minutes", "Minutes per frame", 0.01],
  ["microns_per_pixel", "Micrometers per pixel", 0.01],
];

export function initializeCompare(options) {
  state.analysisOptions = options;
  state.parameters = structuredClone(options.defaults);
  if (!state.groups.length) {
    state.groups = [
      { name: "A", ids: new Set() },
      { name: "B", ids: new Set() },
    ];
  }
}

export function visibleGroupDatasets() {
  const query = state.groupQuery.trim().toLowerCase();
  const selectedByOtherGroups = new Set(state.groups.flatMap((group, index) => index === state.activeGroup ? [] : [...group.ids]));
  return (state.overview?.datasets || []).filter(dataset => dataset.tracking.completed
    && !selectedByOtherGroups.has(dataset.id)
    && `${dataset.name} ${dataset.relative_path}`.toLowerCase().includes(query));
}

export function groupsAreValid() {
  const names = state.groups.map(group => group.name.trim().toLowerCase());
  return state.groups.length >= 2 && state.groups.length <= 6 && state.groups.every(group => group.name.trim() && group.ids.size) && new Set(names).size === names.length;
}

function checkboxOptions(rootId, catalog, selected, parameterKey) {
  const root = document.querySelector(rootId);
  root.innerHTML = Object.entries(catalog).map(([key, label]) => `<label><input type="checkbox" value="${key}" ${selected.includes(key) ? "checked" : ""}><span>${escapeHtml(label)}</span></label>`).join("");
  root.querySelectorAll("input").forEach(input => input.addEventListener("change", () => {
    state.parameters[parameterKey] = [...root.querySelectorAll("input:checked")].map(item => item.value);
    updateCompareValidation();
  }));
}

export function renderCompare() {
  if (!state.overview || !state.analysisOptions || state.tab !== "compare") return;
  state.activeGroup = Math.min(state.activeGroup, state.groups.length - 1);
  const active = state.groups[state.activeGroup];
  const tabs = document.querySelector("#groupTabs");
  tabs.innerHTML = state.groups.map((group, index) => `<button class="group-tab" type="button" role="tab" data-index="${index}" aria-selected="${index === state.activeGroup}">${escapeHtml(group.name || `Group ${index + 1}`)}<span>${group.ids.size}</span></button>`).join("");
  tabs.querySelectorAll("button").forEach(button => button.addEventListener("click", () => { state.activeGroup = Number(button.dataset.index); state.groupQuery = ""; renderCompare(); }));
  const nameInput = document.querySelector("#groupName");
  nameInput.value = active.name;
  nameInput.oninput = () => { active.name = nameInput.value; renderGroupTabsOnly(); updateCompareValidation(); };
  document.querySelector("#removeGroup").disabled = state.groups.length <= 2;
  document.querySelector("#addGroup").disabled = state.groups.length >= 6;
  document.querySelector("#groupSearch").value = state.groupQuery;

  const query = state.groupQuery.trim().toLowerCase();
  const datasets = (state.overview?.datasets || []).filter(dataset => dataset.tracking.completed);
  const selectedByOtherGroups = new Set(state.groups.flatMap((group, index) => index === state.activeGroup ? [] : [...group.ids]));
  const list = document.querySelector("#groupDatasetList");
  list.innerHTML = datasets.length ? datasets.map(dataset => {
    const searchText = `${dataset.name} ${dataset.relative_path}`.toLowerCase();
    const unavailable = selectedByOtherGroups.has(dataset.id);
    return `<label class="group-dataset-option" data-search="${escapeHtml(searchText)}" ${searchText.includes(query) ? "" : "hidden"}><input type="checkbox" data-id="${dataset.id}" ${active.ids.has(dataset.id) ? "checked" : ""} ${unavailable ? "disabled" : ""}><span><strong>${escapeHtml(dataset.name)}</strong><small>${escapeHtml(dataset.group_path || "Datasets")}</small></span></label>`;
  }).join("") : `<div class="empty-state">${formatText("empty")}</div>`;
  list.querySelectorAll("input").forEach(input => input.addEventListener("change", () => { input.checked ? active.ids.add(input.dataset.id) : active.ids.delete(input.dataset.id); renderCompare(); }));

  const numericRoot = document.querySelector("#numericParameters");
  numericRoot.innerHTML = numericFields.map(([key, label, step]) => {
    const [minimum, maximum] = state.analysisOptions.ranges[key] || [undefined, undefined];
    return `<label>${label}<input type="number" data-key="${key}" step="${step}" min="${minimum}" max="${maximum}" value="${state.parameters[key]}"></label>`;
  }).join("") + optionalCalibrationFields.map(([key, label, step]) =>
    `<label>${label}<input type="number" data-key="${key}" data-optional="true" step="${step}" min="0" value="${state.parameters[key] ?? ""}" placeholder="Optional"></label>`
  ).join("");
  numericRoot.querySelectorAll("input").forEach(input => input.addEventListener("input", () => {
    state.parameters[input.dataset.key] = input.dataset.optional ? (input.value === "" ? null : Number(input.value)) : Number(input.value);
    updateCompareValidation();
  }));
  const fallback = document.querySelector("#fallbackTracks");
  fallback.checked = state.parameters.fallback_to_all_tracks;
  fallback.onchange = () => { state.parameters.fallback_to_all_tracks = fallback.checked; };
  checkboxOptions("#figureOptions", state.analysisOptions.figure_types, state.parameters.figure_types, "figure_types");
  checkboxOptions("#temporalOptions", state.analysisOptions.temporal_metrics, state.parameters.temporal_metrics, "temporal_metrics");
  checkboxOptions("#summaryOptions", state.analysisOptions.summary_metrics, state.parameters.summary_metrics, "summary_metrics");
  updateCompareValidation();
  window.lucide?.createIcons({ attrs: { "stroke-width": 1.8 } });
}

function renderGroupTabsOnly() {
  const tabs = document.querySelector("#groupTabs");
  tabs.querySelectorAll(".group-tab").forEach((tab, index) => {
    tab.childNodes[0].textContent = state.groups[index].name || `Group ${index + 1}`;
  });
}

export function updateCompareValidation() {
  const parameters = state.parameters;
  const figures = new Set(parameters.figure_types);
  const numericValid = numericFields.every(([key]) => Number.isFinite(parameters[key]))
    && parameters.random_directionality_max >= 0
    && parameters.random_directionality_max < parameters.directed_directionality_min
    && parameters.directed_directionality_min <= 1
    && parameters.msd_min_fit_points <= parameters.msd_fit_points
    && optionalCalibrationFields.every(([key]) => parameters[key] === null || (Number.isFinite(parameters[key]) && parameters[key] > 0));
  const selectionsValid = parameters.figure_types.length > 0
    && (!(figures.has("temporal_long") || figures.has("temporal_all")) || parameters.temporal_metrics.length > 0)
    && (!figures.has("parameter_distributions") || parameters.summary_metrics.length > 0);
  const selectedIds = state.groups.flatMap(group => [...group.ids]);
  const datasetsUnique = selectedIds.length === new Set(selectedIds).size;
  const valid = groupsAreValid() && datasetsUnique && numericValid && selectionsValid;
  const button = document.querySelector("#analyzeButton");
  button.disabled = !valid;
  button.setAttribute("aria-busy", "false");
  button.innerHTML = `<i data-lucide="chart-no-axes-combined"></i><span>${formatText("generate")}</span>`;
  document.querySelector("#compareHint").textContent = valid ? formatText("ready", { count: state.groups.length }) : formatText("incomplete");
  window.lucide?.createIcons({ attrs: { "stroke-width": 1.8 } });
}

export function addGroup() {
  if (state.groups.length >= 6) return false;
  state.groups.push({ name: String.fromCharCode(65 + state.groups.length), ids: new Set() });
  state.activeGroup = state.groups.length - 1;
  state.groupQuery = "";
  renderCompare();
  return true;
}

export function removeActiveGroup() {
  if (state.groups.length <= 2) return;
  state.groups.splice(state.activeGroup, 1);
  state.activeGroup = Math.max(0, state.activeGroup - 1);
  state.groupQuery = "";
  renderCompare();
}

export function analysisRequest() {
  return {
    groups: state.groups.map(group => ({ name: group.name.trim(), dataset_ids: [...group.ids] })),
    parameters: state.parameters,
  };
}
