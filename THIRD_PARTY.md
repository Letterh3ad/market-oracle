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
| laya 0.3.22 (optional, `pip install -e .[laya]`) | Apache-2.0 | scores headlines per company (`oracle/research/laya.py`); checked 2026-09-30 |
| torch 2.11 (cu128 build from the PyTorch index), transformers, safetensors, huggingface_hub, tokenizers, hf-xet, numpy (via laya) | BSD-3-Clause, Apache-2.0, Apache-2.0, Apache-2.0, Apache-2.0, Apache-2.0, BSD-3-Clause and bundled permissive | laya's own dependencies; checked 2026-09-30 |
| typing_extensions, packaging, setuptools (via torch) | PSF-2.0, Apache-2.0 OR BSD-2-Clause, MIT | torch's own dependencies; checked 2026-09-30 |
| NVIDIA CUDA runtime DLLs (inside the cu128 torch wheel) | NVIDIA CUDA EULA; installed locally, not redistributed | GPU scoring; checked 2026-09-30 |
| regex, PyYAML, tqdm, sympy, mpmath, networkx, Jinja2, MarkupSafe, filelock, fsspec, typer, rich, shellingham, markdown-it-py, mdurl (via torch, transformers, huggingface_hub) | Apache-2.0 AND CNRI-Python, MIT, MPL-2.0 AND MIT, BSD, BSD, BSD-3-Clause, BSD-3-Clause, BSD-3-Clause, MIT, BSD-3-Clause, MIT, MIT, ISC, MIT, MIT | their dependencies; checked 2026-09-30 |
| TradingView Lightweight Charts 4.2.3 (vendored file, licence header kept) | Apache-2.0 | price chart (`oracle/gui/static/vendor/`) |

## Data

| Dataset | License | Use |
|---|---|---|
| [FinanceDatabase](https://github.com/JerBouma/FinanceDatabase) (compressed CSVs, fetched at a pinned commit) | MIT | offline universe: names, classification, exchanges (`oracle/universe/financedatabase.py`) |
| [fja05680/sp500](https://github.com/fja05680/sp500) "S&P 500 Historical Components & Changes (Updated).csv" (point-in-time S&P 500 membership since 1996), read at a pinned commit and stored with its fetch time in the research DB, not redistributed | MIT (Copyright 2019-2020 Farrell J. Aultman; its 1996-2019 base is from Andreas Clenow's *Trading Evolved*) | signal testbed membership by day (`oracle/universe/sp500_history.py`); checked 2026-10-01 |
| Wikipedia "List of S&P 500 companies" GICS Sector column | CC BY-SA 4.0; sectors kept locally, not redistributed | testbed sectors of current members (`oracle/universe/sp500_history.py`); checked 2026-10-01 |
| SEC EDGAR XBRL `companyfacts`, `company_tickers.json` and submissions (data.sec.gov, www.sec.gov) | US government data, public domain (no copyright claimed on EDGAR filings data). SEC's fair access terms: a declared User-Agent with a contact, at most 10 requests a second (paced at 5); stored locally in the research DB, not redistributed | testbed fundamentals: EPS, gross profit, revenue, cost of revenue, total assets and filing acceptance times (`oracle/research/xbrl.py`, `oracle/services/xbrl_service.py`); checked 2026-10-01 |
| SEC Insider Transactions Data Sets (www.sec.gov, quarterly `YYYYqN_form345.zip`, 2006 on) and issuer submissions (data.sec.gov) | US government data, public domain (Form 3/4/5 data extracted by SEC's Division of Economic and Risk Analysis; no copyright claimed). SEC's fair access terms: declared User-Agent with a contact, at most 10 requests a second (paced at 5); stored locally in the research DB, not redistributed | testbed Form 4 open-market purchases and sales, Form 4 and 8-K acceptance times and 8-K items (`oracle/research/insider.py`, `oracle/services/insider_service.py`); checked 2026-10-01 |
| Yahoo Finance chart, `^SP500TR` (S&P 500 Total Return index, S&P Dow Jones Indices data via Yahoo, 1988-01-04 on) | Yahoo terms of service; personal, non-commercial use; index data is S&P DJI's, stored locally in the research DB, not redistributed | trend overlay test series (`oracle/services/trend_service.py`); opens equal the close (no real open) on 5380 sessions up to 2021-01-06, and equal the previous close on 281 more (2009-03-30 to 2015-05-21); checked 2026-10-02 |
| Yahoo Finance chart split events | Yahoo terms of service; personal, non-commercial use | split days and ratios of testbed members, so per-share figures match split-adjusted prices (`oracle/market/yahoo.py`); checked 2026-10-01 |
| Wikipedia index member tables (KOSPI 200, S&P 500 and the rest in `oracle/universe/indexes.py`), read through the Wikimedia REST API and cached a week | CC BY-SA 4.0; member symbols and names only, kept locally, not redistributed | index backfill: which home lines the universe lacks (`oracle/services/backfill_service.py`); checked 2026-09-24 |

## Models

| Model | License | Use |
|---|---|---|
| [convaiinnovations/laya-multilingual](https://huggingface.co/convaiinnovations/laya-multilingual) at commit e4e9ddf2 (mmBERT-base, 322M), downloaded once into the Hugging Face cache, not redistributed | Apache-2.0 (model card and Hub metadata); its base, jhu-clsp/mmBERT-base, is MIT | Laya headline scores (`oracle/research/laya.py`); checked 2026-09-30 |

## Services

Called over HTTP, not redistributed.

| Service | Terms | Use |
|---|---|---|
| Yahoo Finance chart, quoteSummary and search APIs (unofficial) | Yahoo terms of service; personal, non-commercial use | daily bars, FX, fundamentals and name lookups (`oracle/market/yahoo.py`, `fx.py`, `fundamentals.py`) |
| SEC EDGAR (company_tickers.json, the submissions API, filing archives including the XBRL viewer's cover page R1.htm) | US government public data, free to reuse; SEC fair-access policy: a declared User-Agent with a contact, at most 10 requests a second (5 used) | filings for research (`oracle/research/edgar.py`); testbed rename and identity evidence (`oracle/services/testbed_renames.py`); checked 2026-10-01 |
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
| Index member pages (one per index in `oracle/universe/indexes.py`), tables only | Wikipedia articles of those names (captured 2026-09-24) | CC BY-SA 4.0 (attribution in each file) | `tests/fixtures/indexes/` |
| `373220.KS.json`, `BT-A.L.json`, `SPY.json`: quoteSummary answers | Yahoo Finance (captured 2026-09-24) | Yahoo terms; small test inputs only | `tests/fixtures/yahoo/` |

## Dev dependencies

| Package | License |
|---|---|
| pytest | MIT |
| pytest-asyncio | Apache-2.0 |
