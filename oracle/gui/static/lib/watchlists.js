// The watchlist pickers: every list, plus `current` even before anything is on it.
import { el } from "./dom.js";

function fillPicker(select, lists, current) {
  const names = lists.map((w) => w.name);
  if (!names.some((n) => n.toLowerCase() === current.toLowerCase())) names.unshift(current);
  select.replaceChildren(...names.map((n) => el("option", { value: n, selected: n === current ? "" : null }, n)));
}

export { fillPicker };
