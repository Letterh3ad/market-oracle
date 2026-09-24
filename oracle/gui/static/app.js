import { el, clearError } from "./lib/dom.js";
import * as control from "./lib/control.js";
import * as status from "./lib/status.js";
import * as settings from "./lib/settings.js";
import universeTab from "./tabs/universe.js";
import watchlistTab from "./tabs/watchlist.js";
import discoverTab from "./tabs/discover.js";
import companyTab from "./tabs/company.js";
import ledgersTab from "./tabs/ledgers.js";
import activityTab from "./tabs/activity.js";

// Order here is the order in the rail.
const TABS = [
  universeTab,
  watchlistTab,
  discoverTab,
  companyTab,
  ledgersTab,
  activityTab,
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
control.mount();
settings.mount();
selectTab(location.hash.slice(1));
