async def test_scheduling_is_off_until_turned_on_and_then_shows_the_next_run(ctx, client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    schedule = (await client.get("/api/status")).json()["schedule"]
    assert (schedule["enabled"], schedule["time"], schedule["next_run"], schedule["watched"]) == (
        False, "22:30", None, 0)
    body = (await client.put("/api/settings/schedule", json={"enabled": True, "time": "21:00"})).json()
    assert (body["enabled"], body["time"]) == (True, "21:00") and body["next_run"]
    assert (await client.get("/api/status")).json()["schedule"] == body
    assert 'time = "21:00"' in (tmp_path / "oracle.toml").read_text(encoding="utf-8")


async def test_a_bad_time_is_refused(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    response = await client.put("/api/settings/schedule", json={"enabled": True, "time": "9pm"})
    assert response.status_code == 400 and "22:30" in response.json()["error"]
