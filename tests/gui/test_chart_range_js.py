import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_research_js import NODE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")


def run(body: str):
    script = f"""
        const m = await import({json.dumps((STATIC_DIR / "lib" / "chart-range.js").as_uri())});
        console.log(JSON.stringify({body}));
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_options_are_1y_2y_5y_and_all_capped_at_the_route_maximum():
    out = run("m.chartRangeOptions(5000)")
    assert out == [["1y", 365, "1y"], ["2y", 730, "2y"], ["5y", 1825, "5y"], ["all", 5000, "All"]]


def test_caption_names_the_picked_range():
    assert run('m.chartRangeCaption("2y")') == "Last 2 years'"
    assert run('m.chartRangeCaption("all")') == "All stored"
    assert run('m.chartRangeCaption("bogus")') == "Last year's"


def test_key_is_found_from_the_day_count_and_defaults_to_1y():
    assert run("m.chartRangeKey(1825, 5000)") == "5y"
    assert run("m.chartRangeKey(5000, 5000)") == "all"
    assert run("m.chartRangeKey(9, 5000)") == "1y"
