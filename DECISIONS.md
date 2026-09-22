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

## 2026-09-22 - Rate limits are per source, held in SQLite, paced by wall-clock slots

**Context:** The stalker had one API and one process, so an in-memory token bucket was
enough. Here a scheduled tick and the GUI run as separate processes against many sources.
**Decision:** `source_state` holds each source's next free slot as epoch seconds.
`SourceGate.acquire` claims a slot inside `BEGIN IMMEDIATE` (which serialises processes)
and sleeps outside it; idle time banks no burst. Backoff and Retry-After move the shared
slot, so they slow every process. Breaker runs (429 x3, 403 x3, 5xx/transport x5) and
the trip persist, so a restart does not reset a block. Verified by a two-subprocess test
with a negative control. Library-based sources (yfinance) call the gate directly.
**Alternatives:** Per-process buckets (rejected: two processes double the real rate).
A monotonic clock (rejected: not comparable across processes). A lock-server process
(rejected: another thing to keep alive for no gain over SQLite).

## 2026-09-22 - Conservative default source rates

**Context:** Most data sources here are unofficial or unmetered but watched; an IP block
costs far more than a slow tick, and ticks run only ~3 times a day.
**Decision:** yfinance 0.5/s, Stooq 0.5/s, GDELT 0.2/s (GDELT's own ask: one request
per 5s), Google News 0.5/s, ddgs 0.2/s, SEC EDGAR 5/s (half SEC's published 10/s).
Overridable per source in `oracle.toml`.
**Alternatives:** Tune rates by probing (rejected: probing is how blocks happen).

## 2026-09-22 - Job locks: lease plus pid, with a real Windows liveness check

**Context:** A scheduled tick and the GUI must never run the same job twice, and the
stalker showed a hard-killed process leaves state claiming it still runs.
**Decision:** `job_locks` rows carry a token, pid, host and lease. A holder is stale when
its lease lapses without a heartbeat, or, on this host, when its pid is dead; stale locks
are taken over, and a former holder's release or heartbeat is a no-op (heartbeat returns
False so it can stop). On Windows liveness is `OpenProcess` plus
`GetExitCodeProcess == STILL_ACTIVE`: `OpenProcess` alone (the stalker's check) reports a
killed process alive while any handle to it is open, confirmed by a negative control.
**Alternatives:** Lease only (rejected: a crash blocks the job for a whole lease). Pid
only (rejected: pid reuse and other hosts). OS file locks (rejected: behave differently
across platforms and say nothing about staleness).

## 2026-09-22 - Phase 1 review fixes: half-open breaker, Retry-After ceiling, heartbeating holds

**Context:** The two-axis review of phase 1 found real defects in the shared-state code.
**Decision:**
- Migrations re-read `user_version` under `BEGIN IMMEDIATE`; six processes racing a fresh
  DB reproduced `table meta already exists` before the fix.
- The breaker is half-open after cooldown: the tripping run is left one short of its
  limit, so one failed probe re-opens it instead of spending a full run against a live
  block. A success clears it.
- Retry-After is honoured on 503 as well as 429, `0` means "next slot", a zone-less date
  is UTC. A wait over 120s fails the call at once but still moves the shared slot by the
  full wait.
- `JobLock.holding()` heartbeats every lease/3 and exposes `lost`, so a long job keeps its
  lock and learns if it was taken.
- Windows `ACCESS_DENIED` from `OpenProcess` means alive (an elevated tick), and an
  unreadable exit code is treated as alive: a lock is only stolen from a provably dead pid.
- Every shared timestamp is read inside the transaction, after the write lock is held.
- Frontends import only `oracle.services`; config loading moved behind
  `services.context.load_settings`, and the allowlist was narrowed back to the rule.
**Alternatives:** Cap Retry-After silently (rejected: disobeys the server). Sync `held()`
without heartbeats (rejected: a job longer than its lease would run twice).

## 2026-09-22 - Retry the WAL switch on a fresh database

**Context:** After the phase 1 merge a race test failed about 1 run in 20. A stress run
(10 processes x 8 fresh DBs) showed 5 of 80 processes dying on `PRAGMA journal_mode=WAL`
with "database is locked": the switch needs an exclusive lock and SQLite can refuse it
immediately rather than via the busy handler. The code came from the stalker, which has
the same latent race.
**Decision:** `connect()` retries the WAL switch with capped exponential backoff until the
same 30s deadline as the busy timeout. The race test now starts `connect()` behind the
barrier with 10 processes, which reproduced the failure every run before the fix.
**Alternatives:** Serialise first open with a file lock (rejected: more moving parts for a
one-line retry).

## 2026-09-22 - Universe: FinanceDatabase data files loaded into SQLite, no library

**Context:** The `financedatabase` package pulls in pandas and downloads the same `.bz2`
files from GitHub on use. Equities alone are 112,707 rows (93MB CSV); loading that per
command is slow, and tags and watchlists need to join against it.
**Decision:** Fetch the equities and ETF `.bz2` files ourselves through the gate (source
`financedatabase`), pinned to a resolved commit SHA, and load them into SQLite with
stdlib `bz2`/`csv` in one atomic swap. Name and symbol search uses FTS5. Delisted rows
(10,404) are kept but hidden by default.
**Alternatives:** The library with pandas in-memory filtering (rejected: heavy dependency,
seconds per query, no joins). Funds, indices, currencies and crypto (deferred: not needed
for a stock tool yet).

## 2026-09-22 - One row per company by default; tags on companies, watchlists on listings

**Context:** The same company lists on many exchanges (Apple has 19 rows: AAPL, APC.DE,
AAPL.MI...), so raw search results are mostly duplicates. Only 27% of rows carry an ISIN.
**Decision:** Listings sharing an ISIN or a normalised name plus country are one company,
transitively. Neither alone works: most listings carry no ISIN, and some carry another
line's (German Siemens Healthineers listings hold the US ADR's ISIN). A name or ISIN on
more than 30 lines of one exchange links nothing: real companies peak around 25
(Freddie Mac preferreds), while placeholders (96 OTC funds named "ISHARES") and parents'
ISINs on subsidiaries (POSCO) run 34 to 96. Result on real data: 112,705 listings, 64,422
companies. Each company has one primary listing: active before delisted, then an
exchange in its home country (the country most of its listings give), then exchange
tier and market depth, then symbol. Search shows primaries unless `--all-listings`.
Tagging writes a row for every listing of the company, each noting the symbol tagged
(`via`); a company reads the tags of any of its listings, so a tag survives a refresh
that regroups companies or drops one listing, and is orphaned only when every listing
from that tagging has left the universe. Watchlists hold listings,
since a listing is what gets priced (phase 3). Both survive a refresh and flag members
that vanished rather than dropping them.
**Alternatives:** Show every listing (rejected: noise). Group by composite FIGI (rejected:
sparser than ISIN here). Tags on listings (rejected: tagging APC.DE should tag Apple).

## 2026-09-22 - yfinance enrichment moves to phase 3

**Context:** Enriching opened or watched companies needs the PriceSource plumbing.
**Decision:** Phase 2 is the offline universe only; enrichment ships with phase 3.
**Alternatives:** A thin yfinance call in phase 2 (rejected: built twice).

## 2026-09-22 - Universe refresh holds its job lock without a heartbeat task

**Context:** `JobLock.holding()` heartbeats on `clock.sleep`, which under a fake clock
spins and drags fake time forward during every await.
**Decision:** Refresh takes the lock with a 30-minute lease, heartbeats once before it
writes (aborting if the lock was taken), and releases in `finally`. A hard kill is still
freed at once by the pid check. The whole swap is one transaction, so readers see the old
universe or the new one, never a mix.
**Alternatives:** `holding()` (rejected: nondeterministic under the fake clock for no gain
on a job this short).

## 2026-09-22 - Universe filters: company fields from the primary, size-first ranking

**Context:** FinanceDatabase repeats company fields on every listing, not always
consistently (Siemens' Indian listings say India), and bm25 alone ranked junk lines
("NVIDIA CORP NVIDIA ORD" on the LSE's OTC board) above NVDA.
**Decision:** Country, sector, industry and market cap filter and sort on the company's
primary listing; exchange filters the listing, and the row shown is that exchange's
line. Values match case-insensitively; an unknown value fails with the closest real
ones. Text search ranks exact symbol, then market-cap band, then bm25. No text means
browse, biggest first. Blanks sort last in both directions. When nothing can select a
non-primary line, the query reads primaries only (browse ~1s to ~150ms on real data).
**Alternatives:** Per-listing company fields (rejected: Siemens would count as Indian).
Pure bm25 (rejected: ranks short junk names first).

## 2026-09-22 - Phase 2 review fixes

**Context:** The two-axis review of phase 2 found spec gaps and a few risks.
**Decision:**
- Tags: a row per listing (see the universe decision above); `tag remove` on an unknown
  symbol suggests real ones; `tag list <symbol>` works for orphaned symbols; `add_tag`
  checks and writes in one transaction.
- `watch show` marks listings delisted since they were added.
- ISIN links are typed like name links, so a fund never merges with a company.
- SQL field names are checked with a real `ValueError`, not `assert` (stripped by -O).
- Reversing relevance is refused rather than ignored; `--limit` is capped at 500.
- `--sort market-cap --desc` stays smallest first, like every other reversed sort;
  biggest first is already the default.
**Alternatives:** A per-field spec map for the market cap and exchange special cases
(deferred: three call sites, revisit if a fourth appears).

## 2026-09-22 - Prices from Yahoo directly; paid and backup sources as ordered chains

**Context:** The design named yfinance with a Stooq fallback. Probing showed Yahoo's chart
and quoteSummary (cookie plus crumb) endpoints answer a plain httpx client, so yfinance
would add pandas and curl_cffi for nothing; Stooq now serves a JavaScript bot wall.
**Decision:** An in-house Yahoo client through the shared gate (source `yahoo`, replacing
`yfinance`; `stooq` dropped). Prices and FX each come from an ordered source list in
`oracle.toml` (`[prices] sources`, `[fx] sources`), tried in turn. Paid sources are wired
but enabled only by a key (`[keys]` or `ORACLE_<NAME>_KEY`): EODHD for prices, FX and
fundamentals, verified against its public demo key. Free FX backups: ECB SDMX, Frankfurter
and open.er-api (today only). Every stored bar and rate records its source. History: 5
years of daily bars on first fetch, then incremental. `tzdata` so bars carry the
exchange's local date on Windows.
**Alternatives:** yfinance (rejected: heavy deps, bypasses the shared gate). No fallback
price source (rejected: user asked for paid options wired and testable). If Yahoo starts
fingerprint-blocking httpx, a curl_cffi transport can slot under the same client.
