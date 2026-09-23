import { el, clearError } from "./lib/dom.js";
import * as status from "./lib/status.js";
import { placeholder } from "./tabs/placeholder.js";
import universeTab from "./tabs/universe.js";
import watchlistTab from "./tabs/watchlist.js";

// Order here is the order in the rail.
const TABS = [
  universeTab,
  watchlistTab,
  placeholder("company", "Company"),
  placeholder("ledgers", "Ledgers"),
];

function selectTab(id) {
  const tab = TABS.find((t) => t.id === id) ?? TABS[0];
  for (const btn of document.querySelectorAll(".tab-btn")) {
    btn.classList.toggle("active", btn.dataset.tab === tab.id);
  }
  clearError();
  tab.render(document.getElementById("main-pane"));
  history.replaceState(null, "", `#${tab.id}`);
}

document.getElementById("tab-list").append(...TABS.map((tab) =>
  el("button", { class: "tab-btn", type: "button", dataset: { tab: tab.id }, onclick: () => { location.hash = tab.id; } },
    tab.label)));
document.getElementById("error-close").addEventListener("click", clearError);
// Back, forward and typed #tab addresses switch tabs too.
window.addEventListener("hashchange", () => selectTab(location.hash.slice(1)));
status.start();
selectTab(location.hash.slice(1));
