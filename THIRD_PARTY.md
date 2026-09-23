# Third-party and borrowed code

Every dependency, copied file, and reimplemented idea, with its license checked at intake.

## Copied from Warframe Market Stalker (own code, `../Warframe-market-stalker`)

| Here | From | Changes |
|---|---|---|
| `oracle/clock.py` | `wfm/clock.py` | none |
| `tests/fakes/clock.py`, `tests/test_clock.py` | same paths | import paths |
| `oracle/store/db.py` | `wfm/store/db.py` | savepoint prefix |
| `oracle/store/migrate.py` | `wfm/store/migrate.py` | `SchemaTooNew` type |
| `oracle/store/migrations/run_ddl` | `wfm/store/migrations/m0001_initial.up` | extracted as a shared helper |
| `tests/store/test_db.py`, `test_migrate.py` | same paths | rewritten against `MetaRepo` and synthetic migrations |
| `tests/test_architecture.py` | same path | allowlist instead of denylist |
| `oracle/gui/app.py`, `deps.py` | `wfm/gui/app.py`, `deps.py` | one router, error handlers from `oracle/gui/errors.py` |
| `oracle/gui/static/lib/api.js` | `wfm/gui/static/lib/api.js` | none |
| `oracle/gui/static/lib/dom.js` | `wfm/gui/static/lib/dom.js` | `humanAge` dropped |

## Runtime dependencies

| Package | License | Use |
|---|---|---|
| httpx | BSD-3-Clause | async HTTP (`oracle/net/client.py`) |
| tzdata | Apache-2.0 | IANA time zones on Windows, for exchange-local bar dates |
| FastAPI | MIT | GUI web API (`oracle/gui`) |
| uvicorn | BSD-3-Clause | serves the GUI (`oracle gui`) |
| Starlette, pydantic, pydantic-core, anyio (via FastAPI) | BSD-3-Clause, MIT, MIT, MIT | FastAPI's own dependencies |
| TradingView Lightweight Charts 4.2.3 (vendored file, licence header kept) | Apache-2.0 | price chart (`oracle/gui/static/vendor/`) |

## Data

| Dataset | License | Use |
|---|---|---|
| [FinanceDatabase](https://github.com/JerBouma/FinanceDatabase) (compressed CSVs, fetched at a pinned commit) | MIT | offline universe: names, classification, exchanges (`oracle/universe/financedatabase.py`) |

## Services

Called over HTTP, not redistributed.

| Service | Terms | Use |
|---|---|---|
| Yahoo Finance chart and quoteSummary APIs (unofficial) | Yahoo terms of service; personal, non-commercial use | daily bars, FX and fundamentals (`oracle/market/yahoo.py`, `fx.py`, `fundamentals.py`) |
| EODHD (paid, optional) | eodhd.com terms; enabled only with the user's key | fallback daily bars, FX and fundamentals (`oracle/market/eodhd.py`, `fx.py`, `fundamentals.py`) |
| ECB data portal (SDMX API) | ECB reuse policy: free reuse with the source cited | reference FX rates (`oracle/market/fx.py`) |
| Frankfurter (api.frankfurter.app) | Free, open source (MIT), serves ECB data | FX backup (`oracle/market/fx.py`) |
| ExchangeRate-API open access (open.er-api.com) | Free tier; attribution required: "Rates By Exchange Rate API" (https://www.exchangerate-api.com), shown wherever its rates are displayed | today's FX for currencies others lack (`oracle/market/fx.py`) |

## Dev dependencies

| Package | License |
|---|---|
| pytest | MIT |
| pytest-asyncio | Apache-2.0 |
