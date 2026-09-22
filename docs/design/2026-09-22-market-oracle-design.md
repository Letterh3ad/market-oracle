# Market Oracle: design

**Date:** 2026-09-22
**Status:** agreed in a grilling session; phase 1 plan not yet written
**Lineage:** borrows proven parts of `../Warframe-market-stalker` where they fit.

## Problem

A local, private tool that researches companies worldwide and produces a buy/sell lean
per company, backed by deterministic analysis, news research by a local ReAct agent, and
a calibrated decision model. It must earn trust before anyone acts on it: every lean is
scored by a paper-trading account first, then a dummy broker account.

The user is a programmer, not a trader. The tool explains itself in plain English and
ships conservative, documented defaults instead of asking for trading judgement.

## Goals

- Search, tag and sort a global universe (country, sector, industry, type, exchange,
  name, user tags) without storing prices for all of it.
- Research any company on demand through a ReAct loop (reason, act, observe) that
  generates its own search queries.
- Combine deterministic analysts and AI analysts into one lean with a probability.
- Paper-trade leans automatically to build an honest track record.
- Run cheaply: scheduled ticks, models loaded only when needed.

## Non-goals (v1)

- Real-money trading. A dummy IBKR account is phase 12; real money is out of scope.
- Shorting (v2).
- Intraday trading. Horizon is swing: days to a few weeks.
- Full GraphRAG (see Decisions).
- Being public. Private repo; copyleft dependencies are acceptable, permissive preferred.

## After v1 (user requests, 2026-09-22)

- **Custom portfolios.** User-created paper portfolios beside the strategy and manual
  ledgers, each scored the same way.
- **Friends on the network.** A friend connects over the local WiFi, and later from outside
  via port forwarding, with their own portfolio to trade against the automated system.
  Needs per-user accounts and login, portfolios owned by a user, and HTTPS before any port
  is forwarded (a forwarded port is reachable by anyone, not just the friend). Shared
  market data and one rate budget across users. Until then the GUI binds to localhost.

## Hardware

Windows 11, RTX 5060 Laptop 8GB. Default LLM `qwen3:4b-instruct-2507-q8_0` via Ollama
(~5GB, measured in the stalker). Laya multilingual (~0.65GB) fits alongside. A larger
local server may replace this machine later; every model backend is pluggable so that is
a config change. A Raspberry Pi port is a nice-to-have, not a constraint.

## Architecture

```
            FinanceDatabase (offline universe, ~300k securities)
                        │ search / tags / sort
                        ▼
 watchlist ──▶ PriceSource (yfinance → Stooq fallback) ──▶ SQLite ◀── FX (to GBP)
                        │
        ┌───────────────┼──────────────────────────────┐
        ▼               ▼                              ▼
 deterministic     AI analysts                    ReAct engine
 analysts          (Chronos-Bolt forecast,        (query generator + tools:
 (technicals,       FinBERT sentiment,             GDELT, Google News RSS, ddgs,
  factors, risk)    event study)                   EDGAR/IR, related_companies,
        │               │                          prices, fundamentals)
        │               │                          analyst roles → bull/bear → risk
        └───────┬───────┴──────────────────────────────┘
                ▼
        Laya (final typed decision + probability)
                ▼
        Lean report (saved per company per date)
                ▼
        Broker protocol ── PaperBroker (strategy + manual ledgers) ── later: IBKR paper
                ▼
        alerts (ntfy, Discord optional)       GUI (FastAPI + vanilla ES modules)
```

### Layers

Same rule as the stalker, enforced by `tests/test_architecture.py`: `oracle/cli` and
`oracle/gui` import `oracle.services` only; `oracle/services` never imports `oracle/gui`;
pure modules (analysts, features) import no I/O.

### Package sketch

```
oracle/
  store/        SQLite, migrations, repos            (from stalker)
  net/          httpx client, token bucket, breaker   (from stalker)
  universe/     FinanceDatabase adapter, tags, search
  market/       PriceSource protocol, yfinance, stooq, fx
  analysts/     technical, factor, risk, forecast, sentiment, event_study
  research/     react engine, query generator, tools/, roles/, graph
  decide/       Laya adapter, question schemas, calibration
  broker/       Broker protocol, paper (fees, slippage, fx, hours), ibkr (phase 12)
  alerts/       ntfy, discord
  services/     orchestration, jobs (single-flight, from stalker)
  cli/          `oracle ...`
  gui/          FastAPI + static ES modules
```

## Components

### Universe
FinanceDatabase (MIT) provides the searchable catalog with its own classification.
User tags live in SQLite on top. yfinance enriches only companies that are opened,
watchlisted or researched. Watchlists drive what gets priced.

### Market data
`PriceSource` protocol: yfinance primary (global via exchange suffixes, unofficial, can
break), Stooq fallback for daily bars. Paid sources (EODHD, FMP) can slot in later.
All values carry their native currency and are converted to GBP for display, sizing and
the ledgers. Every source goes through the shared token bucket and circuit breaker.
Rate state lives in SQLite so separate processes (tick, GUI) share one budget.

### Deterministic analysts (phase 4)
Technicals (momentum, RSI, MACD, volatility, trend), factor scores against sector peers
(value, quality, growth), portfolio risk (sizing, correlation, drawdown). Pure functions
over stored data, each emitting a typed `AnalystSignal`.

### ReAct engine (phase 7)
Lean, in-house loop sized for a 4B-8B local model: short prompts, one tool call per step,
a hard step cap, every thought/action/observation persisted as a trace. A query generator
turns a company plus context into search queries. Tools: GDELT DOC API (global,
multilingual, timestamped, backtestable), Google News RSS, `ddgs`, SEC EDGAR and IR
pages, `related_companies`, price and fundamentals lookups. SearXNG is an optional later
tool. Roles ported from TradingAgents as prompts: fundamental, technical, news and
sentiment analysts, a bull/bear debate, a risk check. LLM backend is pluggable: Ollama
default, Claude alternate. With Claude selected, TradingAgents itself (Apache-2.0) is an
optional backend for a second opinion (phase 11).

### Relationship graph (phase 8)
SQLite edges (supplier, customer, competitor, subsidiary, same sector, same country).
Candidates come from FinanceDatabase and filings; Laya types each candidate edge.
Answers "news about TSMC matters to Apple" without per-document LLM extraction.

### Laya (phase 8)
`convaiinnovations/laya`, multilingual checkpoint (1024-token context), Apache-2.0,
`pip install laya`. Two jobs:

1. **Screen:** per article/candidate, `noul` relevance and a `choice` direction. Confident
   cases skip the ReAct loop; unsure ones go to it.
2. **Decide:** a compact state of all analyst signals and research findings in, a
   `choice` over the five lean labels out, with probability.

Known limits that shape use: ships over-confident (needs per-question temperature
fitting), weak `score` type (use `choice`/`noul`), short context (state must be
summarised to fit). Until fitted, probabilities are badged **uncalibrated**.

### Other AI analysts (phase 9)
Chronos-Bolt probabilistic price forecast, FinBERT sentiment as a cheap second opinion,
event studies (how this stock moved after past earnings and guidance changes).

### Lean report
Lean (strong sell, sell, hold, buy, strong buy), probability, badges (uncalibrated,
unvalidated), one-sentence plain-English reason, per-analyst breakdown, bull and bear
case, dated sourced evidence, ReAct trace, suggested GBP position size, and an
"invalidated if..." condition. Saved per company per date so a lean's history is visible.

### Broker (phases 5, 12)
`Broker` protocol. `PaperBroker` keeps two ledgers of £10,000 each:

- **Strategy account:** executes lean changes automatically under fixed rules. This is the
  honest track record the validation gate and Laya's calibration need.
- **Manual account:** the user's own clicks, scored the same way.

Realism: IBKR-style per-exchange fees, 0.1% slippage, FX spread into GBP, fills at next
open within each exchange's hours, 0.5% UK stamp duty on LSE buys, long only.

Strategy rules (conservative defaults, config, each justified in `DECISIONS.md`): buy
only on buy/strong buy above a probability threshold; max 10% per position; max 10
positions; 20% cash floor; exit on a sell lean or a -8% stop.

Phase 12: IBKR paper via `ib_async` behind the same protocol.

### Running
- `oracle tick`: one pass (prices, news, re-score watchlist, paper fills, alerts) then
  exit. Default schedule via Task Scheduler: after the Asia, Europe and US closes.
- GUI standalone for search and on-demand research.
- Optional always-on daemon with the stalker's DB stop flag.
- A DB-backed lock keeps processes from running the same job twice. Models load on
  demand and unload after, leaving the GPU free between runs.

### GUI
FastAPI plus native ES modules, no build step (stalker pattern). Tabs: universe (search,
filter, sort, tag), company (lean report), watchlist, research trace, ledgers. Every
metric has a plain-English tooltip.

**Force rerun (user request, 2026-09-22).** Scheduled or throttled work that failed or is
waiting (a Yahoo price or fundamentals fetch, FX full-history backfill held by its weekly
retry, a company's news run) shows as a list of failed or deferred jobs. The user ticks
the ones to rerun, per job and per company, and runs them now, off schedule. A rerun still
goes through the shared gate and circuit breaker; it bypasses only the schedule and retry
throttles. Service first (phase 10 job model), GUI button in phase 6 or later.

### Alerts
ntfy primary (free, phone push, self-hostable), Discord webhook optional. Fired on lean
changes and paper executions.

## Validation

The stalker's lesson: nothing AI-driven is trusted before it is measured.

- AI and news influence on a lean is badged **unvalidated** until the strategy account
  beats a technicals-only baseline over N paper trades (N set in phase 12).
- Backtests of anything LLM-driven run only on periods after the model's training
  cutoff, because an LLM reading old news already knows what happened (look-ahead bias).
- Paper-trade outcomes are the labels for fitting Laya's temperature per question type.

## Build order

1. Skeleton from the stalker: store, migrations, net, services layering, architecture
   test, CLI.
2. Universe: FinanceDatabase search, tags, sort, watchlists.
3. Prices and fundamentals: yfinance, Stooq, GBP FX.
4. Deterministic analysts: technicals, factors, risk.
5. PaperBroker with both ledgers.
6. GUI: universe, company, watchlist, ledger tabs. (Usable screener with no model yet.)
7. News tools, ReAct engine, query generator.
8. Laya integration, relationship graph.
9. Chronos-Bolt, FinBERT, event studies.
10. `oracle tick`, scheduling, alerts.
11. Optional TradingAgents backend for Claude runs.
12. Validation gate, calibration fitting, IBKR paper.

Each phase: design notes, TDD plan, `DECISIONS.md` entries, `HANDOFF.md` update, review
before merge.

## Third-party intake

Every borrowed dependency or file is recorded in `THIRD_PARTY.md` with its license,
checked at intake rather than assumed. Ideas reimplemented from other projects are
credited there too.

## Open for later phases

- N for the validation gate, and the baseline's exact definition (phase 12).
- Laya screening confidence thresholds (phase 8, from observed data).
- Which Chronos-Bolt size fits alongside Ollama and Laya (phase 9, measured).
- LightRAG as a graph upgrade once a server exists.
