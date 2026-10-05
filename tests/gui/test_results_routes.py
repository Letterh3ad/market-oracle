"""The Research tab's results table route (research-lab 06): the CLI's JSON, with its filters."""

import json

from oracle.services import results_service as results
from tests.fakes.results import build


async def test_the_table_is_the_clis_json_and_filters(ctx, client, monkeypatch):
    build(ctx, monkeypatch)
    got = (await client.get("/api/lab/table")).json()
    assert got == json.loads(json.dumps(results.table_json(results.results_table(ctx))))
    narrow = (await client.get("/api/lab/table", params={"stage": "funnel", "verdict": "fail"})).json()
    assert [r["ref"] for r in narrow["rows"]] == ["T-13", "X-01", "X-11", None]
    assert narrow["filters"] == {"family": None, "stage": "funnel", "verdict": "fail"}
    family = (await client.get("/api/lab/table", params={"family": "1. LLM and NLP on text", "stage": ""})).json()
    assert [r["ref"] for r in family["rows"]] == ["A-01", "A-02"]
    bad = await client.get("/api/lab/table", params={"stage": "nowhere"})
    assert bad.status_code == 400 and "stage is one of" in bad.json()["error"]
