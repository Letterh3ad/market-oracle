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


def test_bad_input_is_refused_before_sending():
    assert body({**FORM, "context": "lots"}) == {"refused": "context: not a number"}
    assert body({**FORM, "key": "k", "key_env": "K"}) == {"refused": "give a key or key_env, not both"}
