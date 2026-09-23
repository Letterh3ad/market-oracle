import { el } from "../lib/dom.js";

// Stands in for a tab until its ticket lands.
function placeholder(id, label) {
  return {
    id,
    label,
    render(pane) {
      pane.replaceChildren(el("p", { class: "dim" }, `${label}: coming later in phase 6.`));
    },
  };
}

export { placeholder };
