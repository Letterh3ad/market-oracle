import { el, clearError } from "./lib/dom.js";
import * as status from "./lib/status.js";
import { placeholder } from "./tabs/placeholder.js";
import universeTab from "./tabs/universe.js";
import watchlistTab from "./tabs/watchlist.js";
import companyTab from "./tabs/company.js";

// Order here is the order in the rail.
const TABS = [
  universeTab,
  watchlistTab,
  companyTab,
  placeholder("ledgers", "Ledgers"),
];

// "#company/7203.T" opens a tab with an argument.
function selectTab(hash) {
  const [id, ...rest] = hash.split("/");
  const arg = rest.length ? decodeURIComponent(rest.join("/")) : null;
  const tab = TABS.find((t) => t.id === id) ?? TABS[0];
  for (const btn of document.querySelectorAll(".tab-btn")) {
    btn.classList.toggle("active", btn.dataset.tab === tab.id);
  }
  clearError();
  tab.render(document.getElementById("main-pane"), arg);
  history.replaceState(null, "", `#${tab.id}${arg ? `/${encodeURIComponent(arg)}` : ""}`);
}

document.getElementById("tab-list").append(...TABS.map((tab) =>
  el("button", { class: "tab-btn", type: "button", dataset: { tab: tab.id }, onclick: () => { location.hash = tab.id; } },
    tab.label)));
document.getElementById("error-close").addEventListener("click", clearError);
// Back, forward and typed #tab addresses switch tabs too.
window.addEventListener("hashchange", () => selectTab(location.hash.slice(1)));
status.start();
selectTab(location.hash.slice(1));
