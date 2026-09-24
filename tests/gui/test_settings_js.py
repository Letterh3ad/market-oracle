import json
import shutil
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")


def read(row: dict, box: dict) -> dict:
    """What settings.js makes of a row's input box: {"value": ...} or {"refused": message}."""
    script = f"""
        import {{ read }} from {json.dumps((STATIC_DIR / "lib" / "settings.js").as_uri())};
        const row = {json.dumps(row)}, box = {json.dumps(box)};
        let out;
        try {{ out = {{ value: read(row, box) }}; }} catch (err) {{ out = {{ refused: err.message }}; }}
        console.log(JSON.stringify(out));
    """
    done = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


def box(value: str = "", bad: bool = False) -> dict:
    return {"value": value, "checked": False, "validity": {"badInput": bad}}


def test_an_empty_key_box_refuses_rather_than_clearing_the_key():
    assert read({"kind": "str", "secret": True}, box()) == {"refused": "type a key, or use Clear"}


def test_a_typed_key_is_sent():
    assert read({"kind": "str", "secret": True}, box(" abc ")) == {"value": "abc"}


def test_an_empty_plain_box_means_the_default():
    assert read({"kind": "int"}, box()) == {"value": None}


def test_a_number_box_the_browser_cannot_parse_refuses_rather_than_sending_the_default():
    assert read({"kind": "float"}, box(bad=True)) == {"refused": "not a number: type one, or use Default"}


def test_a_number_is_sent_as_a_number():
    assert read({"kind": "float"}, box("2.5")) == {"value": 2.5}
