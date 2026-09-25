import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.cli.test_holiday import BAD, GOOD
from tests.gui.test_backtest_js import NODE, PRELUDE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")


def run(body: str, arm=BAD):
    script = f"""
        {PRELUDE}
        ANSWERS = {{
          "GET /api/holiday": {json.dumps(GOOD.to_json())},
          "POST /api/holiday/arm": {json.dumps(arm.to_json())},
          "POST /api/holiday/disarm": {json.dumps(GOOD.to_json())},
        }};
        const {{ holidayCard }} = await import({json.dumps((STATIC_DIR / "lib" / "holiday.js").as_uri())});
        const node = holidayCard().node;
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_status_asks_without_confirming_and_shows_each_check():
    out = run('button(node, "Status").fire("click"); await settle(); '
              "console.log(JSON.stringify({asked, text: node.textContent}));")
    assert out["asked"] == ["GET /api/holiday"]
    assert "okAppanswers" in out["text"] and "Registered task X." in out["text"]
    assert "Warning: Automatic sign-in is off." in out["text"]


def test_arm_asks_first_then_shows_the_refusal_with_its_fix():
    out = run('button(node, "Arm").fire("click"); await settle(); const before = [...asked]; '
              'const asking = node.textContent; button(node, "Arm now").fire("click"); await settle(); '
              "console.log(JSON.stringify({before, asked, asking, text: node.textContent}));")
    assert out["before"] == [] and "back up the database" in out["asking"]
    assert out["asked"] == ["POST /api/holiday/arm"]
    assert "FAILSleepsleeps after 30 minfix: set Never" in out["text"] and "Not armed" in out["text"]


def test_cancel_leaves_everything_alone():
    out = run('button(node, "Disarm").fire("click"); await settle(); button(node, "Cancel").fire("click"); '
              'await settle(); console.log(JSON.stringify({asked, back: !!button(node, "Disarm")}));')
    assert out == {"asked": [], "back": True}
