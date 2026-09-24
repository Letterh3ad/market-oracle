import { api } from "./api.js";
import { sendJson } from "./ui.js";
import { el, showError } from "./dom.js";

// Settings > Models: named model profiles, each added, edited, deleted and tested here.
// An edit replaces the whole profile; the key is never sent to the page, so an empty key
// box keeps the saved one.

const FIELDS = ["model", "base_url", "context", "timeout_s", "key_env", "key"];
const PROVIDER_FIELDS = ["base_url", "context"];

// Form values (strings) to the JSON the server takes: empty means the provider's default.
function profileBody(fields, create) {
  const text = (v) => String(v ?? "").trim() || null;
  const number = (v, label) => {
    const t = text(v);
    if (t === null) return null;
    const n = Number(t);
    if (!Number.isFinite(n)) throw new Error(`${label}: not a number`);
    return n;
  };
  const body = {
    provider: fields.provider, model: text(fields.model), base_url: text(fields.base_url),
    context: number(fields.context, "context"), timeout_s: number(fields.timeout_s, "timeout_s"),
    key_env: text(fields.key_env), key: fields.clear_key ? "" : text(fields.key),
  };
  if (!body.model) throw new Error("type a model name");
  if (body.key && body.key_env) throw new Error("give a key or key_env, not both");
  if (create) {
    body.name = text(fields.name);
    if (!body.name) throw new Error("type a profile name");
  }
  return body;
}

// An edit form's boxes: only what the profile sets, so a default never gets written as a
// value (and carried to another provider). Defaults show as placeholders instead.
function prefill(p) {
  if (!p) return { model: "", base_url: "", context: "", timeout_s: "", key_env: "" };
  const set = (k) => (p.set[k] ?? "") === "" ? "" : String(p.set[k]);
  return { model: p.model, base_url: set("base_url"), context: set("context"), timeout_s: set("timeout_s"),
    key_env: p.key_env ?? "" };
}

function placeholders(defaults) {
  return { base_url: defaults.base_url ?? "provider's own endpoint", context: String(defaults.context),
    timeout_s: String(defaults.timeout_s) };
}

function keyState(p) {
  if (p.key_state === "none") return "no key";
  if (p.key_state === "fallback") return p.key_fallback;
  return p.key_env ? `${p.key_state} (${p.key_env})` : p.key_state;
}

function pane() {
  return document.getElementById("models-form");
}

function draw(listing) {
  const body = el("tbody", null, ...listing.profiles.map((p) => profileRow(p, listing.providers)));
  pane().replaceChildren(
    el("h3", null, "Models"),
    el("p", { class: "dim small" }, "Named models a task can run on. The default comes from [llm] until it is " +
      "edited here, which writes [models.default]. Keys are never shown."),
    el("table", { class: "small" },
      el("thead", null, el("tr", null, ...["name", "provider", "model", "context", "timeout", "key", "", ""]
        .map((h) => el("th", null, h)))),
      body),
    el("button", { type: "button", class: "small-btn", onclick: () => body.append(formRow(null, listing.providers)) },
      "Add profile"));
}

function profileRow(p, providers) {
  const result = el("span", { class: "small" });
  const test = el("button", { type: "button", class: "small-btn", onclick: async () => {
    test.disabled = true;
    result.className = "dim small";
    result.textContent = "testing...";
    try {
      const r = await api(`/api/models/${encodeURIComponent(p.name)}/test`, { method: "POST" });
      result.className = r.ok ? "up small" : "down small";
      result.textContent = r.ok ? `ok in ${r.seconds.toFixed(1)}s` : `failed after ${r.seconds.toFixed(1)}s: ${r.error}`;
    } catch (err) {
      // A job holding the model answers 409 with why; shown here, not as a page error.
      result.className = "warn-text small";
      result.textContent = err.message;
    } finally {
      test.disabled = false;
    }
  } }, "Test");
  const tr = el("tr", null,
    el("td", { class: "mono" }, p.name, p.from_llm ? el("span", { class: "dim" }, " [llm]") : null),
    el("td", null, p.provider),
    el("td", { class: "mono" }, p.model),
    el("td", null, String(p.context)),
    el("td", null, `${p.timeout_s}s`),
    el("td", null, keyState(p)),
    el("td", null,
      test,
      el("button", { type: "button", class: "small-btn", onclick: () => tr.replaceWith(formRow(p, providers)) }, "Edit"),
      p.name === "default" ? null : el("button", { type: "button", class: "small-btn", onclick: () => remove(p.name) },
        "Delete")),
    el("td", null, result));
  return tr;
}

async function remove(name) {
  if (!confirm(`Delete the model profile ${name}?`)) return;
  try {
    draw(await api(`/api/models/${encodeURIComponent(name)}`, { method: "DELETE" }));
  } catch (err) {
    showError(`${name}: ${err.message}`);
  }
}

// One row of inputs: a new profile when `p` is null, else an edit of `p`. `providers` maps
// each provider to its defaults.
function formRow(p, providers) {
  const values = prefill(p);
  const box = (key, attrs = {}) => {
    const input = el("input", { type: "text", size: 14, placeholder: key, autocomplete: "off", ...attrs });
    input.value = values[key] ?? "";
    return input;
  };
  const name = p ? null : box("name", { size: 10 });
  const provider = el("select", null, ...Object.keys(providers).map((v) => el("option", { value: v }, v)));
  provider.value = p?.provider ?? Object.keys(providers)[0];
  const inputs = Object.fromEntries(FIELDS.map((k) => [k, box(k, k === "key" ? { type: "password" } : {})]));
  const clear = el("input", { type: "checkbox", title: "Remove the saved key" });
  const keyCell = el("td", null, inputs.key_env, inputs.key, el("label", { class: "small" }, clear, " clear"));
  const fit = () => {
    const defaults = providers[provider.value];
    for (const [k, text] of Object.entries(placeholders(defaults))) inputs[k].placeholder = `default: ${text}`;
    inputs.key_env.placeholder = "key_env";
    inputs.key.placeholder = p?.key_state === "set" && !p.key_env && provider.value === p.provider
      ? "saved key kept" : "key";
    keyCell.hidden = !defaults.keyed;
  };
  provider.addEventListener("change", () => {
    // Another provider's endpoint or window would break this one; its own defaults apply.
    for (const k of PROVIDER_FIELDS) inputs[k].value = "";
    fit();
  });
  fit();
  const save = async () => {
    let body;
    try {
      body = profileBody({ name: name?.value, provider: provider.value, clear_key: clear.checked,
        ...Object.fromEntries(FIELDS.map((k) => [k, inputs[k].value])) }, !p);
    } catch (err) { showError(err.message); return; }
    try {
      draw(await (p ? sendJson(`/api/models/${encodeURIComponent(p.name)}`, body, "PUT") : sendJson("/api/models", body)));
    } catch (err) {
      showError(`${p?.name ?? body.name}: ${err.message}`);
    }
  };
  return el("tr", null,
    el("td", null, name ?? el("span", { class: "mono" }, p.name)),
    el("td", null, provider),
    el("td", null, inputs.model, inputs.base_url),
    el("td", null, inputs.context),
    el("td", null, inputs.timeout_s),
    keyCell,
    el("td", null,
      el("button", { type: "button", class: "small-btn", onclick: save }, "Save"),
      el("button", { type: "button", class: "small-btn", onclick: load }, "Cancel")),
    el("td", { class: "dim small" }, "empty: the provider's default"));
}

async function load() {
  try {
    draw(await api("/api/models"));
  } catch (err) {
    pane().replaceChildren(el("span", { class: "down" }, `models: ${err.message}`));
  }
}

export { load, prefill, profileBody };
