from datetime import date

from oracle.analysts.signals import AnalystSignal
from oracle.services import regime_tilt
from oracle.services.regime_service import NO_MOOD, REGIME_AI_OFF, REGIME_OFF
from oracle.store.signals import SignalRepo
from tests.fakes.regime import use_stub
from tests.fakes.tilt_model import TiltModel
from tests.gui.jobs import run_job, wait_job

PARTS = {"momentum": 0.3, "trend": 0.1, "hold": 0.05}


async def watched(ctx, client):
    await run_job(client, "/api/jobs/prices", symbol="AAPL")
    await client.post("/api/watchlists/main/items", json={"symbol": "AAPL"})
    last = ctx.conn.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    SignalRepo(ctx.conn).save([AnalystSignal("risk", "AAPL", date.fromisoformat(last), 0.0, 1.0, "r",
                                             (("size_share", 0.05),))], "x")


async def test_the_regime_account_is_on_by_default_and_listed_with_the_others(loaded):
    body = (await loaded.get("/api/ledgers")).json()
    assert body["regime_account"] == {"enabled": True}
    assert "regime" in [a["name"] for a in body["accounts"]]
    card = (await loaded.get("/api/ledgers/regime")).json()
    assert (card["enabled"], card["state"], card["decisions"]) == (True, None, [])
    assert card["note"].startswith("no reading yet")


async def test_a_regime_step_trades_and_the_card_shows_the_mood_weights_and_contributions(ctx, priced, monkeypatch):
    use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, PARTS)}, flags=frozenset({"clustered"}))
    await watched(ctx, priced)
    job = await run_job(priced, "/api/ledgers/regime/step")
    assert (job["kind"], job["label"], job["subject"], job["state"]) == ("tick", "Regime step", "regime", "done")
    step = job["result"]
    assert (step["account"], step["off"], step["mood"], step["flags"]) == ("regime", None, "calm_up", ["clustered"])
    assert (step["tilt"], step["lag"]) == (None, None)
    [d] = step["decisions"]
    assert (d["symbol"], d["action"]) == ("AAPL", "buy")
    card = (await priced.get("/api/ledgers/regime")).json()
    assert card["note"] is None and card["day"] == step["day"]
    s = card["state"]
    assert (s["mood"], s["flags"], s["warnings"]) == ("calm_up", ["clustered"], {"momentum": ["rebound"]})
    assert set(s["base_weights"]) == set(s["weights"]) and s["health"]["momentum"] == 1.0
    assert s["note"] is None and s["signs"]["momentum"][0]["source"] == "Daniel-Moskowitz 2016"
    [logged] = card["decisions"]
    assert (logged["account"], logged["symbol"], logged["action"]) == ("regime", "AAPL", "buy")
    assert list(logged["explanation"]["parts"]) == ["momentum", "trend", "hold"]
    [log] = (await priced.get("/api/ledgers/decisions", params={"account": "regime"})).json()
    assert log["explanation"]["mood"] == "calm_up"
    regime = next(a for a in (await priced.get("/api/ledgers")).json()["accounts"] if a["name"] == "regime")
    assert [o["symbol"] for o in regime["pending"]] == ["AAPL"]


async def test_with_no_mood_the_step_and_card_say_it_leans_nothing(ctx, priced, monkeypatch):
    use_stub(monkeypatch, mood=None, leans={"AAPL": ("buy", 0.9, PARTS)})
    await watched(ctx, priced)
    step = (await run_job(priced, "/api/ledgers/regime/step"))["result"]
    assert step["mood"] is None and [x["reason"] for x in step["decisions"]] == [NO_MOOD]
    assert (await priced.get("/api/ledgers/regime")).json()["note"] == NO_MOOD


async def test_the_switch_turns_it_off_and_off_it_decides_nothing_and_says_so(ctx, loaded, monkeypatch):
    built = use_stub(monkeypatch)
    assert (await loaded.put("/api/ledgers/regime", json={"enabled": False})).json() == {"enabled": False}
    assert ctx.config.regime_account.enabled is False
    step = (await run_job(loaded, "/api/ledgers/regime/step"))["result"]
    assert step == {"account": "regime", "off": REGIME_OFF, "day": None, "mood": None, "flags": [], "decisions": [],
                    "tilt": None, "lag": None} and built == []
    card = (await loaded.get("/api/ledgers/regime")).json()
    assert (card["enabled"], card["note"]) == (False, REGIME_OFF)
    assert (await loaded.put("/api/ledgers/regime", json={"enabled": True})).json() == {"enabled": True}


async def test_the_settings_form_lists_the_switch_and_it_applies_at_once(ctx, loaded):
    rows = (await loaded.get("/api/settings")).json()["settings"]
    row = next(r for r in rows if (r["section"], r["key"]) == ("regime_account", "enabled"))
    assert (row["value"], row["default"], row["kind"], row["live"]) == (True, True, "bool", True) and row["meaning"]
    await loaded.put("/api/settings", json={"section": "regime_account", "key": "enabled", "value": False})
    assert ctx.config.regime_account.enabled is False


async def test_the_regime_routes_refuse_what_they_do_not_know(loaded):
    assert (await loaded.put("/api/ledgers/regime", json={"enabled": "yes"})).status_code in (400, 422)
    assert (await loaded.get("/api/ledgers/regime", params={"limit": 0})).status_code in (400, 422)



async def test_the_regime_ai_account_is_on_by_default_and_listed_with_its_model(loaded):
    body = (await loaded.get("/api/ledgers")).json()
    assert body["regime_ai_account"] == {"enabled": True, "model": ""}
    assert [a["name"] for a in body["accounts"]][-2:] == ["regime", "regime-ai"]
    card = (await loaded.get("/api/ledgers/regime-ai")).json()
    assert (card["account"], card["enabled"], card["tilt"], card["model"], card["research_profile"]) == (
        "regime-ai", True, None, "", "default")


async def test_a_regime_ai_step_is_a_job_that_tilts_once_and_the_card_shows_the_tilt(ctx, priced, monkeypatch):
    use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, PARTS)})
    model = TiltModel({"momentum": 0.5, "trend": 1.2, "reason": "Rebound: momentum is risky."})
    monkeypatch.setattr(regime_tilt, "tilt_model", lambda c: (model, "fake"))
    await watched(ctx, priced)
    job = await run_job(priced, "/api/ledgers/regime-ai/step")
    assert (job["label"], job["subject"]) == ("Regime-AI step", "regime-ai")
    step = job["result"]
    assert step["account"] == "regime-ai" and step["tilt"]["tilt"] == {"momentum": 0.5, "trend": 1.2}
    assert [d["symbol"] for d in step["decisions"]] == ["AAPL"]
    again = await run_job(priced, "/api/ledgers/regime-ai/step")
    assert again["result"]["tilt"] == step["tilt"] and len(model.asked) == 1
    card = (await priced.get("/api/ledgers/regime-ai")).json()
    assert card["tilt"]["reason"] == "Rebound: momentum is risky." and card["tilt"]["model"] == "fake"
    assert card["state"]["tilt"] == {"momentum": 0.5, "trend": 1.2} and card["tilt"]["rules"]
    assert {d["account"] for d in card["decisions"]} == {"regime-ai"}
    assert (await priced.get("/api/ledgers/regime")).json()["decisions"] == []


async def test_a_failed_step_job_is_retried_as_the_same_lines_step(ctx, loaded, monkeypatch):
    def boom(*_):
        raise RuntimeError("book broke")

    from oracle.services import tick_service

    real = tick_service.regime_step
    monkeypatch.setattr(tick_service, "regime_step", boom)
    job = await run_job(loaded, "/api/ledgers/regime-ai/step")
    assert (job["state"], job["error"]) == ("failed", "book broke")
    monkeypatch.setattr(tick_service, "regime_step", real)
    use_stub(monkeypatch)
    retried = (await loaded.post(f"/api/activity/{job['id']}/retry")).json()
    assert (retried["label"], retried["subject"]) == ("Regime-AI step", "regime-ai")
    assert (await wait_job(loaded, retried))["result"]["account"] == "regime-ai"


async def test_the_regime_ai_switch_and_model_are_set_on_the_card_and_in_settings(ctx, loaded, monkeypatch):
    built = use_stub(monkeypatch)
    put = await loaded.put("/api/ledgers/regime-ai", json={"enabled": False})
    assert put.json() == {"enabled": False, "model": ""} and ctx.config.regime_ai_account.enabled is False
    step = (await run_job(loaded, "/api/ledgers/regime-ai/step"))["result"]
    assert step["off"] == REGIME_AI_OFF and built == []
    assert (await loaded.get("/api/ledgers/regime-ai")).json()["note"] == REGIME_AI_OFF
    assert (await loaded.put("/api/ledgers/regime-ai", json={"model": "default"})).json() == {
        "enabled": False, "model": "default"}
    assert (await loaded.put("/api/ledgers/regime-ai", json={"model": "nope"})).status_code in (400, 404, 422)
    assert ctx.config.regime_ai_account.model == "default"
    rows = (await loaded.get("/api/settings")).json()["settings"]
    keys = {r["key"]: r for r in rows if r["section"] == "regime_ai_account"}
    assert set(keys) == {"enabled", "model"} and all(r["live"] and r["meaning"] for r in keys.values())
    await loaded.put("/api/settings", json={"section": "regime_ai_account", "key": "enabled", "value": True})
    assert ctx.config.regime_ai_account.enabled is True


async def test_the_regime_ai_routes_refuse_what_they_do_not_know(loaded):
    assert (await loaded.put("/api/ledgers/regime-ai", json={"enabled": "yes"})).status_code in (400, 422)
    assert (await loaded.put("/api/ledgers/regime-ai", json={"model": 3})).status_code in (400, 422)
    assert (await loaded.put("/api/ledgers/regime-ai", json={"tilt": 1})).status_code in (400, 422)
