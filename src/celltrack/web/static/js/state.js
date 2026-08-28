const allowedTabs = new Set(["process", "compare"]);
const requestedTab = new URL(location.href).searchParams.get("tab");

export const state = {
  tab: allowedTabs.has(requestedTab) ? requestedTab : "process",
  sidebarCollapsed: localStorage.getItem("celltrack-sidebar-collapsed") === "true",
  overview: null,
  jobs: [],
  analysisOptions: null,
  history: [],
  selected: new Set(),
  query: "",
  filters: {
    stages: new Set(),
    tasks: new Set(),
  },
  groups: [],
  activeGroup: 0,
  groupQuery: "",
  parameters: null,
  viewer: null,
  poller: null,
};

export function setTabInUrl(tab, replace = false) {
  const url = new URL(location.href);
  url.searchParams.set("tab", tab);
  history[replace ? "replaceState" : "pushState"]({ tab }, "", url);
}

export function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, char => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", "'":"&#39;", '"':"&quot;" })[char]);
}
