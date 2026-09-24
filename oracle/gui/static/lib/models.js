import { api } from "./api.js";
import { sendJson } from "./ui.js";
import { el, showError } from "./dom.js";

// Settings > Models: named model profiles, each added, edited, deleted and tested here.
// An edit replaces the whole profile; the key is never sent to the page, so an empty key
// box keeps the saved one.

const FIELDS = ["model", "base_url", "context", "timeout_s", "key_env", "key"];
const PROVIDER_FIELDS = ["base_url", "context"];
// What an edit form shows for a provider the server no longer offers (claude_code turned off).
const OFF = { base_url: null, context: "", timeout_s: "", keyed: false, url: false };

// Form values (strings) to the JSON the server takes: empty means the provider's default.
function profileBody(fields, create, defaults = {}) {
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
  if (defaults.base_url_required && !body.base_url) throw new Error("type a base_url: the server's API root");
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

// The provider-bound boxes after a switch to `provider`: empty, since another provider's
// endpoint or window would break it and its key must not reach this one's host; the
// profile's own values again when switched back to its provider.
function switchedValues(p, provider) {
  const own = p?.provider === provider ? prefill(p) : {};
  return Object.fromEntries([...PROVIDER_FIELDS, "key_env", "key"].map((k) => [k, own[k] ?? ""]));
}

function placeholders(defaults) {
  const base = defaults.base_url_required ? "required, like http://localhost:1234/v1"
    : `default: ${defaults.base_url ?? "provider's own endpoint"}`;
  return { base_url: base, context: `default: ${defaults.context}`, timeout_s: `default: ${defaults.timeout_s}` };
}

function keyState(p) {
  if (p.key_state === "none") return "no key";
  if (p.key_state === "fallback") return p.key_fallback;
  return p.key_env ? `${p.key_state} (${p.key_env})` : p.key_state;
}

// The slots a profile serves, those it serves by being unset marked so; a warning when its
// key is missing, saying what then happens to each.
function slotsCell(p) {
  const text = [...p.slots, ...p.unset_slots.map((s) => `${s} (unset)`)].join(", ");
  if (!p.if_unset?.length) return el("td", { class: "dim small" }, text);
  return el("td", { class: "warn-text small", title: `${p.unavailable}: ${p.if_unset.join("; ")}` },
    `${text}: ${p.key_state === "unset" ? "key unset" : "off"}`);
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
      el("thead", null, el("tr", null, ...["name", "provider", "model", "context", "timeout", "key", "slots", "", ""]
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
    el("td", { class: "mono" }, p.name, p.from_llm ? el("span", { class: "dim" }, " [llm]") : null,
      p.unusable ? el("span", { class: "warn-text small", title: p.unusable }, " unusable: edit to fix") : null),
    el("td", null, p.provider, p.off ? el("span", { class: "warn-text", title: "set allow_claude_code = true " +
      "under [models] in oracle.toml to use it" }, " (off)") : null),
    el("td", { class: "mono" }, p.model),
    el("td", null, String(p.context)),
    el("td", null, `${p.timeout_s}s`),
    el("td", null, keyState(p)),
    slotsCell(p),
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
  // A profile whose provider is off is still editable; only a switch to another is saved.
  const names = Object.keys(providers);
  const provider = el("select", null, ...names.map((v) => el("option", { value: v }, v)),
    p && !providers[p.provider] ? el("option", { value: p.provider, disabled: true }, `${p.provider} (off)`) : null);
  provider.value = p?.provider ?? names[0];
  const inputs = Object.fromEntries(FIELDS.map((k) => [k, box(k, k === "key" ? { type: "password" } : {})]));
  const clear = el("input", { type: "checkbox", title: "Remove the saved key" });
  const keyCell = el("td", null, inputs.key_env, inputs.key, el("label", { class: "small" }, clear, " clear"));
  const hint = el("td", { class: "dim small" });
  const fit = () => {
    const defaults = providers[provider.value] ?? OFF;
    for (const [k, text] of Object.entries(placeholders(defaults))) inputs[k].placeholder = text;
    inputs.key_env.placeholder = "key_env";
    inputs.key.placeholder = p?.key_state === "set" && !p.key_env && provider.value === p.provider
      ? "saved key kept" : "key";
    keyCell.hidden = !defaults.keyed;
    hint.textContent = defaults.base_url_required ? "base_url required; other empty boxes: the provider's default"
      : "empty: the provider's default";
    inputs.base_url.hidden = !defaults.url;
  };
  provider.addEventListener("change", () => {
    for (const [k, v] of Object.entries(switchedValues(p, provider.value))) inputs[k].value = v;
    fit();
  });
  fit();
  const save = async () => {
    let body;
    try {
      body = profileBody({ name: name?.value, provider: provider.value, clear_key: clear.checked,
        ...Object.fromEntries(FIELDS.map((k) => [k, inputs[k].value])) }, !p, providers[provider.value]);
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
    el("td"),
    el("td", null,
      el("button", { type: "button", class: "small-btn", onclick: save }, "Save"),
      el("button", { type: "button", class: "small-btn", onclick: load }, "Cancel")),
    hint);
}

async function load() {
  try {
    draw(await api("/api/models"));
  } catch (err) {
    pane().replaceChildren(el("span", { class: "down" }, `models: ${err.message}`));
  }
}

export { load, placeholders, prefill, profileBody, slotsCell, switchedValues };
