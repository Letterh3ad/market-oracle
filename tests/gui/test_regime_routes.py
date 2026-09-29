from datetime import date

from oracle.analysts.signals import AnalystSignal
from oracle.services.regime_service import NO_MOOD, REGIME_OFF
from oracle.store.signals import SignalRepo
from tests.fakes.regime import use_stub
from tests.gui.jobs import run_job

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
    assert card["note"].startswith("no regime reading yet")


async def test_a_regime_step_trades_and_the_card_shows_the_mood_weights_and_contributions(ctx, priced, monkeypatch):
    use_stub(monkeypatch, leans={"AAPL": ("buy", 0.9, PARTS)}, flags=frozenset({"clustered"}))
    await watched(ctx, priced)
    step = (await priced.post("/api/ledgers/regime/step")).json()
    assert (step["off"], step["mood"], step["flags"]) == (None, "calm_up", ["clustered"])
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
    step = (await priced.post("/api/ledgers/regime/step")).json()
    assert step["mood"] is None and [x["reason"] for x in step["decisions"]] == [NO_MOOD]
    assert (await priced.get("/api/ledgers/regime")).json()["note"] == NO_MOOD


async def test_the_switch_turns_it_off_and_off_it_decides_nothing_and_says_so(ctx, loaded, monkeypatch):
    built = use_stub(monkeypatch)
    assert (await loaded.put("/api/ledgers/regime", json={"enabled": False})).json() == {"enabled": False}
    assert ctx.config.regime_account.enabled is False
    step = (await loaded.post("/api/ledgers/regime/step")).json()
    assert step == {"off": REGIME_OFF, "day": None, "mood": None, "flags": [], "decisions": []} and built == []
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
