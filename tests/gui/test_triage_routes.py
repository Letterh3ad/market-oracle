"""The Research tab's funnel stage routes (research-lab 05)."""

from datetime import datetime

from oracle.services import triage_service as triage
from tests.fakes.osap import write_release
from tests.lab.test_triage_service import KEPT


async def test_import_map_triage_power_and_the_table(client, tmp_path, monkeypatch):
    monkeypatch.setattr(triage, "observed_windows", lambda c: KEPT)
    folder = write_release(tmp_path / "osap")
    done = (await client.post("/api/lab/osap/import", json={"folder": str(folder)})).json()
    assert [i["what"] for i in done["imports"]] == ["doc", "op", "large"]
    status = (await client.get("/api/lab/osap")).json()
    assert status["signals"]["Predictor"] == 4 and status["release"] == triage.RELEASE
    assert (await client.put("/api/lab/methods/T-07/osap", json={"signal": "gp"})).json() == {"ref": "T-07",
                                                                                               "signal": "GP"}
    bad = await client.put("/api/lab/methods/T-07/osap", json={"signal": "nope"})
    assert bad.status_code == 400 and "not in OSAP" in bad.json()["error"]
    assert (await client.post("/api/lab/methods/T-07/triage", json={})).status_code == 400
    await client.post("/api/lab/methods/T-07/mark", json={"how": "go"})
    ran = (await client.post("/api/lab/methods/T-07/triage", json={})).json()
    assert ran["outcome"] == "pass" and ran["status_to"] == "power" and ran["by"] == "user"
    power = (await client.post("/api/lab/methods/T-07/power", json={"horizon": 20, "expected": 0.08})).json()
    assert power["outcome"] == "pass" and power["detail"]["expected_ic"] == 0.08
    table = (await client.get("/api/lab/table")).json()
    row = next(r for r in table["rows"] if r["ref"] == "T-07")
    assert row["signal"] == "GP" and row["triage"]["outcome"] == "pass" and row["power"]["outcome"] == "pass"
    assert table["triage_t"] == triage.TRIAGE_T


async def test_the_download_job_is_refused_outside_the_compute_window(client, monkeypatch):
    monkeypatch.setattr(triage, "local_now", lambda c: datetime(2026, 10, 4, 9, 0))
    refused = await client.post("/api/jobs/osap-fetch")
    assert refused.status_code == 400 and "compute window" in refused.json()["error"]
