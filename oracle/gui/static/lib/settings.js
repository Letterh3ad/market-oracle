import { api } from "./api.js";
import { sendJson } from "./ui.js";
import { el, showError } from "./dom.js";
import { offerRestart } from "./control.js";

// Every oracle.toml key, one row each, saved one at a time. The server checks each change
// by loading the whole file, so the page only turns inputs into JSON values.

function pane() {
  return document.getElementById("settings-form");
}

function show(value) {
  if (value === null || value === undefined) return "unset";
  return Array.isArray(value) ? value.join(", ") : String(value);
}

function input(row) {
  if (row.secret) return el("input", { type: "password", placeholder: "new key", autocomplete: "off", size: 24 });
  if (row.kind === "bool") {
    const box = el("input", { type: "checkbox" });
    box.checked = row.value === true;
    return box;
  }
  const number = row.kind === "int" || row.kind === "float";
  const box = el("input", { type: number ? "number" : "text", step: number ? "any" : null, size: 28 });
  box.value = row.value === null ? "" : show(row.value);
  return box;
}

// An empty box means "use the default", sent as null (the key is removed from the file).
// A key is only removed by Clear, and a number the browser could not parse reads as "", so
// both refuse rather than silently drop the key.
function read(row, box) {
  if (row.kind === "bool") return box.checked;
  if (box.validity?.badInput) throw new Error("not a number: type one, or use Default");
  const text = box.value.trim();
  if (!text && row.secret) throw new Error("type a key, or use Clear");
  if (!text) return null;
  if (row.kind === "list") return text.split(",").map((s) => s.trim()).filter(Boolean);
  return row.kind === "int" || row.kind === "float" ? Number(text) : text;
}

function state(row) {
  if (!row.secret) return row.live ? "applies now" : "applies after Restart";
  const where = row.set ? "set in oracle.toml" : "not set";
  return row.from_env ? `${where}; ${row.env} in the environment wins` : where;
}

function settingRow(row) {
  const box = input(row);
  const note = el("span", { class: row.live || row.secret ? "dim small" : "warn-text small" }, state(row));
  const label = `${row.section ? `[${row.section}] ` : ""}${row.key}`;
  const save = async (value) => {
    try {
      const saved = await sendJson("/api/settings", { section: row.section, key: row.key, value }, "PUT");
      tr.replaceWith(settingRow(saved));
      if (!saved.live) offerRestart();
    } catch (err) {
      showError(`${label}: ${err.message}`);
    }
  };
  const saveBox = () => {
    let value;
    try { value = read(row, box); } catch (err) { showError(`${label}: ${err.message}`); return; }
    save(value);
  };
  const tr = el("tr", null,
    el("td", { class: "mono" }, row.key),
    el("td", null, box),
    el("td", null,
      el("button", { type: "button", class: "small-btn", onclick: saveBox }, "Save"),
      el("button", { type: "button", class: "small-btn", title: row.secret ? "Remove the key from oracle.toml" :
        "Remove the key from oracle.toml so the default applies", onclick: () => save(null) },
      row.secret ? "Clear" : "Default")),
    el("td", { class: "dim small mono" }, row.secret ? "" : show(row.default)),
    el("td", null, note),
    el("td", { class: "dim small" }, row.meaning));
  return tr;
}

function sectionTable(title, rows) {
  return el("div", null, el("h3", null, title),
    el("table", { class: "small" },
      el("thead", null, el("tr", null, ...["key", "value", "", "default", "", "meaning"].map((h) => el("th", null, h)))),
      el("tbody", null, ...rows.map(settingRow))));
}

let loaded = false;

async function load() {
  let rows;
  try {
    rows = (await api("/api/settings")).settings;
  } catch (err) {
    pane().replaceChildren(el("span", { class: "down" }, `settings: ${err.message}`));
    return;
  }
  loaded = true;
  const sections = [...new Set(rows.map((r) => r.section))];
  const tables = sections.map((s) => [s, sectionTable(s ? `[${s}]` : "top level", rows.filter((r) => r.section === s))]);
  const pacing = tables.filter(([s]) => s.startsWith("sources."));
  pane().replaceChildren(
    el("p", { class: "dim small" }, "Saved to oracle.toml with its comments kept. Settings marked " +
      "\"applies after Restart\" take effect once Oracle restarts; API keys are never shown."),
    ...tables.filter(([s]) => !s.startsWith("sources.")).map(([, t]) => t),
    el("details", null, el("summary", { class: "dim" }, "Request pacing per source"), ...pacing.map(([, t]) => t)));
}

// Loads when first opened, so the page does not fetch every key at start; a failed load
// tries again on the next opening.
function mount() {
  const details = pane().closest("details");
  details.addEventListener("toggle", () => { if (details.open && !loaded) load(); });
}

export { mount, read };
