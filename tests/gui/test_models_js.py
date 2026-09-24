import json
import shutil
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")


def body(fields: dict, create: bool = False) -> dict:
    """What models.js sends for a profile form: {"value": body} or {"refused": message}."""
    script = f"""
        import {{ profileBody }} from {json.dumps((STATIC_DIR / "lib" / "models.js").as_uri())};
        let out;
        try {{ out = {{ value: profileBody({json.dumps(fields)}, {json.dumps(create)}) }}; }}
        catch (err) {{ out = {{ refused: err.message }}; }}
        console.log(JSON.stringify(out));
    """
    done = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


FORM = {"provider": "ollama", "model": " qwen3:8b ", "base_url": "", "context": "8192", "timeout_s": "",
        "key_env": "", "key": "", "clear_key": False}


def test_empty_boxes_mean_the_default_and_numbers_are_numbers():
    assert body(FORM) == {"value": {"provider": "ollama", "model": "qwen3:8b", "base_url": None, "context": 8192,
                                    "timeout_s": None, "key_env": None, "key": None}}


def test_an_empty_key_box_keeps_the_saved_key_and_clear_removes_it():
    assert body(FORM)["value"]["key"] is None
    assert body({**FORM, "clear_key": True})["value"]["key"] == ""
    assert body({**FORM, "key": " sk-1 "})["value"]["key"] == "sk-1"


def test_a_new_profile_needs_a_name_and_every_profile_a_model():
    assert body(FORM, create=True) == {"refused": "type a profile name"}
    assert body({**FORM, "name": "big"}, create=True)["value"]["name"] == "big"
    assert body({**FORM, "model": " "}) == {"refused": "type a model name"}


def prefill(profile) -> dict:
    script = f"""
        import {{ prefill }} from {json.dumps((STATIC_DIR / "lib" / "models.js").as_uri())};
        console.log(JSON.stringify(prefill({json.dumps(profile)})));
    """
    done = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


def test_an_edit_form_fills_only_what_the_profile_sets_never_the_providers_defaults():
    row = {"model": "qwen3:8b", "base_url": "http://localhost:11434", "context": 16384, "timeout_s": 180.0,
           "key_env": None, "set": {"base_url": None, "context": 8192, "timeout_s": None}}
    assert prefill(row) == {"model": "qwen3:8b", "base_url": "", "context": "8192", "timeout_s": "", "key_env": ""}
    assert prefill(None) == {"model": "", "base_url": "", "context": "", "timeout_s": "", "key_env": ""}


def placeholders(defaults) -> dict:
    script = f"""
        import {{ placeholders }} from {json.dumps((STATIC_DIR / "lib" / "models.js").as_uri())};
        console.log(JSON.stringify(placeholders({json.dumps(defaults)})));
    """
    done = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


def test_a_provider_without_an_endpoint_of_its_own_asks_for_one():
    own = {"base_url": None, "base_url_required": False, "context": 200000, "timeout_s": 180.0}
    assert placeholders(own) == {"base_url": "default: provider's own endpoint", "context": "default: 200000",
                                 "timeout_s": "default: 180"}
    assert placeholders({**own, "base_url_required": True})["base_url"] == "required, like http://localhost:1234/v1"


def test_a_provider_needing_a_base_url_refuses_an_empty_one_before_sending():
    script = f"""
        import {{ profileBody }} from {json.dumps((STATIC_DIR / "lib" / "models.js").as_uri())};
        const needs = {{ base_url_required: true }};
        let refused;
        try {{ profileBody({json.dumps(FORM)}, false, needs); }} catch (err) {{ refused = err.message; }}
        const ok = profileBody({json.dumps({**FORM, "base_url": "http://localhost:1234/v1"})}, false, needs);
        console.log(JSON.stringify({{ refused, base_url: ok.base_url }}));
    """
    done = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    assert json.loads(done.stdout) == {"refused": "type a base_url: the server's API root",
                                       "base_url": "http://localhost:1234/v1"}


def switched(profile, provider) -> dict:
    script = f"""
        import {{ switchedValues }} from {json.dumps((STATIC_DIR / "lib" / "models.js").as_uri())};
        console.log(JSON.stringify(switchedValues({json.dumps(profile)}, {json.dumps(provider)})));
    """
    done = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


def test_switching_provider_clears_its_values_and_switching_back_restores_them():
    row = {"provider": "claude_api", "model": "m", "key_env": "MY_KEY",
           "set": {"base_url": "https://proxy.example/v1", "context": 100000, "timeout_s": None}}
    assert switched(row, "openai_compatible") == {"base_url": "", "context": "", "key_env": "", "key": ""}
    assert switched(row, "claude_api") == {"base_url": "https://proxy.example/v1", "context": "100000",
                                           "key_env": "MY_KEY", "key": ""}
    assert switched(None, "ollama") == {"base_url": "", "context": "", "key_env": "", "key": ""}


def test_bad_input_is_refused_before_sending():
    assert body({**FORM, "context": "lots"}) == {"refused": "context: not a number"}
    assert body({**FORM, "key": "k", "key_env": "K"}) == {"refused": "give a key or key_env, not both"}


def slots_cell(profile: dict) -> dict:
    script = f"""
        globalThis.document = {{ createElement: (tag) => ({{ tag, attrs: {{}}, children: [],
          setAttribute(k, v) {{ this.attrs[k] = v; }}, append(...n) {{ this.children.push(...n); }} }}),
          createTextNode: (text) => text }};
        const {{ slotsCell }} = await import({json.dumps((STATIC_DIR / "lib" / "models.js").as_uri())});
        const td = slotsCell({json.dumps(profile)});
        console.log(JSON.stringify({{ text: td.children.join(""), cls: td.className, title: td.attrs.title }}));
    """
    done = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


def test_the_slots_a_profile_serves_show_unset_ones_marked_and_warn_when_its_key_is_unset():
    default = {"slots": ["notes"], "unset_slots": ["research", "roles"], "key_state": "none"}
    assert slots_cell(default) == {"text": "notes, research (unset), roles (unset)", "cls": "dim small"}
    keyless = {"slots": ["verdict"], "unset_slots": [], "key_state": "unset", "unavailable": "MY_KEY is not set",
               "if_unset": ["verdict runs on the research slot's profile"]}
    assert slots_cell(keyless) == {"text": "verdict: key unset", "cls": "warn-text small",
                                   "title": "MY_KEY is not set: verdict runs on the research slot's profile"}
