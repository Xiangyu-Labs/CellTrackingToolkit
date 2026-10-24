// Figure editor on the result page.
//
// Everything here is presentation: group names, order and colours, wording,
// font size, resolution, file formats and composite layouts. Re-rendering
// creates a new analysis from the same datasets and parameters, so the
// numbers cannot change and the current result is kept as it is. The one
// exception is the physical scale (µm per pixel, minutes per frame): it
// converts lengths and times but leaves every p-value and classification alone.

import { api } from "./api.js?v=2";
import { escapeHtml } from "./state.js?v=6";
import { formatText } from "./strings.js?v=7";

const TYPES = ["Random", "Mixed", "Directed"];
const TEXT_LIMIT = 150;

let result = null;
let options = null;
let edit = null;

function defaultGroups() {
  return result.groups.map((group, index) => ({ source: index, name: group.name, original: group.name }));
}

function startState() {
  const style = structuredClone(result.style || options.defaults);
  const palette = options.group_colors;
  const groups = defaultGroups();
  return {
    groups,
    colors: groups.map((group, index) => style.group_colors?.[group.name] || palette[index % palette.length]),
    style,
    units: {
      microns_per_pixel: result.parameters?.microns_per_pixel ?? null,
      frame_interval_minutes: result.parameters?.frame_interval_minutes ?? null,
    },
    query: "",
  };
}

function catalog() {
  const seen = new Set();
  result.images.forEach(image => (image.texts || []).forEach(text => seen.add(text)));
  Object.keys(edit.style.text || {}).forEach(text => seen.add(text));
  return [...seen];
}

function collectStyle() {
  const style = structuredClone(edit.style);
  const palette = options.group_colors;
  style.group_colors = {};
  edit.groups.forEach((group, position) => {
    const color = edit.colors[position];
    if (color.toLowerCase() !== palette[position % palette.length].toLowerCase()) style.group_colors[group.name] = color;
  });
  style.type_colors = Object.fromEntries(
    Object.entries(style.type_colors || {}).filter(([type, color]) => color.toLowerCase() !== options.type_colors[type].toLowerCase())
  );
  style.text = Object.fromEntries(Object.entries(style.text || {}).filter(([key, value]) => key !== value));
  return style;
}

function groupRows() {
  return edit.groups.map((group, position) => `
    <div class="editor-group-row" data-position="${position}">
      <input type="color" data-field="group-color" value="${escapeHtml(edit.colors[position])}" aria-label="${escapeHtml(formatText("groupColor", { name: group.name }))}">
      <input type="text" data-field="group-name" value="${escapeHtml(group.name)}" maxlength="40" aria-label="${formatText("groupName")}">
      <small>${group.name === group.original ? "" : escapeHtml(formatText("wasNamed", { name: group.original }))}</small>
      <button class="icon-button" type="button" data-action="group-up" ${position ? "" : "disabled"} aria-label="${formatText("moveUp")}" title="${formatText("moveUp")}"><i data-lucide="arrow-up"></i></button>
      <button class="icon-button" type="button" data-action="group-down" ${position < edit.groups.length - 1 ? "" : "disabled"} aria-label="${formatText("moveDown")}" title="${formatText("moveDown")}"><i data-lucide="arrow-down"></i></button>
    </div>`).join("");
}

function typeRows() {
  return TYPES.map(type => `
    <label class="editor-inline"><input type="color" data-field="type-color" data-type="${type}" value="${escapeHtml(edit.style.type_colors?.[type] || options.type_colors[type])}"><span>${type}</span></label>`).join("");
}

function outputRows() {
  const style = edit.style;
  const [minDpi, maxDpi] = options.ranges.dpi;
  const [minScale, maxScale] = options.ranges.font_scale;
  const formats = options.formats.map(format => `
    <label class="editor-inline"><input type="checkbox" data-field="format" value="${format}" ${style.formats.includes(format) ? "checked" : ""} ${format === "png" ? "disabled" : ""}><span>${format.toUpperCase()}</span></label>`).join("");
  return `
    <div class="editor-grid">
      <label>${formatText("dpi")}<input type="number" data-field="dpi" min="${minDpi}" max="${maxDpi}" step="10" value="${style.dpi}"></label>
      <label>${formatText("fontScale")}<input type="number" data-field="font_scale" min="${minScale}" max="${maxScale}" step="0.1" value="${style.font_scale}"></label>
    </div>
    <div class="editor-checks">${formats}</div>
    <div class="editor-checks">
      <label class="editor-inline"><input type="checkbox" data-field="hide_figure_titles" ${style.hide_figure_titles ? "checked" : ""}><span>${formatText("hideTitles")}</span></label>
      <label class="editor-inline"><input type="checkbox" data-field="short_dataset_names" ${style.short_dataset_names ? "checked" : ""}><span>${formatText("shortNames")}</span></label>
    </div>
    <p class="editor-hint">${formatText("dpiHint")}</p>`;
}

function unitRows() {
  const value = key => edit.units[key] ?? "";
  return `
    <div class="editor-grid">
      <label>${formatText("micronsPerPixel")}<input type="number" data-field="microns_per_pixel" min="0" step="any" placeholder="px" value="${value("microns_per_pixel")}"></label>
      <label>${formatText("minutesPerFrame")}<input type="number" data-field="frame_interval_minutes" min="0" step="any" placeholder="frame" value="${value("frame_interval_minutes")}"></label>
    </div>
    <p class="editor-hint">${formatText("unitsHint")}</p>`;
}

function textRows() {
  const query = edit.query.trim().toLowerCase();
  const texts = catalog().filter(text => !query || text.toLowerCase().includes(query));
  const shown = texts.slice(0, TEXT_LIMIT);
  const rows = shown.map(text => {
    const replacement = edit.style.text?.[text];
    const hidden = replacement === "";
    return `
      <div class="editor-text-row" data-text="${escapeHtml(text)}">
        <span title="${escapeHtml(text)}">${escapeHtml(text)}</span>
        <input type="text" data-field="text" value="${escapeHtml(hidden || replacement === undefined ? "" : replacement)}" placeholder="${escapeHtml(text)}" maxlength="300" ${hidden ? "disabled" : ""} aria-label="${escapeHtml(formatText("replaceText", { text }))}">
        <label class="editor-inline"><input type="checkbox" data-field="text-hide" ${hidden ? "checked" : ""}><span>${formatText("hide")}</span></label>
      </div>`;
  }).join("");
  if (!catalog().length) return `<p class="editor-hint">${formatText("noTexts")}</p>`;
  const more = texts.length > shown.length ? `<p class="editor-hint">${formatText("moreTexts", { count: texts.length - shown.length })}</p>` : "";
  return rows + more;
}

function blockOptions(selected) {
  return Object.entries(options.blocks).map(([key, label]) => `<option value="${key}" ${key === selected ? "selected" : ""}>${escapeHtml(label)}</option>`).join("");
}

function compositeRows() {
  const items = edit.style.composites.map((composite, index) => {
    const columns = composite.columns.map((column, columnIndex) => `
      <div class="editor-column" data-column="${columnIndex}">
        <strong>${formatText("column", { number: columnIndex + 1 })}</strong>
        ${column.map((block, blockIndex) => `
          <div class="editor-block" data-block="${blockIndex}">
            <select data-field="block" aria-label="${formatText("panel")}">${blockOptions(block)}</select>
            <button class="icon-button" type="button" data-action="block-remove" aria-label="${formatText("removePanel")}" title="${formatText("removePanel")}"><i data-lucide="x"></i></button>
          </div>`).join("")}
        <div class="editor-actions">
          <button class="text-button" type="button" data-action="block-add">${formatText("addPanel")}</button>
          <button class="text-button" type="button" data-action="column-remove">${formatText("removeColumn")}</button>
        </div>
      </div>`).join("");
    return `
      <div class="editor-composite" data-composite="${index}">
        <div class="editor-grid">
          <label>${formatText("compositeName")}<input type="text" data-field="composite-name" value="${escapeHtml(composite.name)}" maxlength="60"></label>
          <label>${formatText("compositeTitle")}<input type="text" data-field="composite-title" value="${escapeHtml(composite.title || "")}" maxlength="200"></label>
        </div>
        <label class="editor-inline"><input type="checkbox" data-field="composite-letters" ${composite.panel_letters ? "checked" : ""}><span>${formatText("panelLetters")}</span></label>
        <div class="editor-columns">${columns}</div>
        <div class="editor-actions">
          <button class="text-button" type="button" data-action="column-add" ${composite.columns.length >= 4 ? "disabled" : ""}>${formatText("addColumn")}</button>
          <button class="text-button danger" type="button" data-action="composite-remove">${formatText("removeComposite")}</button>
        </div>
      </div>`;
  }).join("");
  const presets = Object.entries(options.presets).map(([key, preset]) => `<option value="${key}">${escapeHtml(preset.label)}</option>`).join("");
  return `
    ${items || `<p class="editor-hint">${formatText("noComposites")}</p>`}
    <div class="editor-actions">
      <select id="presetSelect" aria-label="${formatText("preset")}">${presets}</select>
      <button class="button secondary" type="button" data-action="preset-add">${formatText("addPreset")}</button>
      <button class="button secondary" type="button" data-action="composite-add">${formatText("addComposite")}</button>
    </div>`;
}

function render() {
  const root = document.querySelector("#figureEditor");
  const focused = document.activeElement?.id === "textSearch";
  root.innerHTML = `
    <summary>${formatText("editFigures")}</summary>
    <p class="editor-hint">${formatText("editorIntro")}</p>
    <fieldset><legend>${formatText("groupsLegend")}</legend>${groupRows()}<p class="editor-hint">${formatText("groupsHint")}</p></fieldset>
    <fieldset><legend>${formatText("typeColors")}</legend><div class="editor-checks">${typeRows()}</div></fieldset>
    <fieldset><legend>${formatText("output")}</legend>${outputRows()}</fieldset>
    <fieldset><legend>${formatText("units")}</legend>${unitRows()}</fieldset>
    <fieldset><legend>${formatText("wording")}</legend>
      <p class="editor-hint">${formatText("wordingHint")}</p>
      <label class="search-field"><i data-lucide="search"></i><input id="textSearch" type="search" value="${escapeHtml(edit.query)}" placeholder="${formatText("searchTexts")}" aria-label="${formatText("searchTexts")}"></label>
      <div class="editor-texts" id="textRows">${textRows()}</div>
    </fieldset>
    <fieldset><legend>${formatText("composites")}</legend><p class="editor-hint">${formatText("compositesHint")}</p>${compositeRows()}</fieldset>
    <div class="editor-footer">
      <button class="button secondary" type="button" data-action="export"><i data-lucide="file-down"></i><span>${formatText("exportStyle")}</span></button>
      <label class="button secondary"><i data-lucide="file-up"></i><span>${formatText("importStyle")}</span><input type="file" id="styleImport" accept="application/json,.json" hidden></label>
      <button class="text-button" type="button" data-action="reset">${formatText("resetStyle")}</button>
      <span class="editor-status" id="editorStatus" role="status" aria-live="polite"></span>
      <button class="button primary" type="button" data-action="rerender"><i data-lucide="refresh-cw"></i><span>${formatText("rerender")}</span></button>
    </div>`;
  root.open = true;
  window.lucide?.createIcons({ attrs: { "stroke-width": 1.8 } });
  if (focused) {
    const search = document.querySelector("#textSearch");
    search.focus();
    search.setSelectionRange(search.value.length, search.value.length);
  }
}

function renderTexts() {
  document.querySelector("#textRows").innerHTML = textRows();
}

function setStatus(message, error = false) {
  const status = document.querySelector("#editorStatus");
  status.textContent = message;
  status.classList.toggle("error", error);
}

function composite(element) {
  return edit.style.composites[Number(element.closest("[data-composite]").dataset.composite)];
}

function columnIndex(element) {
  return Number(element.closest("[data-column]").dataset.column);
}

function onInput(event) {
  const target = event.target;
  const field = target.dataset.field;
  if (target.id === "textSearch") {
    edit.query = target.value;
    renderTexts();
    return;
  }
  const position = Number(target.closest("[data-position]")?.dataset.position);
  if (field === "group-name") edit.groups[position].name = target.value;
  else if (field === "group-color") edit.colors[position] = target.value;
  else if (field === "type-color") edit.style.type_colors = { ...(edit.style.type_colors || {}), [target.dataset.type]: target.value };
  else if (field === "dpi") edit.style.dpi = Number(target.value);
  else if (field === "font_scale") edit.style.font_scale = Number(target.value);
  else if (field === "microns_per_pixel" || field === "frame_interval_minutes") {
    const number = Number(target.value);
    edit.units[field] = target.value.trim() && number > 0 ? number : null;
  }
  else if (field === "text") {
    const original = target.closest("[data-text]").dataset.text;
    const text = { ...(edit.style.text || {}) };
    if (target.value && target.value !== original) text[original] = target.value;
    else delete text[original];
    edit.style.text = text;
  } else if (field === "composite-name") composite(target).name = target.value;
  else if (field === "composite-title") composite(target).title = target.value;
}

function onChange(event) {
  const target = event.target;
  const field = target.dataset.field;
  if (field === "format") {
    const formats = new Set(edit.style.formats);
    if (target.checked) formats.add(target.value);
    else formats.delete(target.value);
    edit.style.formats = options.formats.filter(format => format === "png" || formats.has(format));
  } else if (field === "hide_figure_titles" || field === "short_dataset_names") {
    edit.style[field] = target.checked;
  } else if (field === "text-hide") {
    const row = target.closest("[data-text]");
    const text = { ...(edit.style.text || {}) };
    if (target.checked) text[row.dataset.text] = "";
    else delete text[row.dataset.text];
    edit.style.text = text;
    renderTexts();
  } else if (field === "composite-letters") {
    composite(target).panel_letters = target.checked;
  } else if (field === "block") {
    composite(target).columns[columnIndex(target)][Number(target.closest("[data-block]").dataset.block)] = target.value;
  } else if (target.id === "styleImport" && target.files?.[0]) {
    importStyle(target.files[0]);
  }
}

function uniqueName(name) {
  const taken = new Set(edit.style.composites.map(item => item.name.toLowerCase()));
  let candidate = name;
  for (let number = 2; taken.has(candidate.toLowerCase()); number += 1) candidate = `${name} ${number}`;
  return candidate;
}

async function onClick(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const action = button.dataset.action;
  const position = Number(button.closest("[data-position]")?.dataset.position);
  if (action === "group-up" || action === "group-down") {
    const other = action === "group-up" ? position - 1 : position + 1;
    [edit.groups[position], edit.groups[other]] = [edit.groups[other], edit.groups[position]];
    [edit.colors[position], edit.colors[other]] = [edit.colors[other], edit.colors[position]];
  } else if (action === "preset-add") {
    const preset = structuredClone(options.presets[document.querySelector("#presetSelect").value].composite);
    preset.name = uniqueName(preset.name);
    edit.style.composites.push({ title: "", ...preset });
  } else if (action === "composite-add") {
    edit.style.composites.push({ name: uniqueName("Composite"), title: "", panel_letters: true, columns: [["cell_appearance"]] });
  } else if (action === "composite-remove") {
    edit.style.composites.splice(Number(button.closest("[data-composite]").dataset.composite), 1);
  } else if (action === "column-add") {
    composite(button).columns.push(["group_trajectories"]);
  } else if (action === "column-remove") {
    const item = composite(button);
    item.columns.splice(columnIndex(button), 1);
    if (!item.columns.length) item.columns.push(["cell_appearance"]);
  } else if (action === "block-add") {
    const column = composite(button).columns[columnIndex(button)];
    if (column.length < 6) column.push("classification");
  } else if (action === "block-remove") {
    const item = composite(button);
    const column = item.columns[columnIndex(button)];
    column.splice(Number(button.closest("[data-block]").dataset.block), 1);
    if (!column.length) item.columns.splice(columnIndex(button), 1);
    if (!item.columns.length) item.columns.push(["cell_appearance"]);
  } else if (action === "reset") {
    edit = startState();
    edit.style = structuredClone(options.defaults);
    edit.colors = edit.groups.map((_group, index) => options.group_colors[index % options.group_colors.length]);
  } else if (action === "export") {
    exportStyle();
    return;
  } else if (action === "rerender") {
    await rerender(button);
    return;
  } else {
    return;
  }
  render();
}

function exportStyle() {
  const payload = {
    groups: edit.groups.map(group => ({ source_name: group.original, name: group.name })),
    style: collectStyle(),
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `figure-style-${result.id}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

async function importStyle(file) {
  try {
    const payload = JSON.parse(await file.text());
    const style = payload.style || payload;
    edit.style = { ...structuredClone(options.defaults), ...style };
    edit.style.formats = options.formats.filter(format => format === "png" || (style.formats || []).includes(format));
    if (Array.isArray(payload.groups)) {
      const byOriginal = new Map(defaultGroups().map(group => [group.original, group]));
      const ordered = payload.groups.map(item => byOriginal.get(item.source_name) && { ...byOriginal.get(item.source_name), name: item.name }).filter(Boolean);
      if (ordered.length === result.groups.length) edit.groups = ordered;
    }
    const palette = options.group_colors;
    edit.colors = edit.groups.map((group, index) => edit.style.group_colors?.[group.name] || palette[index % palette.length]);
    render();
    setStatus(formatText("styleImported"));
  } catch (error) {
    setStatus(formatText("styleImportFailed"), true);
  }
}

async function rerender(button) {
  const names = edit.groups.map(group => group.name.trim());
  if (names.some(name => !name) || new Set(names.map(name => name.toLowerCase())).size !== names.length) {
    setStatus(formatText("incompleteGroups"), true);
    return;
  }
  button.disabled = true;
  setStatus(formatText("submitting"));
  try {
    const task = await api(`/api/analysis/${result.id}/rerender`, {
      method: "POST",
      body: JSON.stringify({
        groups: edit.groups.map((group, position) => ({ source_index: group.source, name: names[position] })),
        style: collectStyle(),
        units: edit.units,
      }),
    });
    location.assign(`/analysis/tasks/${task.id}`);
  } catch (error) {
    button.disabled = false;
    setStatus(error.message || formatText("requestFailed"), true);
  }
}

export function initializeFigureEditor(analysis, styleOptions) {
  result = analysis;
  options = styleOptions;
  edit = startState();
  const root = document.querySelector("#figureEditor");
  root.hidden = false;
  render();
  root.open = false;
  root.addEventListener("input", onInput);
  root.addEventListener("change", onChange);
  root.addEventListener("click", onClick);
}
