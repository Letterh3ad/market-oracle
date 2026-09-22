# Decisions

Design and architecture decisions made on the user's behalf. Full design:
`docs/design/2026-09-22-market-oracle-design.md`.

## 2026-09-22 - A new repo, borrowing from the stalker rather than sharing a package

**Context:** The Warframe stalker has proven infrastructure (store, rate limiting,
services layering, news pipeline, GUI pattern) that a stock tool needs too.
**Decision:** Fresh git history in `programming_Shit/market-oracle`. Copy stalker modules
with their tests where they fit, write fresh where they don't.
**Alternatives:** A shared package imported by both (rejected: couples the stalker to a new
project's churn).

## 2026-09-22 - Lightweight relationship graph instead of GraphRAG

**Context:** The user suggested GraphRAG so news about one company reaches related ones.
GraphRAG builds its graph with an LLM pass over every document: hours of GPU time on an
8GB card with a 4B model, and shaky extraction quality, against a goal of running sparingly.
**Decision:** SQLite edges (supplier, customer, competitor, subsidiary, sector, country)
from FinanceDatabase and filings, typed by Laya (~35ms per edge), exposed to the ReAct loop
as a `related_companies` tool.
**Alternatives:** Microsoft GraphRAG (rejected: cost above). LightRAG (MIT, much cheaper;
deferred until a server exists).

## 2026-09-22 - In-house ReAct engine, TradingAgents as an optional Claude backend

**Context:** TradingAgents is the strongest open design, but brings LangGraph/LangChain and
long prompts tuned for frontier models.
**Decision:** A lean ReAct loop sized for a 4B-8B local model, porting TradingAgents' roles
as prompts. TradingAgents itself runs as an optional backend when Claude is selected.
**Alternatives:** TradingAgents on Ollama (rejected: a 4B model struggles with its prompts).

## 2026-09-22 - Laya screens and decides; its probabilities are badged until fitted

**Context:** Laya ships over-confident (ECE 0.466 before temperature fitting), has a weak
`score` type and a 1024-token multilingual context.
**Decision:** Multilingual checkpoint. Use `choice`/`noul` only. Summarise state to fit
the context. Badge probabilities "uncalibrated" until per-question temperatures are fitted
on paper-trade outcomes.
**Alternatives:** Trust raw probabilities (rejected: measured over-confidence). An LLM as
final decider (rejected: no calibrated probability, and the stalker's 4B model said `high`
on 45 of 50 answers).

## 2026-09-22 - Two paper ledgers with conservative, rule-based strategy defaults

**Context:** The user is a programmer, not a trader, and needs an honest track record
before a dummy broker account.
**Decision:** An automatic strategy account and a manual account, £10k each. Strategy rules:
buy only on buy/strong buy above a probability threshold; max 10% per position (no single
bad call sinks the account); max 10 positions (keeps each one reviewable); 20% cash floor
(room to act on new leans); exit on a sell lean or a -8% stop (caps a wrong call before it
compounds). All in config.
**Alternatives:** Manual confirmation only (rejected: human selection biases the score).

## 2026-09-22 - Validation gate: forward paper trades, post-cutoff backtests only

**Context:** An LLM backtested on old news already knows the outcome (look-ahead bias).
**Decision:** AI/news influence is badged "unvalidated" until the strategy account beats a
technicals-only baseline over N paper trades. LLM-driven backtests run only on periods
after the model's training cutoff.
**Alternatives:** Unrestricted backtests (rejected: inflated by leakage). No gate (rejected:
the stalker's core lesson).

## 2026-09-22 - Scheduled ticks first, daemon optional, models on demand

**Context:** The user will run this sparingly to save compute.
**Decision:** `oracle tick` (one pass, then exit) via Task Scheduler, ~3x daily after the
Asia, Europe and US closes. The GUI runs standalone. A DB lock and SQLite-held rate state
coordinate processes. Models load on demand and unload after.
**Alternatives:** Stalker-style always-on daemon hosting the GUI (kept as an option, not
the default).

## 2026-09-22 - GBP base currency, long-only, IBKR as the future dummy broker

**Context:** Global stocks, a UK user, and the plan to move to a dummy broker account.
**Decision:** Every value converts to GBP. Paper fills model IBKR-style fees, 0.1%
slippage, FX spread, exchange hours and 0.5% LSE stamp duty. No shorting until v2. IBKR
paper via `ib_async` in phase 12, the only free option covering global markets.
**Alternatives:** Alpaca paper (rejected as the target: US stocks and crypto only).
