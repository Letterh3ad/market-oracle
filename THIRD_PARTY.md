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
| `oracle/research/news.py` (RSS parsing) | `wfm/news/sources/feed.py` | Google News items only, outlet from `<source>` |
| `oracle/research/web.py` (HTML to text) | `wfm/news/html.py` | page furniture dropped too, block tags as line breaks, title and meta description kept |
| `oracle/research/ollama.py` | `wfm/news/classify/ollama.py` | chat reply for any schema, client per call, non-JSON handed back, `num_ctx` |

## Runtime dependencies

| Package | License | Use |
|---|---|---|
| httpx | BSD-3-Clause | async HTTP (`oracle/net/client.py`) |
| tzdata | Apache-2.0 | IANA time zones on Windows, for exchange-local bar dates |
| FastAPI | MIT | GUI web API (`oracle/gui`) |
| uvicorn | BSD-3-Clause | serves the GUI (`oracle gui`) |
| Starlette, pydantic, pydantic-core, anyio (via FastAPI) | BSD-3-Clause, MIT, MIT, MIT | FastAPI's own dependencies |
| anthropic 1.8 (Anthropic Python SDK) | MIT | Claude backend for research (`oracle/research/claude.py`); checked 2026-09-23 |
| httpx2, httpcore2, jiter, docstring-parser, truststore (via anthropic) | BSD-3-Clause, BSD-3-Clause, MIT, MIT, MIT | the SDK's own dependencies (the rest were already here) |
| ddgs 9.16 | MIT | web search for research (`oracle/research/web.py`); checked 2026-09-23 |
| primp, click, lxml, colorama (via ddgs; colorama on Windows only) | MIT, BSD-3-Clause, BSD-3-Clause, BSD-3-Clause | ddgs's own dependencies |
| tomlkit 0.15 | MIT | writes settings into `oracle.toml` keeping comments (`oracle/config.py`); checked 2026-09-23 |
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

## Reimplemented ideas

| Idea | From | License | Where |
|---|---|---|---|
| Research roles: fundamental, technical and news analysts, a bull/bear debate, a risk check | TradingAgents (TauricResearch) | Apache-2.0 | `oracle/research/roles.py`; structure only, prompts written fresh, no text copied |

## Test fixtures

| File | From | License | Where |
|---|---|---|---|
| Hand-trimmed 10-Q, 10-K and 8-K main documents | SEC EDGAR filings (Apple Inc.), prose cut and edited | Public filings with the SEC; no copyright claimed on the trimmed structure | `tests/fixtures/edgar/` |
| `aapl-8k-index.htm`: an 8-K's filing index table | SEC EDGAR (sec.gov page, trimmed) | US government work, public domain | `tests/fixtures/edgar/` |
| `citi-10q.txt`: first 200 lines of a 10-Q as text | SEC EDGAR filing (Citigroup Inc., Q2 2026 10-Q) | Public filing with the SEC; short excerpt kept as a test input only | `tests/fixtures/edgar/` |
| `wikipedia-largest-tech-companies.html`: the 2025 table, scripts and styling stripped | Wikipedia, "List of largest technology companies by revenue" (captured 2026-09-24) | CC BY-SA 4.0 (attribution in the file) | `tests/fixtures/list_pages/` |

## Dev dependencies

| Package | License |
|---|---|
| pytest | MIT |
| pytest-asyncio | Apache-2.0 |
