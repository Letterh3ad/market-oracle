import { api } from "./api.js";
import { el } from "./dom.js";

// A run's model: "per slot" (each stage on its [models.slots] profile, "default" where unset)
// or one profile for every stage of that run.
function modelPicker() {
  const pick = el("select", { "aria-label": "Model", title: "Model profile for this run: per slot runs each "
    + "stage on the profile Settings > [models.slots] names; a profile runs every stage on it" },
    el("option", { value: "" }, "per slot"));
  api("/api/models").then(({ profiles }) => {
    pick.append(...profiles.map((p) => el("option", { value: p.name }, p.name)));
  }).catch(() => {});
  return pick;
}

// Query parameters for a picked model: none for "per slot".
const modelParams = (pick) => (pick?.value ? { model: pick.value } : {});

export { modelParams, modelPicker };
