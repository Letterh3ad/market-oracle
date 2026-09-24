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
        console.log(JSON.stringify({{ text: td.children.join(""), cls: td.className }}));
    """
    done = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


def test_the_slots_a_profile_serves_show_unset_ones_marked_and_warn_when_its_key_is_unset():
    default = {"slots": ["notes"], "unset_slots": ["research", "roles"], "key_state": "none"}
    assert slots_cell(default) == {"text": "notes, research (unset), roles (unset)", "cls": "dim small"}
    keyless = {"slots": ["verdict"], "unset_slots": [], "key_state": "unset", "key_env": "MY_KEY"}
    assert slots_cell(keyless) == {"text": "verdict: key unset", "cls": "warn-text small"}
