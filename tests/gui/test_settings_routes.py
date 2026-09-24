async def test_the_form_lists_every_key_and_a_save_applies_live_keys(ctx, client, tmp_path):
    rows = (await client.get("/api/settings")).json()["settings"]
    assert {"section": "schedule", "key": "time"}.items() <= next(r for r in rows if r["key"] == "time").items()
    saved = (await client.put("/api/settings", json={"section": "schedule", "key": "time", "value": "20:45"})).json()
    assert (saved["value"], saved["live"]) == ("20:45", True) and ctx.config.schedule.time == "20:45"
    assert 'time = "20:45"' in (tmp_path / "oracle.toml").read_text(encoding="utf-8")


async def test_a_bad_value_is_refused_with_the_loaders_message(client, tmp_path):
    response = await client.put("/api/settings", json={"section": "gui", "key": "port", "value": 80})
    assert response.status_code == 400 and "gui.port must be a whole number from 1024 to 65535" in response.json()["error"]
    assert not (tmp_path / "oracle.toml").exists()


async def test_a_secret_is_saved_but_never_sent_back(client, tmp_path):
    body = await client.put("/api/settings", json={"section": "keys", "key": "eodhd", "value": "s3cret"})
    assert body.status_code == 200 and body.json()["set"] is True
    listing = await client.get("/api/settings")
    assert "s3cret" not in body.text + listing.text
    assert 's3cret' in (tmp_path / "oracle.toml").read_text(encoding="utf-8")
