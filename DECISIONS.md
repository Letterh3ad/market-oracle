# Decisions

Design and architecture decisions made on the user's behalf. The full design and
tickets are planning notes kept locally, outside the repo.

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

## 2026-09-22 - Yahoo fundamentals present a browser user agent for the crumb

**Context:** quoteSummary needs a session cookie plus a crumb. Yahoo answered the crumb
endpoint with 429 to our honest `market-oracle/0.1` user agent and 200 to a browser one
seconds later, so the filter is on the user agent, not the rate.
**Decision:** Only the three fundamentals calls (fc.yahoo.com, getcrumb, quoteSummary) send
a browser user agent, as yfinance does; the chart endpoint keeps the honest one. They go
through the shared `yahoo` gate, so a run of 429s trips the same breaker as prices and
backs every Yahoo call off. Snapshots are refreshed at most daily, for the company's
primary listing; a failed refresh shows the last snapshot with its date.
**Alternatives:** EODHD only for fundamentals (rejected: paid). curl_cffi browser
impersonation (not needed: a header is enough today).

## 2026-09-22 - Phase 3 review fixes

**Context:** The two-axis review of phase 3 found duplicated source chains, a refetch rule
that could loop, and gaps in attribution and typing.
**Decision:**
- `prices show --base` (not `--gbp`): the base currency is configurable, GBP by default.
- Incremental fetches re-read the last 7 days (`REFETCH_OVERLAP`), so late corrections
  and a missed day are picked up without a full refetch.
- Fundamentals follow the `[prices] sources` order; no separate list to keep in step.
- `fx update` exists as its own command, as well as running inside `watch update`.
- FX history: an `fx_history` row per currency records whether a source with history
  (`FxSource.has_history`) filled it and when full history was last tried, failed tries
  included. Until filled, full history is retried at most weekly, so a currency only
  open.er-api (today only) or no source covers does not ask every source for 5 years of
  rates on each run. Cost: after an outage or a source recovering, the backfill can wait
  up to a week; recent rates still update daily meanwhile. A one-off conversion (a
  market cap in a currency no price uses) fetches the last week only. Replaces a
  date-gap rule that refetched full history every run when a source's history started
  late.
- `SourceFailed.status` carries the HTTP status, so callers never match message text.
  A 4xx from the Yahoo cookie page still sets the cookie; only 429, 5xx or no answer
  stop the crumb.
- One chain helper (`market/sources.first_success`): a malformed response counts as that
  source failing, never aborting the run.
**Alternatives:** Per-source gap filling in FX (declined: a missing day borrows the rate
up to 5 days older, `MAX_RATE_AGE`, and it doubles traffic). Exchange holiday calendars
for staleness (declined: too big for now; a week-long closure shows stale briefly).
Inferring "filled" from the stored
`source` column (rejected: a one-off conversion's short fetch would look filled).
Kept as is: `CompanyProfile` extending the store's `ListingProfile` (small, read-only);
`first_success` treating any parse error as that source failing (a parser bug then shows
as "unreadable response", not a crash).

## 2026-09-22 - Phase 4 analysts: peers, risk scope, stored signals

**Context:** Factor scores need peer fundamentals, which are fetched per company on
demand; there is no portfolio until phase 5; the lean report and Laya need signal history.
**Decision:** Peers are the biggest companies in the same industry group (sector when too
few), `[analysts] peers` = 20 by default, adjustable later from the GUI; peer fundamentals
are reused for 7 days. Risk is per listing in phase 4 (volatility-scaled size, max
drawdown, correlation with the watchlist); portfolio risk waits for the ledgers. Every
`AnalystSignal` is stored per symbol, analyst and date; a same-day rerun replaces it.
**Alternatives:** Only already-stored peers (rejected: thin or missing scores). 50 peers
(rejected: more traffic and crumb 429 risk). A hypothetical equal-weight watchlist
portfolio (rejected: rebuilt once real ledgers exist). Recompute on demand (rejected:
history and Laya need storage).

## 2026-09-22 - Signal shape and trend defaults

**Context:** Every analyst needs one output shape the lean report and Laya can read.
**Decision:** `AnalystSignal`: score -1..1, confidence 0..1 (how far the analyst's own
inputs agree), one plain-English reason, named figures; direction is derived from the
score (neutral inside +/-0.2), never stored separately. Keyed by data date, not run date.
An analyst that cannot judge returns `Skipped` with a reason, never a guess. Trend: close
against the 200-day average, 50-day against 200-day, and the 12-1 month return (full
strength at +/-20%); within 1% of an average counts as on it. Needs 253 bars. Uses
adjusted closes so dividends and splits do not read as drops. Analysts may import only
data types; the architecture test enforces it.
**Alternatives:** Direction as its own field (rejected: can contradict the score).
Confidence from signal strength (rejected: that double-counts the score).

## 2026-09-22 - Technical analyst defaults

**Context:** Ticket 02 needs RSI, MACD and volatility signals and one combined line.
**Decision:** RSI (Wilder, 14): outside 30..70 is a mean-reversion hint worded "may
bounce / may pull back", never an instruction; score 0.5..1 by how far out; confidence
is how many of the last 5 readings share the zone. MACD (12/26/9): the histogram's sign
counts twice, the line's once (the crossover leads, the side of zero confirms); reports
the crossover's age. Volatility (20-day, annualised) never gives a direction; against
its median over the past year it cuts the combined confidence (swings 2x usual halve
it). The combined `technicals` signal is the confidence-weighted average of trend, RSI
and MACD. RSI, MACD and volatility need 60 bars.
**Alternatives:** RSI as a trend-follower (overbought = strong; rejected: conflicts with
the trend analyst, which already covers direction). Volatility as bearish (rejected:
high volatility says risk, not direction).

## 2026-09-22 - Peer ordering and the shared fundamentals fetch

**Context:** FinanceDatabase gives market cap only as a band (Mega, Large, ...), so
"biggest 20" needs a tie-break; peers and the company view both fetch fundamentals.
**Decision:** Peers are active equity companies (primary listing) in the same industry
group, ordered by band, then by how many active listings the company has (a proxy for
global prominence), then by name. Fewer than 5 in the industry group falls back to the
sector. `fundamentals_service.fetch_fundamentals` is the one fetch path, with a maximum
age: today for the company itself, 7 days for peers; one client and crumb per run.
**Alternatives:** Ordering by fetched market cap (rejected: needs every candidate's
fundamentals before choosing, far more traffic).

## 2026-09-22 - Factor scoring rules

**Context:** Ticket 04 turns peer comparisons into value, quality and growth signals.
**Decision:** Each figure is a percentile (share of reporting peers beaten, ties half);
a factor averages its figures' percentiles and maps 0..1 to -1..1. Negative P/E, forward
P/E, P/B and debt to equity (losses or negative equity) rank as the worst, never the
cheapest. Confidence is the share of figures agreeing with the factor's direction times
a thinness penalty (reporting peers / 5, capped at 1). Value: P/E, forward P/E, P/B,
dividend yield. Quality: profit margin, ROE, debt to equity. Growth: revenue growth
(the only growth figure the sources give today). `analyse` is now async: it fetches
the company's fundamentals (daily) and its peers' (weekly).
**Alternatives:** Z-scores (rejected: one outlier peer distorts a 20-company group).
Sector-median comparison only (rejected: says less than a rank).

## 2026-09-22 - Per-listing risk in the base currency

**Context:** Ticket 05, before any ledger exists.
**Decision:** Risk works on adjusted closes converted to the base currency (the risk a
GBP holder carries, currency moves included); days without a rate are left out, and
with no rates at all risk is skipped with "run `oracle fx update`". Size = account x
max_position x min(1, target_volatility / yearly volatility), never above account x (1 -
cash_floor). Yearly volatility uses up to 252 daily returns; correlation needs 60 shared
trading days. The risk signal has no direction (score 0). The sizing rules are a
`[risk]` config section (10,000 account, 10% cap, 20% cash floor, 20% target volatility)
that the phase 5 paper broker will share.
**Alternatives:** Native-currency volatility (rejected: hides FX risk for a GBP account).
Fixed-fraction sizing (rejected: a volatile small cap would get the same stake as a
utility).

## 2026-09-22 - When a stored signal is out of date

**Context:** Ticket 06 shows stored signals in `watch show` and `company` without
recomputing them, so readers need to know when one is old.
**Decision:** Price-based signals (trend, RSI, MACD, volatility, technicals, risk) are
out of date once a bar newer than their data date is stored. Factor signals are dated
by their fundamentals and are out of date once those are more than a week old (the peer
reuse window). `watch show` summarises technicals and the three factors as short marks
(`tech+ val- qual= grow+`, `*` when out of date); `company` prints the full breakdown.
**Alternatives:** Comparing run time with bar fetch time (rejected: every `prices update`
refetches the last week, so everything would read stale after each update).

## 2026-09-22 - Phase 4 review round 1

**Context:** Two-axis review of phase 4 against `main`.
**Decision:**
- Momentum is part of trend (the 12-1 month return), not its own analyst: the design's
  list names what technicals cover, and a separate momentum signal would double-count
  the same return inside the combined line.
- Risk skips watched symbols gone from the universe (they broke every analysis); its
  text uses the configured base currency, not a hardcoded pound sign.
- MACD with no crossover in the data says how long it has been on one side, never an
  invented crossover (`days_since_cross` = -1).
- `analyse` fetches the company's and its peers' fundamentals in one call, one crumb.
**Alternatives:** Kept as is: the analyst name lists (display order, combined inputs,
watch marks) live beside their use; one registry would be premature with nine analysts.
Risk shows out of date when FX lags the last bar by more than 5 days: honest, since its
series really ends earlier.

## 2026-09-22 - Planning files stay out of the repo

**Context:** The user asked for CLAUDE.md, `.scratch/` and other planning files to be
ignored, keeping DECISIONS.md and README.md as the tracked record.
**Decision:** `/CLAUDE.md`, `/.scratch/` and `/docs/` (design doc, agent conventions) are
gitignored and untracked; local copies stay. THIRD_PARTY.md stays tracked: it is the
license record, not planning. Earlier commits still hold those files; history is not
rewritten.
**Alternatives:** Rewriting history to purge them (declined: private repo, force-push).

## 2026-09-22 - Paper fills: next open, whole shares, pessimistic costs

**Context:** Phase 5 paper ledgers must not flatter the strategy; the validation gate and
Laya's calibration will be measured on them.
**Decision:** Two accounts (`strategy`, `manual`) start with `[risk] account` in the base
currency. An order fills at the open of the first stored bar dated after the day it was
placed, in whole shares. Costs: 0.1% adverse slippage; 0.2% FX spread on each conversion
(none for base-currency listings); IBKR fixed-plan fees in the listing's currency (US
0.005 per share, min 1, max 1% of value; UK and Europe 0.05%, min 3; elsewhere 0.1%,
min 5); 0.5% stamp duty on LSE buys. A buy is sized so every cost fits its amount. No FX
rate for the fill day leaves the order pending with the reason. Cash and positions are
replayed from fills (average cost), never stored. `prices update` fills pending orders
after fetching; `oracle tick` (phase 10) will own that sequencing.
**Alternatives:** A `Broker` protocol now (deferred to phase 12: with one implementation
it would only delegate). Fractional shares (rejected: most non-US venues do not offer
them). Filling at the placing day's close (rejected: look-ahead).

## 2026-09-22 - Strategy rules engine

**Context:** Ticket 04: the strategy account trades on leans under fixed rules.
**Decision:** A pure `decide()` over leans, holdings, cash, equity and risk sizes. Sells
first: a stop (8% or more below average cost) sells whatever the lean says; otherwise a
sell or strong sell lean sells the whole position. Buys by probability, highest first:
buy or strong buy at 60% or more, at most 10 positions, sized at the risk analyst's
suggested share of equity capped at 10%, never spending below the 20% cash floor. Sell
proceeds are not counted until they fill. Held or pending listings are not bought again.
Every decision, skips included, is logged with its reason. Thresholds are a
`[strategy]` config section; sizing reuses `[risk]`. Leans are stored per listing, date
and source so AI leans can sit beside the baseline.
**Alternatives:** Topping up held positions (deferred: needs a rebalance rule).
Counting pending sell proceeds (rejected: optimistic; a sell can still be rejected).

## 2026-09-23 - Baseline lean from technicals and factors, never traded when out of date

**Context:** Ticket 05: until AI leans exist, the strategy account needs a lean.
**Decision:** `oracle analyse` builds a `baseline` lean from the current stored signals:
technicals plus value, quality and growth, averaged by confidence. Score bands match
signal direction (+-0.2 ends neutral, +-0.6 starts strong). Probability is 0.5 plus half
the agreeing confidence out of all four inputs, so missing inputs lower it. No technicals
signal means no lean (factors alone say nothing about timing). Stale signals (a newer bar
stored, or fundamentals over a week old) are left out; a recent factor signal from an
earlier run is kept, so one failed fundamentals fetch does not thin the lean. The lean is
dated by the last bar; `paper step` skips a watched listing whose lean is older than its
last bar, or that has none, and logs why. Stops still apply to held listings either way.
**Alternatives:** Only this run's signals (rejected: a transient fetch failure would drop
the factors). Trading on the last lean regardless of age (rejected: never trade on stale data).

## 2026-09-23 - Phase 5 review round 1

**Context:** Two-axis review of phase 5 against `main`.
**Decision:**
- Pending strategy buys reserve their cash and take a position slot, so repeated
  `paper step` runs before a fill cannot break the cash floor or the position cap.
- A buy must spend more than nothing. A bar with no open is skipped; the order fills at
  the next bar that has one, rather than waiting forever.
- A listing gone from the universe: a held position still sells, at the dearest fee band;
  a buy is rejected (its exchange, and so any stamp duty, is unknown).
- A stop on a listing whose lean is out of date logs only the sell.
- `paper buy SYMBOL AMOUNT` takes a positional amount in the configured base currency,
  not the ticket's `--gbp` (the base currency is not always GBP).
- Equity is recorded once per day that fills run (`prices update` or `paper fill`), not
  on a daily timer; the largest fall is measured over those days. `oracle tick`
  (phase 10) will run fills daily, closing the gaps.
- A sell whose fee exceeds its proceeds leaves cash lower, as a real broker would.
**Alternatives:** Deferred as follow-ups: a per-sell realised P&L figure (only the running
total is shown), side-by-side account columns (phase 6 GUI), enums for side, status and
action strings, merging `Holding` and `Position`, store-level tests for paper and lean
repos. A position that has never had a rate counts as nothing in equity until one arrives.

## 2026-09-23 - Phase 5 review round 2

**Context:** Second two-axis review of phase 5, mostly the round 1 fixes.
**Decision:**
- A buy amount must be finite as well as positive (`nan` would have crashed every fill run).
- A zero risk size means no buy, like a missing one.
- A listing with a pending order gets a logged skip ("an order is already pending") on
  each step. A held listing with nothing to do (no sell lean, no stop) logs nothing.
- Strategy decisions are logged as each order is placed, not after the loop.
**Alternatives:** Moving `print_fills` out of `cli/common.py` again (kept: it is shared by
`paper` and `prices`, which is what common is for).

## 2026-09-23 - Phase 5 review round 3

**Context:** Third and last full review round of phase 5.
**Decision:**
- A held listing gone from the universe can still be sold, by hand or by a stop: selling
  falls back to the account's own holdings when the universe no longer knows the symbol.
- A watched listing gone from the universe is skipped ("no longer in the universe"), so
  one delisting never aborts `paper step`.
- A buy of a listing gone from the universe is rejected at once, not after its next bar.
- One logged decision per listing per step: a pending order or a sell outranks a lean
  that is missing or out of date.
**Alternatives:** Further review rounds are capped: from here only critical and high
findings are fixed.

## 2026-09-23 - Phase 6 scope: force rerun deferred, market-cap sort direction

**Context:** Planning phase 6 (GUI).
**Decision:** The force-rerun list moves to phase 10: it needs a record of failed and
deferred jobs, which only the phase 10 job model provides (today's jobs are single-flight
locks only). Market-cap sorting follows every other field: ascending is smallest first,
`--desc` biggest first; a search without text still defaults to biggest first. Ledgers
split into a read-only view and a trading ticket.
**Alternatives:** A thin failed-job table in phase 6 (rejected: would be rebuilt in
phase 10). Keeping the old market-cap direction and renaming the flag (rejected: every
other sort reads "desc" as largest first).

## 2026-09-23 - GUI skeleton: localhost, error codes, in-process route tests

**Context:** Ticket 02: `oracle gui`.
**Decision:** FastAPI plus native ES modules, the stalker's pattern, served by uvicorn in
the CLI's own thread (the SQLite connection stays on the thread that opened it; every
route is `async def`). `[gui]` defaults to `127.0.0.1:8740`, so nothing else on the
network can reach it until the LAN step after v1. Service errors reach the page as
`{"error": message}`: unknown symbol or watchlist 404, bad input 400, blocked by state
(job running, universe not loaded, no prices) 409, upstream failure 502, any other
service error 400; anything unexpected is a plain 500 pointing at the terminal, never a
traceback. Route tests run in-process through `httpx.ASGITransport`, which keeps requests
on the test's own thread, so no cross-thread SQLite workaround is needed. One glossary
module holds every metric's plain-English tooltip.
**Alternatives:** The stalker's `TestClient` (rejected: needs `check_same_thread=False`
in tests, which production never uses). A JS test runner (declined by the user: browser
checks by hand per tab).

## 2026-09-23 - Question-driven discovery as phase 8b

**Context:** The user asked whether the ReAct loop can research a question to find new
companies, not just companies already named. The design covered research on any company
on demand and related-company surfacing through the graph, but not starting from a question.
**Decision:** Add phase 8b, Discovery: a question becomes queries, candidates are matched
to the universe and widened through the relationship graph, and the result is a saved,
rerunnable shortlist with evidence and research/watch actions. After phase 8, because it
reuses the ReAct loop and tools (7) and the graph plus Laya screening (8).
**Alternatives:** Building it in phase 7 (rejected: would duplicate the graph and
screening that phase 8 adds). Leaving it until after v1 (rejected: it is how the user
means to find companies).

## 2026-09-23 - Watchlist tab: jobs run in the request, analysis locked, symbol index

**Context:** Ticket 04, plus two slow spots found checking it in the browser.
**Decision:** "Update prices" and "Analyse" run inside their request under the
single-flight job lock; the server keeps answering other requests while a job awaits the
network (no transaction is held across an `await`, checked by a scan of the code), and a
job running anywhere answers 409 at once. `analyse_watched` now takes an `analyse` job
lock too, for the CLI as well. "Update watched prices, then fill paper orders" moved from
the CLI into `routine_service.update_prices`, shared by both frontends and the base for
`oracle tick`. The job-busy message is plain English. Watch lines carry the latest lean
and flag it when older than the last bar. Migration 0017 adds a case-insensitive symbol
index: every symbol lookup was scanning the whole universe (~100ms each; now ~1ms). The
status line counts listings with a plain `COUNT(*)` (the full per-company counts took
~570ms on every 30-second poll).
**Alternatives:** Background jobs with a job-state table and polling (deferred: the
phase 10 job model owns job history and force rerun; progress here is "running").

## 2026-09-23 - Company tab: GUI settings written to oracle.toml, single-listing jobs

**Context:** Ticket 05. The peer count must be adjustable from the page, and a company
you do not watch still needs prices and analysis.
**Decision:** GUI-changed settings are written into `oracle.toml` itself (one line edited
by `config.with_setting`, comments and other settings kept; the section or file is made
when missing, where `oracle` looks for it), checked to load before it is kept, and applied
to the running app. The job routes take an optional `symbol` to price or analyse one
listing, watched or not; one listing's price update also fetches its currency's FX and
runs fills. The chart is TradingView Lightweight Charts 4.2.3 (Apache-2.0), vendored like
the stalker's. Symbols link to `#company/SYMBOL`; tabs take an argument after the slash.
**Alternatives:** Settings in the database overriding the file (rejected: two sources of
truth, and the CLI would disagree with the GUI). A TOML writer dependency (rejected: it
would drop the user's comments).

## 2026-09-23 - Penny shares opt-in for v2; day trading stays a non-goal

**Context:** The user asked about penny shares and day trading, later or in v2.
**Decision:** Penny shares become a v2 opt-in with guardrails: a liquidity floor,
spread-based costs (flat 0.1% slippage would make paper profits fiction), a promotion-risk
flag, strategy account excluded by default. Day trading stays out: the system is a daily
loop with minutes of local-model time per company, free intraday data is delayed and
short, costs eat intraday moves, and the AI's edge (news, filings, supply chains) plays
out over days to weeks. The practical form is a 1-3 day swing horizon on daily data (v2).
**Alternatives:** A small intraday paper sandbox for learning, much later, never the main
strategy (left open).

## 2026-09-23 - Ledgers view: realised result per sell

**Context:** Ticket 06, and the phase 5 follow-up asking for realised P&L per sell.
**Decision:** The average-cost replay now also yields each sell (date, shares, proceeds,
cost of the shares sold, realised), so the running total is the sum of the per-sell
figures by construction; hand-checked against the phase 5 worked example. The ledgers tab
shows both accounts in two columns with the same metrics, an equity chart once fills
have run on two days, positions, waiting orders with their notes, sells, and the
strategy's decision log. Read-only; trading is ticket 07.
**Alternatives:** Storing realised P&L per sell (rejected: derived data, replayed like the
rest of the ledger).

## 2026-09-23 - Ledgers trading: the page trades the manual account only

**Context:** Ticket 07.
**Decision:** The buy and sell routes are fixed to the manual account and refuse any
`account` field (a 400 naming it) rather than ignore it; the strategy account moves only
through "Strategy step", which runs the same rules engine as `oracle paper step`.
Refusals from the service (amount not positive, not held, too many shares) appear under
the form that caused them. "Fill now" and "Strategy step" report each outcome.
**Alternatives:** An account picker on the forms (rejected: manual trades on the strategy
account would spoil the track record the validation gate relies on).

## 2026-09-23 - Phase 6 review round 1

**Context:** Two-axis review of phase 6 against `main`.
**Decision:**
- Single-listing price and analyse jobs take the same one-at-a-time locks as the
  watchlist runs (CLI `oracle analyse SYMBOL` too); "update prices" for one listing also
  runs fills for both accounts, as the watchlist run does (strategy orders still come only
  from its rules).
- Every figure carries a tooltip: analyst figures from `analysts/meanings.py` (keyed by
  analyst and name), ledger figures from the page glossary; the strategy tooltip is built
  from the configured rules, not hard-coded numbers.
- FX credits appear wherever converted values do: ledgers (from the rates valuing
  positions) and the price chart (from every rate in its history). The chart names its
  currency and falls back to the listing's own when a day lacks a rate.
- "Fill now" lists orders still waiting for their next opening price, with the reason.
- The status panel shows each source's allowed rate. Its "why" is the breaker's reason
  while paused; no last-error history is kept (the phase 10 job model can add one).
- Settings writes are atomic (staged file, checked to load, then renamed) and keep the
  file's line endings.
- Website links are followed only for http and https addresses.
- Late answers never repaint a tab the user has left; shared JS helpers for formatting,
  JSON requests and busy buttons.
**Alternatives:** Deferred as low: the ledgers route replays fills three times per
account (fine at paper-account sizes); a watch line shows no lean when a listing has no
prices (a lean cannot exist without them).

## 2026-09-23 - Phase 6 review round 2

**Context:** Two-axis review of `phase-6-gui` against `main`, focused on the round 1 fixes.
No hard standards breaches; round 1 fixes confirmed correct.
**Decision:**
- Ledger credits also cover the rates past fills used (proceeds, costs, realised results),
  not only those valuing open positions, so open.er-api stays credited after a sell.
- Settings writes stage to a unique temp file in the same folder, removed on any failure.
- "Fill now" reads pending orders straight from the store (`pending_orders`) instead of
  valuing every account.
- Lean, next-step and order-outcome JSON built once (`oracle/gui/routes/shapes.py`).
- Percent and signed formatters show "n/a" for missing values, like money.
**Alternatives:** Deferred as low: one shared JS guard for late answers (three tabs use a
counter, ledgers a root check; both work); the ledgers route's hand-picked strategy rules
could become a service-side view; single-listing fills run outside the prices lock
(harmless on the single-threaded server, revisit with the phase 10 job model).

## 2026-09-23 - Phase 7 scope: research reports only, ten tickets

**Context:** Phase 7 brings news tools, the ReAct engine and the query generator. The
lean the strategy trades on is the technicals and factors baseline.
**Decision:** Research produces a saved, cited report (badged unvalidated) and a trace, but
does not change the lean; Laya combines both in phase 8. `related_companies` returns
universe peers until the phase 8 graph replaces it behind the same tool. A Claude backend
ships alongside Ollama (default). Tools reuse the shared gate and breaker, one source each.
**Alternatives:** Nudging the lean with research now (rejected: untested AI influence on
paper trades before the validation gate exists).

## 2026-09-23 - ReAct engine shape

**Context:** Ticket 1 of phase 7: the loop has to work on a 4B local model.
**Decision:**
- Each step is one JSON reply (`thought`, `action`, `input`) with the action constrained
  to the tool names by Ollama's `format`. Tools take one string: a 4B model fills one field
  far more reliably than an argument object.
- A garbled reply, an unknown tool or a failing tool becomes an observation the model sees;
  only a backend that cannot answer at all fails the run.
- At the step cap (default 8) the model is asked once more, constrained to `final`, so a
  capped run still has findings; the run is marked `step_cap`.
- Steps are saved as they happen, so a failed run keeps its trace. One run at a time
  (`research` job lock).
- Ollama calls skip the rate gate (localhost has no budget to share); `num_ctx` 8192, since
  Ollama's default is too small for several observations; temperature 0.
- `market_data` reads stored data only, with figures as labelled percentages (a raw 1.49
  ROE was read by the model as 1.49%), and forgives stray JSON punctuation in its input.
**Alternatives:** Native tool calling (rejected for now: uneven across small models, and
the constrained JSON step works on every backend).

## 2026-09-23 - News evidence: one article per URL, citations checked

**Context:** Ticket 2 of phase 7: research needs dated, sourced news it can cite.
**Decision:**
- Google News RSS (en-GB edition) through the shared gate as `google_news`. Articles are
  stored once per URL whichever search found them; each run records what it was shown.
- Observations list at most the 8 newest articles as `[a<id>] date outlet: title`, titles
  cut at 140 characters, so several searches fit a small model's context.
- Findings cite as `[a<id>]`. Citations of anything the run was not shown are removed, so
  an invented id never makes a claim look sourced; the ones kept are marked cited.
- Links are Google News redirect URLs. Resolving them to the publisher is left to the page
  reader (ticket 4).
**Alternatives:** Keeping invented citations flagged (rejected: a reader skims past a flag).

## 2026-09-23 - GDELT and as-of research runs

**Context:** Ticket 3 of phase 7: worldwide news, and research replayable as of a past day
for backtests after the model's training cutoff.
**Decision:**
- `gdelt_search` uses the DOC API (ArtList JSON); outlet is the domain, date is when GDELT
  first saw it. A plain-text answer (GDELT's way of refusing a query) is shown to the model.
- GDELT paced at one request per 10 seconds with one retry: it asks for one per 5 seconds
  and answers a quicker one with a lockout that each further request lengthens.
- An as-of run sees only what existed by the end of that day (UTC) in every tool, not just
  news: `market_data` drops later bars, fundamentals and leans, else a replay would read
  the future from the price. Undated articles are dropped, as they may be later. The task
  prompt names the day and tells the model not to use what it knows from after it.
- GDELT's DOC API covers roughly the last three months, so older replays rely on Google.
- Citations written `(a3)` count like `[a3]` and are rewritten as `[a3]` (seen live).
**Alternatives:** Cutting only news at the as-of date (rejected: prices leak the outcome).

## 2026-09-23 - Web search and the page reader

**Context:** Ticket 4 of phase 7: the open web beyond news feeds.
**Decision:**
- `web_search` through `ddgs` (MIT; intake in THIRD_PARTY.md). It makes its own HTTP calls,
  so the tool drives the `ddgs` gate itself: a slot before each search, 429 on its rate
  limit, a transport error otherwise. Five results with 100-character snippets, sized so a
  full list fits one observation (eight with longer snippets were cut mid-line live).
- `read_page` fetches through a new `web` source (1 per second, one retry, 2-minute
  cooldown: three paywalled 403s in a row say little about the next site). It reads at
  most 2 MB, shows at most 1300 characters, and accepts HTML or plain text only.
- The reader refuses non-http(s), `localhost`, `.local`/`.internal` names and non-public IP
  literals: links come from pages the model read, so they may point into this machine or
  the LAN. DNS names resolving to private addresses are not checked (personal tool).
- Pages lead with their meta description when the body is thin (script-built pages).
- Google News links only redirect by script, so the reader asks the model to search the
  headline instead.
- As-of replays get neither tool: web pages and results carry no reliable date.
- Our user agent carries no contact address; sites that require one (Wikipedia) refuse it.
  Adding the user's email to requests to arbitrary sites was not done without asking.
- `RateLimitedClient.get` gains `max_bytes` (streams and stops; drops the encoding and
  length headers of the cut, already-decoded body).
**Alternatives:** ddgs's own `extract` for pages (rejected: bypasses the gate and breaker).

## 2026-09-23 - SEC filings, and a contact sent to SEC only

**Context:** Ticket 5 of phase 7. SEC refuses requests whose User-Agent lacks a declared
contact (name and email). The user chose a config setting sent to sec.gov only.
**Decision:**
- `[research] contact = "Name email"` (validated to hold an email). Sent in the
  User-Agent to sec.gov hosts only, never to other sites. Unset, `filings` and reading a
  sec.gov page answer with how to set it, without a request.
- `filings` lists key forms only (10-K, 10-Q, 8-K, 20-F, 40-F, 6-K, DEF 14A, S-1, S-4 and
  amendments); insider and ownership forms are noise for a lean. Filings are dated
  evidence, so they work in as-of replays too.
- SEC's ticker map is cached in SQLite (`sec_tickers`) and refreshed weekly.
- A symbol with an exchange suffix (VOD.L, 7203.T) is answered as non-US without a request;
  US share classes use a dash (BRK-B) and are tried with SEC's dot form too.
- Reading a sec.gov page goes through the `sec_edgar` budget, not the general `web` one.
- The live EDGAR check reads `ORACLE_SEC_CONTACT` and skips without it, so no personal
  contact is ever sent by a test unasked.
**Alternatives:** Sending the contact to every site (rejected by the user: it would unblock
Wikipedia but hands the email to arbitrary hosts).

## 2026-09-23 - related_companies from the universe until the graph exists

**Context:** Ticket 6 of phase 7. The relationship graph is phase 8.
**Decision:** `related_companies` returns the factor analysts' own peer choice (same
industry group, else sector), at most 10, largest first, with the basis named once. No
network. Depositary receipt lines (names with ADR/GDR) are skipped: they repeat a company
already listed and filled 3 of 10 slots for Apple (Samsung's GDRs). The peer choice for
factor scores is unchanged. Phase 8 swaps the graph in behind the same tool name.

## 2026-09-23 - Query generator: name seeds, a local-language query, model suggestions

**Context:** Ticket 7 of phase 7. Left alone, the 4B model searched "Apple Inc. recent news
headlines 2026".
**Decision:**
- Seeds from the company's short name (legal forms and "Group"/"Holdings" stripped: "Sony
  Group Corporation" is "Sony"): "<name> shares", "<name> earnings outlook", and where the
  listing's country reads another language, "<name> <local word for share price>" ("Sony 株価",
  "Siemens Aktie"). FinanceDatabase has Latin-script names only, so the native-script name
  is asked of the model rather than seeded.
- One model call before the loop suggests up to 4 more (no dates or "latest"); blank,
  overlong (100+ characters) and repeated ones are dropped. A garbled reply leaves the seeds.
- The queries are saved with the run and given to the loop as its starting plan.
- The CLI replaces characters the console cannot encode ("?") instead of crashing: a
  Japanese query crashed `oracle research 7203.T` through a cp1252 pipe.
- Citations grouped as `[a71, a85]` (seen live) count, rewritten as `[a71] [a85]`.

## 2026-09-23 - Research roles and the report

**Context:** Ticket 8 of phase 7: turn a run's evidence into a report a reader can check.
**Decision:**
- After the loop, six one-call roles in TradingAgents' structure (structure only, prompts
  written fresh): fundamental, technical and news analysts (up to 3 claims each), a bull
  and a bear (one case each, reading the analysts' findings), and a risk manager (up to 3
  risks and an "invalidated if" condition).
- Every claim must cite evidence the run was shown; one without a valid citation is dropped,
  and a side with none reads "no case the evidence supports".
- The stored prices, fundamentals and analyst signals become one dated evidence item
  (`stored data`, as of the run's day), so data facts are cited like articles.
- The report is saved per run as JSON, badged unvalidated, and never touches the lean;
  `oracle research SYMBOL --history` lists past reports.
- Live, a role reply looped inside a JSON array until the 180s timeout (reported wrongly as
  "not reachable"). Fixed three ways: role arrays carry `maxItems` (Ollama's grammar honours
  it), every reply is capped at 1024 tokens, and a timeout is reported as a timeout. A run
  with roles now takes about a minute on the 4B model.
**Alternatives:** Several debate rounds as in TradingAgents (rejected for a 4B model: each
round costs a call and small models repeat themselves rather than rebut).

## 2026-09-23 - Research in the GUI

**Context:** Ticket 9 of phase 7.
**Decision:**
- `POST /api/jobs/research?symbol=` runs research inside the request under the job lock,
  like the other job buttons (about a minute); `GET /api/companies/{symbol}/research`
  returns the latest run with its report, evidence and trace.
- A model backend that cannot answer is 503 (a service of this machine, not an upstream).
- Evidence carries a `link` only for http(s) addresses, and the page checks again
  (`safeUrl`, now shared in `dom.js`); links open in a new tab with `noopener noreferrer`.
- The Company tab gains a Research section: bull and bear cases, findings by role, risks,
  "invalidated if", cited evidence with in-page jumps from each [aN], and a collapsible
  trace. Every part has a glossary tooltip; the unvalidated badge explains the gate.
- A separate research tab was not added: the trace sits under its report on the Company
  tab, where the symbol is already chosen.

## 2026-09-23 - Claude as a research backend

**Context:** Ticket 10 of phase 7: `[llm] backend = "claude"` runs the same loop, query
generator and roles on Claude.
**Decision:**
- Through the official Anthropic Python SDK (MIT), not raw HTTP. Credentials come from
  `ANTHROPIC_API_KEY` or an `ant auth login` profile; nothing is stored in oracle.toml.
- `claude_model` defaults to `claude-opus-5`, a separate setting from the Ollama `model`
  so switching backend needs one line. Paid per token; Ollama stays the default.
- Replies use structured outputs (`output_config.format` with the step or role schema),
  closed with `additionalProperties: false` and all fields required; array-length limits
  are dropped for Claude (unsupported) as callers cap lengths themselves.
- Refusals fall back server-side (`fallbacks: "default"`, beta
  `server-side-fallback-2026-07-01`), routed by category. A request refused even so is
  handed back as no answer, which the loop treats as not understood.
- Credential, rate-limit, status and connection failures become plain ModelErrors naming
  the fix. Tests fake the SDK client (the SDK runs on httpx2, so the httpx fake web cannot
  stand in); the live check runs only with `ORACLE_CLAUDE_SMOKE=1`, as it costs money.

## 2026-09-23 - Phase 7 review round 1

**Context:** Two-axis review of `phase-7-research` against `main`.
**Decision:**
- `read_page` follows redirects itself, one hop at a time (at most 5), re-checking each
  target: a public page could otherwise bounce the reader into this machine or the LAN,
  and carry the SEC contact to another host. Pages are asked for uncompressed, so the 2 MB
  cap bounds memory; a body of exactly the cap is no longer flagged cut.
- A malformed address (`http://[bad`) is refused as invalid instead of crashing the run.
- Any failure, not only a model error, marks the run failed (never left "running").
- The run's model names any fallback that answered after a refusal on Claude.
- Claude timeouts read as timeouts; its client is closed after each call.
- Articles keep the query that first found them; the stored-data item a report cites is
  kept per run with its text, so a report can be checked later.
- A capped run says so in the trace's last step, not only in its status.
- The Company tab keeps the last report on screen when a newer run fails, with the error.
- `research_tools` split into a package (shelf, market, news, web, filings) with one fetch
  helper, one evidence-line format and one citation writer; wording no longer assumes a
  local model; the adapted HTML stripper is credited in THIRD_PARTY.md.
**Alternatives:** Deferred as low: `related_companies` has no country basis (the phase 8
graph adds it); seeds carry a local-language word, not a native-script name (the model is
asked for that); a per-run scope type for the tool wiring. Server-side refusal fallbacks
stay on for Claude (Anthropic's guidance for Opus 5), the user can turn them down.

## 2026-09-23 - Phase 7 review round 2 fixes

**Context:** Round 2 found runs that could still be left running, a fallback model written
as prose into `model`, a newer replay hidden behind an older live report, and the step-limit
note missing from the GUI trace.
**Decision:**
- One guard covers a whole run after it starts. A failure to mark it failed is added as a
  note on the original error, never raised in its place. A cancelled run says "cancelled".
- The report, citations and finish are written in one transaction.
- Fallback models get their own column (`fell_back_to`, m0025) and a `ChatModel` protocol
  field, cleared per run and recorded on failed runs too. Claude detects a fallback by the
  API's `usage.iterations` `fallback_message` entry, not by comparing model ids (an alias
  can come back as a longer dated id).
- The Company tab shows the newest reported run by when it ran, not by report day.
- The GUI trace shows the forced final step's note; evidence shows the query that found it.
- A redirect with no Location is its own error; the duplicate status check is gone.
**Alternatives:** Comparing served and configured ids for equality (the review's
suggestion) would flag every dated alias as a fallback. Showing both a live report and a
replay was left for later.

## 2026-09-23 - Phase 7 review round 3 fixes (last full round)

**Context:** Round 3 found no critical or high issues; lows and mediums fixed in this round.
**Decision:**
- Claude records the fallback entry's own `model` from `usage.iterations`, not
  `response.model`.
- `ResearchRun.model_label` builds "backend model (X answered when it refused)" once, for
  the CLI and the GUI.
- Report history is ordered by when each run started, so the CLI and the Company tab agree
  on "newest".
- A failure note on a `ModelError` is carried onto the `ModelUnavailable` raised in its place.
- A test proves the report, citations and finish roll back together.
**Alternatives:** Deferred as low: the service clears `model.fell_back_to` per run, which
relies on `single_flight` serializing runs (returning fallbacks per reply would keep that
state inside the run); the GUI trace tells a forced final step by its observation, which
relies on the engine leaving normal final steps without one.

## 2026-09-23 - Instruct stays the research default; thinking writes reports on an opt-in deep run
**Context:** Compared `qwen3:4b-instruct-2507` with `qwen3:4b-thinking-2507` writing the six
report roles from the same evidence (AAPL, 7203.T, SAP.DE), plus the user's abliterated
`general-heretic` on 7203.T.
**Decision:** Instruct remains the default for the loop and the roles. A user-chosen "deep"
run keeps the loop on instruct and writes the roles on thinking. Thinking was more
accurate (valuations read right, one or two precise citations per claim) but ~15x slower
(8 to 10 min against ~35s) and lost one of 18 replies to bad JSON. It needs a far larger
reply cap (~6144 tokens) and context (16k): at the 1024 cap it spends every token thinking
and returns nothing.
**Alternatives:** Thinking as the default (too slow for routine runs); thinking for the loop
too (slower still, no evidence it searches better); `general-heretic` (matched instruct,
since research never triggers refusals, so abliteration buys nothing).

## 2026-09-23 - New build order after the grilling: evidence first
**Context:** User wants fake-money evidence as soon as possible, GUI parity for a future
compiled build, discovery before the graph, per-task models, and a backtest.
**Decision:** Named streams before phase 8, in this order: `autotick`, `ai-account`,
`gui-parity`, `universe-quality`, `models-and-deep`, `backtest`, `discovery`, then phase 8.
Phase numbers stay; each stream ships its own GUI controls.
**Alternatives:** Parity first (delays evidence by several sessions); renumbering phases.

## 2026-09-23 - Automatic ticks run in-process, off by default
**Context:** The design planned `oracle tick` under Windows Task Scheduler; a compiled app
for users with no command line makes OS tasks awkward.
**Decision:** A scheduler inside the GUI server with a toggle, off on a fresh install. One
daily time (22:30 local, weekdays). A run missed while the app was closed runs once on the
next start. Sequence: prices, FX, fill, analyse, step (plus AI-account research once that
stream lands). Status shows next run, last run, last error. Listings with three "no data"
results on three distinct days are marked dead: skipped by updates, never primary, with a
"check again" button.
**Alternatives:** OS task (later option); per-exchange times; catching up every missed run.

## 2026-09-23 - A third paper account trades on AI verdicts
**Context:** Research is advisory by design, so the rule account can never show whether the
AI adds anything.
**Decision:** An `ai` paper account trades a structured verdict (buy/hold/sell, confidence,
invalidation) from the report roles, on the same watchlist, cash and risk limits as the
strategy account. Research refreshes when a report is over 7 days old, price moved over 5%
since it, or on demand; each tick has a 30-minute research budget, overflow rolls to the
next tick. Rules, AI and buy-and-hold equity are compared side by side. Once discovery
lands, an opt-in toggle lets the AI account trade the `discovered` watchlist too.
**Alternatives:** Advisory only (no measurement); research overriding the rule account
(blurs which one earned the result).

## 2026-09-23 - Model profiles and task slots across providers
**Context:** User wants to switch models per task and use Claude or OpenAI optionally, while
staying free on local models. Replaces the single `deep_model` idea.
**Decision:** Providers `ollama`, `openai_compatible`, `claude_api`, `claude_code`. Named
profiles (provider, model, caps, timeout, key). Slots: research loop, report roles, query
planning, discovery chat, deep roles, AI account; one default plus per-slot overrides and
a per-run picker. Every default is Ollama, and every model feature must work on Ollama
alone. `claude_code` drives the user's logged-in `claude -p` (subscription); used only when
picked, and hidden in distributed builds unless a toml flag enables it, since Anthropic
bars third-party products offering claude.ai login without approval.
**Alternatives:** One `deep_model` setting; API-only Claude.

## 2026-09-23 - GUI parity: settings in the toml, guarded by a test
**Decision:** The GUI edits `oracle.toml` through `tomlkit` (keeps comments), validated by
the config loader before writing, applied live except `db_path` and host/port (restart
banner). Keys are masked. A manifest test fails when a CLI command has no GUI route or a
stated reason. Universe refresh becomes a background job with progress, cancel and an
atomic swap.
**Alternatives:** Settings in the DB (two sources of truth); restart for every change.

## 2026-09-23 - Universe quality: dead listings, suffix-blind grouping, user listings
**Context:** BTQ.DE (BT Group on XETRA) was primary but Yahoo has no data for it; the LSE
home listing sat in a separate "BT Group" company.
**Decision:** Suggest live sibling listings with a one-click swap; grouping ignores legal
suffixes (plc, Inc., AG, Ltd), blocked when both ISINs exist and differ, shipped only after
a report of new merges on the real universe is reviewed. Listings found by discovery are
resolved by Yahoo search, verified by a chart fetch, and kept in a `user_listings` table
that refresh never touches.
**Alternatives:** Leaving FinanceDatabase data as is.

## 2026-09-23 - Discovery is a guided research chat, auto mode in v1
**Decision:** A chat engine: the agent proposes a plan, runs it, reports cited findings and
suggests next directions; the user steers or presses Stop. Auto mode (in v1) takes its own
directions for up to N rounds within a time budget, stopping when a round finds nothing
new, and adds finds to a `discovered` watchlist. Output: up to 10 ranked candidates with
Watch / Research / add to universe. Company Research stays the six-role report.
Conversations are saved. Ships before phase 8; the graph improves it later.
**Alternatives:** Approving every tool call; interrupting mid-step (hard for a 4B model);
a full report per candidate (10+ minutes).

## 2026-09-23 - Rule-only backtest
**Decision:** `backtest` reuses the tick pipeline with a simulated clock on a chosen
watchlist, with a survivorship-bias warning, next-open fills, commission (default 0) and
slippage (default 10 bps), against equal-weight buy-and-hold. AI is never backtested on
periods the model has seen.

## 2026-09-23 - The scheduler polls instead of sleeping until the due time
**Context:** Ticket autotick 02: settings must apply live, and a laptop that sleeps through
a due time must still notice it.
**Decision:** Every 30 seconds the scheduler asks whether the most recent weekday due time
has a tick newer than it. A setting change counts due times only from the poll before it,
so turning scheduling on, or moving the time, never fires at once for a time already
passed. At start there is no such limit, so the latest missed run fires once as a
catch-up, unless no tick has ever run (nothing was missed). A failed or skipped tick
counts as done; nothing retries until the next due time.
**Alternatives:** Sleeping until the due time with a wake-up event (more moving parts,
misses a sleeping laptop's due time); catching up before any tick has run.

## 2026-09-23 - tomlkit replaces the regex settings writer
**Decision:** `with_settings` (tomlkit) writes several keys in one section at once and keeps
comments; the staged-file-then-validate write around it is unchanged.
**Alternatives:** Keeping the regex writer (wrong on strings, arrays and nested tables).

## 2026-09-23 - Dead listings: counted per day, kept apart from the universe
**Decision:** A `listing_health` table (a refresh replaces `listings` whole) holds the
distinct days every price source answered "no data" since bars last came. Three days
marks the listing dead, dated to the first of them. Throttling, network errors, breaker
trips and unreadable answers never count. Watched updates and ticks skip dead listings;
a single-listing update (the Check again button, `oracle prices update SYMBOL`) always
tries. Dying or recovering re-picks the company's primary listing at once.
**Alternatives:** A column on `listings` (lost on refresh); counting runs, not days.

## 2026-09-23 - Autotick review round 1: follow-ups left low
**Context:** Standards and spec review of the autotick branch.
**Decision:** Fixed: an empty price answer counts as "no data" on every fetch (the window
always reaches back past the last stored bar); a running GUI re-reads `[schedule]` when
oracle.toml changes; the last tick is the one that finished last, and any tick finishing
after a due time covers it; an interrupted tick is recorded. Left as low follow-ups:
no-data days are UTC dates (same day as local for UK evening runs); tick states and
triggers are strings, not enums; the dead-listing wording repeats across frontends;
`ListingOutcome` carries `error` and `skipped` side by side.
**Alternatives:** Local no-data days (needs the zone threaded into the price service).

## 2026-09-23 - Discovery moves up; ticks warn before heavy work
**Context:** User wants evidence and autonomy first, and the AI account will make ticks use
the GPU for up to the research budget while they may be using the PC.
**Decision:** Order is now `ai-account`, `discovery`, `gui-parity`, `universe-quality`,
`models-and-deep`, `backtest`, then phase 8. `ai-account` gains a ticket: a browser
notification and banner before each scheduled tick (default 10 minutes) with Postpone 1
hour and Skip today, a Stop button while a tick runs, and an opt-in to research only when
the PC is idle. Ticks stay once a day at 22:30 local: every stage works on daily bars and
fills at the next open, so more ticks add nothing.
**Alternatives:** Windows toast now (new dependency, deferred to packaging); ticks several
times a day (no gain on daily data).

## 2026-09-23 - Work runs as jobs listed in an Activity tab
**Context:** GUI work runs inside its request: buttons block, a second research click is
refused, nothing can be cancelled, and progress is visible only on the page that clicked.
**Decision:** A job runner in the GUI server (started and stopped with it). Every button
that starts work submits a job to a per-kind FIFO queue (research one at a time), and a
new Activity tab lists running jobs with their stage, queued jobs, and finished ones, each
cancellable while queued or running. Jobs held by other processes show read-only from the
job locks. Queued jobs are dropped on restart ("not run: app closed"); finished ones are
kept. Built before `ai-account`, whose tick research and Stop build on it.
**Alternatives:** Refusing a busy research click (today); running research in parallel (an
8GB GPU does one run well); resuming queued jobs on restart (surprising).

## 2026-09-23 - App control: restart, new-version banner, start at login
**Context:** The live GUI runs detached from a `main` worktree; updating it means killing a
process by hand, nothing says when newer code is on disk (F5 reloads page files, not the
server), and it stops at every reboot.
**Decision:** A small `app-control` stream right after `activity`: a Restart button (the
server starts a copy of itself, then exits; the copy waits for the port), a banner when
the code on disk differs from the running version, and a "Start Oracle when Windows
starts" toggle writing a launcher to the user's Startup folder (no admin, no dependency).
Each has a CLI equivalent; OS-specific parts sit behind small interfaces.
**Alternatives:** A Windows service (admin rights, harder to see); Task Scheduler at logon
(works, but a Startup-folder file is visible and easy to remove by hand); auto-restarting
on new code (would cancel running research without asking).

## 2026-09-23 - Turning the system off: an automation switch and Shut down, no daemon split
**Context:** User wants to turn the whole system off, as the stalker's daemon could be
stopped from its GUI. Oracle's automation runs inside the GUI server.
**Decision:** `app-control` 04: an Automation On/Off master switch in the header
(`[automation] enabled`; nothing starts by itself while off, manual work still runs; every
automatic job source checks it), and a Shut down button that ends the process, with a
Market Oracle desktop / Start-menu shortcut to start it again.
**Alternatives:** A separate scheduler daemon controlled from the GUI (a second process to
start, stop and keep in sync, for no saving: the idle server costs nothing); relying on
the schedule toggle alone (later automation, AI research and discovery auto mode, would
each need its own switch).

## 2026-09-23 - Jobs record their server's pid; interrupted is its own outcome
**Context:** Activity review gate. A starting server closed every queued or running job row,
including a second live server's (app-control's Restart will overlap two servers briefly).
Work cut off by the app closing showed as "cancelled", the same as a user's Cancel.
**Decision:** `jobs.pid` (m0029) names the owning server; the startup sweep closes only rows
whose pid is gone, and a live server's rows cannot be cancelled from another. A job running
when its server stops or dies ends `interrupted`; queued ones end `cancelled`, "not run".
A scheduled tick cancelled before it starts is recorded as skipped, so it is not resubmitted.
**Alternatives:** A heartbeat per job row (more writes for the same answer on one machine);
keeping `cancelled` with the reason in the error text (the state column then lies).

## 2026-09-23 - GUI jobs run on worker threads with their own connection
**Context:** A watchlist Analyse held the server's event loop for ~30 s on the real DB, so
every page stalled until it finished. The job runner ran work as tasks on that loop.
**Decision:** A job's work takes the context it runs with, and the runner runs it through
an executor. The GUI server uses a thread executor: one thread per running job, its own
event loop and SQLite connection, same config, clock and transport. Cancel reaches the
worker's task across threads, and the runner waits for the job's cleanup before recording
the outcome. Queue-logic tests use an inline executor. Measured on a real-DB copy: 158 page
loads during a 32 s Analyse, worst 56 ms.
**Alternatives:** A process pool (pickling contexts and results, no shared clock or fake
transport in tests); chunking long loops with `await asyncio.sleep(0)` (every future long
stretch would need the same care); running only the analysis in `to_thread` (fixes one job,
not the class).

## 2026-09-23 - Restart spawns a detached copy that waits for the port
**Context:** app-control 01. The server must restart itself from the page, and the new copy
must outlive whatever started the old one (a Claude session included).
**Decision:** The server starts its own command (interpreter, absolute config, working
folder, spelled out rather than copied from `sys.argv`) in its own minimised console, out of
the parent's job object when Windows allows it, then asks uvicorn to exit gracefully. The
new copy binds its own socket, retrying for 30 s while the old one lets go. `/api/status`
names the answering server's pid, so the page reloads only once a new one answers.
**Alternatives:** A supervisor process that restarts the server (a second thing to keep
alive); `os.execv` in place (Windows starts a new process anyway, and the old one's port and
jobs would not shut down cleanly first); no console window (the log would go nowhere).

## 2026-09-23 - Start at login is a Startup-folder shortcut that runs the loaded code's folder
**Context:** app-control 03. Oracle should start with Windows without admin rights or new
dependencies, and must run the live worktree even if the current server was started from
another folder.
**Decision:** A `Market Oracle.lnk` in the user's Startup folder, written through
PowerShell's WScript.Shell with values passed as environment variables; its presence is the
setting. It starts the running interpreter with the absolute config, minimised, in the
folder the `oracle` package was loaded from. Restart uses the same command.
**Alternatives:** A Task Scheduler entry (more moving parts, harder to see and delete by
hand); a `.vbs` launcher (VBScript is being removed from Windows); a `.cmd` (flashes a
console); pywin32 for the shortcut (a dependency for one call).

## 2026-09-23 - Automation switch stops only automatic work; shortcuts use Windows' folders
**Context:** app-control 04 and its review gate.
**Decision:** Jobs carry an `automatic` flag (scheduled and catch-up ticks today); turning
automation off from the GUI offers "Stop it" / "Let it finish" for those only, and the CLI
switch leaves running work alone and says so. A tick that fell due while automation was off
never fires late. Desktop, Start-menu and Startup folders come from `SHGetFolderPathW`
(OneDrive-redirected desktops work). Restart, Shut down and the automation switch sit in
the always-visible header; start-at-login and shortcuts in the App section, which names the
running version and the folder the launchers start. A launcher written from a server in the
dev checkout points at the dev checkout: set them from the live GUI's App section.
**Alternatives:** Cancelling every job on "off" (manual work is the user's own); asking
every time even with nothing automatic running (noise); `%USERPROFILE%\Desktop` (wrong under
OneDrive).

## 2026-09-23 - Research answer checks live in the report JSON; a run with no usable role fails

**Context:** ai-account 06: every research role's answer is checked (usable or not, claims
dropped by reason) and the counts are shown with the report. The ticket asked for a migration.
**Decision:** The per-role checks are stored inside `research_reports.body` beside the claims,
since they are only ever read with the report; reports saved earlier read back with no checks.
If no role gave a usable answer, the run finishes `failed` with "the model's answers were
unusable", keeps the loop's notes, and saves no report (no exception, so the job reads done
and the company page shows the failure beside the last report). The CLI exits 1 then. The
fake models now answer a well-formed empty reply for roles a test does not script.
**Alternatives:** A `research_checks` table (a migration for data never queried on its own);
saving an empty report for an all-garbled run (looks like research that found nothing);
raising (the run is already recorded as failed; an exception adds nothing but a noisy job).

## 2026-09-23 - The verdict reads the statistics and the whole run, under a context budget

**Context:** ai-account 01: each report ends in a buy/hold/sell verdict. The user asked that
it read the regular statistics and the full research run, not only the final report.
**Decision:** A seventh role, `verdict`, runs after the six (only when the report is usable).
Its input, filled in this order: the stored-data line (prices, 1-day/1-month/3-month change,
fundamentals, baseline lean and probability, analyst signals; never trimmed), the
researcher's notes, the six roles' outputs, cited evidence, tool observations that show cited
evidence, other observations, then uncited evidence. Budget: `([llm] context - 1024) * 4`
characters less the system prompt; Claude uses a fixed 200k tokens. Filling stops at the first
item that does not fit, so nothing lower-priority takes its room; what was left out is counted
by kind ("did not read: 7 uncited items") and shown. The reason must cite an item the verdict
role was actually shown; otherwise, and for a bad label, confidence or blank condition, the
verdict is rejected with its reason. It reuses ticket 06's `Answer` check.
**Alternatives:** Skipping items that do not fit and packing smaller later ones (lets uncited
items displace role outputs); counting tokens with a tokenizer (a dependency for a rough
budget); letting the verdict cite anything the run was shown (it could cite what it never read).

## 2026-09-23 - The verdict is stored in the report JSON; replays never supply the latest verdict

**Context:** Ticket 02's AI step reads each listing's latest verdict.
**Decision:** `verdict`, `verdict_rejected` and `unread` live in `research_reports.body` like
06's checks (no migration); older reports read back with none ("written before reports had
verdicts"). `latest_verdict(ctx, symbol)` returns the newest live run whose report has a valid
verdict, found with `json_extract`; as-of replays are excluded so a backtest never trades live.
**Alternatives:** A verdict column or table (a migration for one query `json_extract` serves);
returning the newest report even when its verdict was rejected (ticket 02 can still apply its
own age limit to the older valid one).

## 2026-09-23 - The AI account shares the strategy's step; its verdicts become leans

**Context:** Ticket 02 adds a third paper account, `ai`, trading research verdicts under the
strategy's rules.
**Decision:** `strategy_step` and `ai_step` share one decide/place/log helper; only the input
step differs. A verdict becomes `Lean(symbol, report day, label, confidence, "ai")`, so buy,
hold and sell go through the same pure `decide`, sizes (risk analyst), limits and stop loss.
The AI step uses the newest valid live verdict even when a newer report's verdict was
rejected (subject to the age limit); with no valid one it skips, quoting the newest report's
rejection. Age is report day to today, at most `[ai_account] max_verdict_age_days` (default 7,
1 to 90). Off, `ai_step` returns None and the CLI and GUI say so. `strategy_decisions` gains
`account` (old rows `strategy`) and `report_id` (the research run) rather than a new table.
`[ai_account]` reloads live like `[schedule]`.
**Alternatives:** A second rule set for verdicts (drifts from the strategy's); skipping when the
newest report's verdict was rejected (throws away a valid, still-fresh call); a separate
`ai_decisions` table (two logs to query for ticket 04's comparison).

## 2026-09-23 - Ticks refresh stale research inline, within a time budget

**Context:** Ticket 03: while the AI account is on, each tick keeps research fresh before the
AI step.
**Decision:** Two new tick stages, `research` (after `analyse`) and `ai_step` (after `step`),
both no-ops that say so while the AI account is off. Research runs inline in the tick's worker
thread, calling `research()` per listing, not as separate Activity jobs: the tick is already a
job, so queuing runs from it means cross-thread waits for no gain, and cancelling the tick
stops its research too. A listing is due with no report, a report older than
`max_verdict_age_days` (reused, so the refresh age matches the trading age), or a close moved
more than `move_threshold` (default 5%) since the report day's close. Order: no report first,
then oldest report, then biggest move. New runs start only while
`research_budget_min` (default 30, 1 to 480) lasts on `ctx.clock`; the rest is deferred to
the next tick. A busy research lock (a manual run) defers the rest instead of failing them;
other errors count as failed and the next listing runs. Stage progress ("research 2 of 5:
MSFT") doubles as the tick lock's heartbeat. Settings are edited from the Ledgers AI column.
**Alternatives:** Research as runner jobs the tick waits on (the older HANDOFF note); a
separate "max report age" setting (two ages that can disagree); retrying the next listing
after a busy lock (a manual run holds it for minutes).

## 2026-09-23 - Rules, AI, manual and buy-and-hold are compared from the day the AI account started
**Context:** ai-account ticket 04 needs one start day and a buy-and-hold benchmark to measure the accounts against.
**Decision:** The start is the AI account's first equity record. Each account's line is its return against its first
record on or after that day; trades and costs count its fills since then. Buy-and-hold splits the starting cash equally
over the listings watched today, each bought at its first close (base currency) on or after the start and held with no
costs; a missing day or a close with no FX rate keeps the last value, a listing not yet priced waits as cash, and one
with no close from the start on (or gone from the universe) is left out and named. With no AI history yet, each account
is shown from its own first record and buy-and-hold is empty. Pure helpers live in `oracle/broker/compare.py`; `oracle
paper history` without `--account` prints the comparison.
**Alternatives:** buying at the last close before the start (a weekend start would then use a stale price); counting
buy-and-hold costs (hides the benchmark behind fee assumptions); the watchlist as it was at the start (not stored).

## 2026-09-24 - Postpone and Skip are scheduler state; Skip is a tick record at the due time
**Context:** ai-account ticket 05 warns before a scheduled tick and lets the user postpone or skip it.
**Decision:** Skip stores a skipped tick ("skipped: skipped by the user") dated at the due time, so `ticked_since`
covers it, the next poll never fires it, and a restart still honours it. Postpone (one hour, once per due time) is held
in the running `Scheduler` only; a restart forgets it and the tick then fires as a catch-up. The GUI always builds the
`Scheduler` (polling only with `schedule=True`) so its postpone and skip routes answer. Changing `warn_minutes` or the
idle settings never moves the scheduler's floor; only `enabled`, `time` and automation do. Stop is Activity's cancel.
**Alternatives:** persisting postponements in the DB (a new table for a one-hour state); a skip dated now (would not
cover a due time still ahead).

## 2026-09-24 - Research waits for an idle PC only when asked, on every tick
**Context:** research can hold the GPU for up to the research budget; the user may be using the PC.
**Decision:** `[schedule] research_when_idle` (off) with `idle_minutes` (15): the research stage defers every due
listing ("PC in use") when the last keyboard or mouse input is more recent; the light stages still run. The idle time
comes from `oracle/system/idle.py` (Windows `GetLastInputInfo`); a PC that cannot tell counts as idle, so research is
never held off forever. It applies to manual ticks too (the option is about the PC being in use, whoever started it).
Browser notifications ask permission only from a "Notify me" click and fire once per warning; the in-page banner
always shows. `warn_minutes` defaults to 10 (0 to 120, 0 off).
**Alternatives:** gating only scheduled ticks; a Windows toast (deferred to packaging); a new notification dependency.

## 2026-09-24 - ai-account gate: the newest report rules, the comparison starts when the AI is first on

**Context:** Review gate round 1 on ai-account overturned four recorded choices.
**Decision:** The AI step and research freshness use each listing's newest live report: no
valid verdict on it means skip (quoting why) and due for research; an older valid verdict is
never traded past a newer rejected one. A backend failure at the verdict keeps the report,
with "the model could not write a verdict: ..." as the rejection. Tick research orders due
listings by last attempt (any run status), never tried first, so a listing whose research
always fails cannot hold the front of the queue. The `ai` account's equity is recorded only
while it is on, so "since the AI account started" means since it was first turned on. The
verdict budget fills at 3 characters a token (75% of the old 4), since numbers and URLs run
nearer 3.
**Alternatives:** Falling back to the newest valid verdict within the age limit (trades a call
the latest research no longer supports); ordering by report day (starvation); recording `ai`
equity from the upgrade (a start the user never chose).

## 2026-09-24 - GUI parity manifest in tests, with untracked gaps pending
**Context:** gui-parity 01 pins every CLI action to a GUI route. The survey missed three gaps
(`jobs status`, `tag list`, `prices sources`).
**Decision:** The manifest is test data (`tests/gui_parity.py`). Routes are checked against
the app's OpenAPI schema and against the page scripts, so a route with no page control fails
too. The three missed gaps are `Pending("untracked: ...")` until ticketed. Flags that only
shape CLI output are exempt as output-only.
**Alternatives:** A manifest under `oracle/` (the app does not need it); exempting the missed
gaps (hides real gaps).
**Follow-up (2026-09-24):** all three are now mapped (`GET /api/activity`,
`GET /api/universe/tags`, `GET /api/status`); no `Pending("untracked: ...")` is left.

## 2026-09-24 - One generic settings path over every oracle.toml key
**Context:** gui-parity 07: every oracle.toml key must be editable in the GUI; there is no
CLI command to pin in the manifest.
**Decision:** `GET/PUT /api/settings` over `setting_list()`, walked from `Config`, so a new
key appears by itself. A save builds the file with `with_settings` and goes through the safe
writer, which loads it with the real loader first: bad values are refused with the loader's
message and validation stays in one place. Meanings come from inline comments in
`oracle.toml.example` (a test requires one per key). `[schedule]`, `[automation]`,
`[ai_account]` and `[analysts]` apply live (now all reloaded by `reload_live_settings`);
the rest say "applies after Restart" and offer the header's Restart. `[keys]` values are
never sent: rows say set/not set and name the environment variable that overrides them.
The manifest holds `SETTINGS_FORM` routes, checked as served and called by the page.
**Alternatives:** One setter per key (dozens, validation duplicated); sending masked secrets
(still leaks length); applying every key live (db and gui changes need a fresh process).

## 2026-09-24 - A replay is a job of its own, subject `SYMBOL@day`
**Context:** gui-parity: research replays as of a past day run from the Company tab through the
job runner, which joins a second submit with the same kind and subject.
**Decision:** A replay's subject is `SYMBOL@YYYY-MM-DD` (label "Replay SYMBOL as of day"), so it
never joins, or is taken for, a live run of the listing; two presses for the same day join.
**Alternatives:** The listing as subject (a replay would join a live run and report its result);
a unique subject per press (duplicate replays of one day).

## 2026-09-24 - Universe refresh and Update FX are runner jobs
**Context:** gui-parity: both are long network work the GUI must start and show like any other.
**Decision:** Both go through the job runner and show in Activity. The universe refresh has one
subject (`universe`), so a second press joins it, with its stage as progress. Update FX
dedupes the named sources and uses them, joined in order, as the subject (`*` for all), so the
same source list joins while a different one runs as its own job.
**Alternatives:** Blocking requests (the page waits minutes, no Activity row); one FX subject
for every source list (a narrower update would silently join a wider one, or the reverse).

## 2026-09-24 - Messages name the action, not a CLI command
**Context:** The app ships compiled, so a GUI user has no terminal; services held CLI wording
with a GUI override (`ServiceError.in_gui`), which put GUI knowledge in the service layer.
**Decision:** Every message a service, broker or research module raises or stores names the
action in words both frontends fit ("update its prices first", "refresh the universe first",
"analyse it first"); `in_gui` is gone. `tests/test_architecture.py` fails on any "`oracle "
in those packages. CLI help text in `oracle/cli` may still name commands. The GUI's
unexpected-error message says to try again and then Restart. A locked oracle.toml on save is
`ConfigInUse` (409): "oracle.toml is open in another program: close it and try again".
**Alternatives:** Keep `in_gui` (GUI wording in services, every new message needs two); map
each message per frontend (a table to keep in step with every service message).

## 2026-09-24 - A dead listing's siblings and the swap
**Context:** Ticket universe-quality 01: a watched listing no source has data for should
point at the company's live listings.
**Decision:** Siblings are the company's other listings that are neither delisted nor dead,
in the primary-pick order (`rank_listings`, which `pick_primary` now takes the head of). A
swap puts the new symbol in the old one's place on every list (dropped where the new one is
already listed), in one transaction, and refuses the same listing, another company's, a
delisted or dead one, and a symbol on no list. Paper positions and orders stay on the old
symbol; the answer names the accounts holding it. The Company tab offers Swap only when the
listing is watched; otherwise it lists the siblings as links.
**Alternatives:** Appending the new symbol to the end of each list (loses the user's order);
moving paper positions (a position is a fill history at another listing's prices).

## 2026-09-24 - Suffix-blind grouping: the legal forms, the ISIN block, the report
**Context:** Ticket universe-quality 02: "BT GROUP PLC" (LSE) and "BT Group" (XETRA) sat in
two companies. Grouping must not change while the setting is off.
**Decision:** `[universe] suffix_blind_grouping` (default false, live). Legal forms stripped
from the end of the normalised name only, keeping at least one word: plc, inc,
incorporated, corp, corporation, co, ltd, limited, ag, sa, nv, se, spa, ab, asa, oyj, kk
(punctuation already gone, so "p.l.c." and "S.A." match). Not Holdings, Group or Company. It
is a second pass after the unchanged grouping, so off runs exactly the old code path; a
blind link joins two companies unless both already carry trusted ISINs and share none, is
typed (funds never join companies) and per country, and obeys MAX_LINES_PER_EXCHANGE. The
report regroups the stored listings with the setting on and lists new groups spanning more
than one stored company, with the "name, country" they matched on; it runs as a `merges`
job and never writes. Turning it on applies at the next universe refresh; no separate
Regroup job yet.
**Alternatives:** Stripping anywhere in the name (catches "Co-operative"); a pairwise ISIN
check (order-free but needs every member pair); a Regroup job without the download (left for
when the user has reviewed the report).
**Follow-up (2026-09-24, review):** the real-universe report joined different companies whose
forms stripped alike ("Chiyoda Co., Ltd." and "Chiyoda Corporation", both on JPX). A blind
join is now refused when two of the companies it would bridge both have a line on one
exchange (two home lines on one exchange are two companies), or both carry trusted ISINs and
share none. A conflict skips every join it touches, per matched name and again per chain of
joins, so the result no longer depends on row order (an ISIN-less "Foo" joins neither "Foo
plc" nor "Foo AG" with different ISINs). The report shows each side's countries, ISINs and
legal forms (abbreviations spelled out, so Corp and Corporation agree) and flags, first and
counted in the header, merges whose sides name different legal forms (plc and Limited: maybe
a successor company) and merges matched with no country. 728 proposed merges became 675.

## 2026-09-24 - Reading stage: what is read, stored and reused
**Context:** Deep research ticket 01: verdicts should rest on what articles say, not their
headlines, on a 4B model.
**Decision:** After the loop, up to 12 articles the run was shown (`READ_ARTICLES`, a setting
in ticket 04) are read: newest first, one per outlet before any outlet's second, skipping the
stored data and anything already settled. A fetch goes through the fetch path the `read_page`
tool now shares with it (gate, `unsafe_url`, per-hop redirect checks, byte cap,
readable types). Text is kept in `article_texts` up to 100,000 characters; a failure is kept
with its reason, and a passing one (network, rate limit, open breaker, a Google News link in
a replay) is tried again by the next run. Pages under 300 characters of text count as
unreadable (paywall or script page); the loop's `read_page` stores its text or that verdict
too, so the stage never fetches a page twice. Notes are kept per (article, "backend/model")
in `article_notes`; a garbled reply or a backend error on one note is kept as the reason and
retried next run, never failing the run. The progress line reads from a new
`research_runs.stage` column.
**Alternatives:** Keeping failures forever (a timeout would hide an article for good);
reusing `articles.body` for page text (it means text Market Oracle wrote itself); notes keyed
per article only (a model switch would reuse another model's reading).

## 2026-09-24 - The note call and the context budget
**Context:** One small model call per article must fit a 4B model at 8192 tokens today and
16384 after ticket 04.
**Decision:** A short system prompt naming the company, the article's evidence line as the
heading, and its text cut to min(12,000 characters, what fits at 3 characters a token beside
the prompt and 1024 reply tokens), marked " [cut]". Schema: `about_company` (boolean) and
`facts` (array of strings, maxItems 3); facts are trimmed, cut to 200 characters and
deduplicated. Not about the company means no facts and a marker on the headline line. The
roles now read a budgeted digest: stored data and the researcher's notes always, then lines
with facts, then headlines alone, strictly in that order within the context less the longest
role prompt and 4000 characters kept for the analysts' findings and the cases. The verdict's
fill order puts cited items with facts before cited headlines and a new "uncited items read"
kind before step observations and uncited headlines.
**Alternatives:** Several articles per call (fewer calls, but a 4B model mixes them up);
letting the model cite ids in its facts (one article per call makes the id certain, so the
code adds it); an unbudgeted roles digest (12 noted articles could crowd out the later
roles' inputs).

## 2026-09-24 - Resolving Google News links
**Context:** Google News RSS links are redirect stubs `read_page` refuses.
**Decision:** The feed's `<source url>` is now stored as `articles.site`. A stub is resolved
by a web search (through the ddgs gate) for its headline, taking the first result on that
site or a subdomain of it; with no site stored, a domain label must match the outlet's name.
No match is a lasting skip with the reason; replays never search and leave the link unread.
**Alternatives:** Following Google's redirect (it answers with a script page, not a Location
header); decoding the article id in the stub (undocumented and has changed before).

## 2026-09-24 - Local news as its own loop tool
**Context:** Listings outside the English-speaking markets need Google News in their own
country and language, and the ReAct loop runs on a 4B model.
**Decision:** A separate `local_news_search` tool, offered only when the listing's country
has an edition (`EDITION` beside `LOCAL` in `oracle/research/queries.py`). The step schema
limits `action` to tool names, so the model cannot misspell or forget the choice; the input
stays a plain query. English-speaking listings see the same tools and the en-GB edition as
before. Editions were checked live (a pairing Google does not serve redirects); Denmark has
none and uses Norway's, where Google itself sends it.
**Alternatives:** An input flag such as `local: <query>` on `news_search` (a small model
drops or garbles it, and nothing in the schema enforces it); always searching both editions
in one call (doubles Google traffic and halves the room per edition in an observation).

## 2026-09-24 - Planned searches name their tool and stay at six
**Context:** Local-edition and press-release seeds mean more seeds, and each planned search
costs one of the loop's 8 steps.
**Decision:** Seeds and suggestions are `Search(tool, query)`; the task lists them as
`- tool: query`. A press-release / investor-relations seed goes to `web_search` (only when
the web is offered, so never in replays). Seeds are always kept and suggestions fill up to
`MAX_PLANNED` = 6 in all (US: 3 seeds + 3; Japan: 4 + 2). A suggestion with non-Latin
letters goes to the local edition. Stored queries stay plain strings, so no migration.
**Alternatives:** Plain query lines (the model would send local-script and press queries to
the English news tool); letting the plan grow to 8 (the loop would spend every step on seeds).
Ticket 04 raises the steps to 16 and may raise `MAX_PLANNED` with them.

## 2026-09-24 - Which filings the reading stage reads, and where they come from
**Context:** Ticket 03: a US listing's filings text should be read, taking at most 2 of the
run's reading slots, before articles.
**Decision:** The stage looks the filings up itself (when a SEC contact is set), whether or
not the loop called `filings`: the latest 10-Q or 10-K, then 8-Ks from the 30 days before the
run's day (the as-of day in a replay, so only filings made by then), newest first. They are
kept as the run's evidence, so they are citable, and take the first 2 unsettled slots. Other
SEC documents the loop listed or read (older reports, proxies, amendments) are not read by
the stage, so untrimmed filings never take article slots. A filing is always fetched again
by the stage, since a copy the loop read is stored whole. Amendments, 20-F and 6-K are left out.
**Alternatives:** Reading only filings the loop happened to list (a 4B model often skips the
tool); 8-Ks before the periodic report (an earnings 8-K's main document is mostly a pointer to
its exhibit, while MD&A carries the numbers).

## 2026-09-24 - Trimming a filing to its key sections
**Context:** A 10-Q or 10-K runs to hundreds of thousands of characters and a note call reads
12,000. EDGAR HTML is inline XBRL, headings vary in case and layout, and the table of
contents repeats every heading.
**Decision:** `edgar.key_text` works on the page's plain text: a heading starts a line
("Item 2. Management's Discussion", "ITEM 7." with the title on the next line, "Item 1A. Risk
Factors", "Item 2.02"), a section runs to the next item heading or "Signatures", and of several
occurrences the one with the most text under it wins. 10-Q/10-K keep MD&A then risk factors;
an 8-K keeps every item but 9.01 (the exhibit list). Sections share `FILING_TEXT` (the note
call's 12,000 characters) evenly, a short one leaving its room to the others. With no sections
found (or a report's under 500 characters), the text is cut to the cap, never dropped. Filings
are fetched with a 16 MB byte cap (pages keep 2 MB): live main documents measured 1.5 to 16 MB,
and MD&A sits past the financial statements. `page_text` now drops `ix:header` (the hidden
XBRL facts), which otherwise led every filing's text. Checked live on AAPL, JPM, KO, GOOGL,
NVDA, BRK-B and XOM (a network test covers three).
**Alternatives:** Parsing the HTML structure (bold spans, anchors from the contents table:
layouts differ by filing agent); the XBRL financial data API (numbers, not the narrative);
the first occurrence of a heading (that is the table of contents).

## 2026-09-24 - Report pages first for listings outside the US
**Context:** Non-US listings have no EDGAR; their results and annual reports are on the
company's own site, found by the press-release seed and the loop's web searches.
**Decision:** With no filings to read, undated evidence (web results) whose title or path
names results or a report (English plus a few local words such as Geschaeftsbericht,
resultats and kessan in Japanese script) and is not a PDF takes the same 2 first slots, read and noted like an article
without trimming.
**Alternatives:** A dedicated investor-relations search in the stage (another ddgs call per
run, and the seed already makes one); reading PDFs (out of scope for this ticket).

## 2026-09-24 - Research stale listings from the Ledgers tab
**Context:** The AI account skips listings with "no verdict yet" or a verdict too old, and the
user wanted to act on that from the Ledgers tab rather than wait for a tick or open each company.
**Decision:** `stale_research(ctx)` is the tick's due rule (missing, no valid verdict, too old,
or behind a price move; same order), shared by `refresh_research` and a new "Research all
stale (n)" button. The button queues one research job per due listing through
`submit_research`, with no time budget, skipping listings already queued or running here; n
counts only what it would queue. AI decisions carry a stored `needs_research` flag (migration
m0031, older rows backfilled from their reason text) and the page shows a "Research" button
beside such a line only while the listing is still stale, so a line from before its research
offers none. No CLI command: the parity manifest maps CLI to GUI only, and `oracle tick`
already researches stale listings.
**Alternatives:** Matching the reason text in JS (breaks on rewording); computing the flag at
read time from the current due list (would also flag buys behind a price move, which the
reason does not say); queueing one batch job for all listings (one cancel for all, and a
listing already queued by its own button would run twice).

## 2026-09-24 - Deep defaults: 16 steps, 16k context, 12 articles, 120-minute budget
**Context:** Deep research ticket 04: every run reads, searches local and press sources and
reads filings, so the depth numbers become settings with deep defaults.
**Decision:** `[llm] max_steps` 8 to 16, `[llm] context` 8192 to 16384, new
`[research] read_articles` 12 (1 to 30; replaces the `READ_ARTICLES` constant),
`[ai_account] research_budget_min` 30 to 120. All are in the Settings form through the
example file's hints. `MAX_PLANNED` 6 to 8 and `MAX_SUGGESTED` 4 to 5: the plan takes half the
16 steps for every listing (US: 3 seeds + 5 suggestions; Japan: 4 + 4), leaving 8 for pages,
filings and follow-ups.
**Alternatives:** Keeping the plan at 6 (10 free steps the 4B model rarely uses; see the live
run below); a plan of 12 (too few steps left for reading a page the model found).

## 2026-09-24 - Tick time moves to 22:00 with the 120-minute budget
**Context:** At 120 minutes, a tick at 22:30 could still be researching when Tokyo opens at
01:00 UK time.
**Decision:** The user agreed to schedule the tick at 22:00 once the budget is 120, so
research ends by 00:00 to 00:30. `[schedule] time` keeps its code default "22:30"; the user's
own `oracle.toml` sets the time. Their `oracle.toml` also sets `research_budget_min = 30`
explicitly, which overrides the new default until they change it.
**Alternatives:** Changing the code default to 22:00 (every install, not only this user's
Tokyo concern).

## 2026-09-24 - Timed live run at the deep defaults
**Context:** The budget must cover ~25 to 30 watched listings over a night or two.
**Decision:** Keep the defaults. One `oracle research MSFT` on a copy of the real DB, local
Ollama `qwen3:4b-instruct-2507-q8_0` at 16384 context on an RTX 5060 Laptop (8GB): 135 s end to
end. The GPU was otherwise idle (the live GUI was not researching then), so contention would
add to this. Ollama loaded at context 16384 with 5.4GB for the model, 6.7GB used in all, no
errors or fallbacks. Loop: 9 steps (the 8 planned searches, then the answer; no `read_page`),
about 30 s. Reading: 12 picked (2 filings first, then 10 news), about 60 s: both filings read
(the 10-K trimmed to 3,613 characters, an 8-K to 1,383), 3 news pages read, 5 Google News
links not resolved (Fortune, pluang, IBD, Benzinga, Yahoo Finance: no search result on the
outlet's site), 2 resolved but answered 403 (TheStreet, Seeking Alpha). 5 notes (one "not
about the company"), about 3 s each; an unresolved link costs about 5 s of search. Roles and
verdict about 40 s. At ~2.3 min a run, 120 minutes covers ~50 listings; even at twice that
(all 12 readable, GPU shared with the GUI) it covers ~25 in one night.
**Alternatives:** Fewer articles or steps (not needed at these timings); a 32k context (does
not fit 8GB).

## 2026-09-24 - Filings take at most 2 reading slots; extra 8-Ks stay headlines
**Context:** Ticket 03 asked both for the latest 10-Q/10-K plus every 8-K from the last 30
days and for filings to take at most 2 reading slots.
**Decision:** The 2-slot cap wins. The periodic report and the newest 8-K are read; any further
8-K stays in the evidence as a headline.
**Alternatives:** Read every recent 8-K (takes article slots from news on a busy month).

## 2026-09-24 - Reading stage hardening (gate round 1)
**Context:** Review of the deep-research branch.
**Decision:** Any per-article failure is stored as a permanent read or note failure and logged,
never raised. An address or body httpx cannot handle is a tool error. Pages are refused when
their host resolves to a non-global address, checked with `getaddrinfo` before each hop (a
DNS rebind between that check and the connection remains possible; SEC hosts are exempt). A 403
from the `web` source is one page's refusal and never counts toward its breaker. A replay reads
and shows text and notes only from SEC filings. A model's note unusable twice is not asked for
again (backend failures do not count). Citation tokens are stripped from noted facts. An
outlet with no stored site is matched only on a domain's own name label.
**Alternatives:** A connect-time check in a custom httpx network backend (closes the rebind
window, but reaches into httpx's private pool); a separate gate source for the reading stage
with a high 403 limit.
**Follow-ups:** an `Evidence.read` primitive; `Reader`'s 9 constructor parameters; a filing is
fetched again when another model notes it.

## 2026-09-24 - Repaint in place after actions; research buttons follow live jobs
**Context:** After an action the Ledgers tab (and the Company page) blanked to "Loading..." and
rebuilt, so the page height collapsed and the view jumped to the top; research buttons gave no
sign a listing was already queued or running, inviting repeat presses.
**Decision:** One helper, `lib/repaint.js` `repaint(box, nodes, after)`: the caller fetches
first, then the helper swaps the new nodes in one step, holds the box's old height until
`after` (charts, parts that load later) settles, puts the window scroll back and refocuses the
control with the same id or aria-label. Only a tab's first load shows "Loading...". Used by
Ledgers, Company, and the Universe results and Watchlist table (for focus). Each tab keeps
its late-answer guard (Ledgers gained a load counter beside its root check). The Ledgers
research buttons come from `researchLive()`, which polls `/api/activity` every 3 seconds while
the tab is on screen and repaints only those buttons: Queued, Researching... with progress,
then "Researched" once the listing is no longer due (one `/api/ledgers/ai/stale` call when a
job ends). A press flips its button at once, and a poll sent before the press cannot undo it.
The Company page's Research and Replay buttons show Queueing.../Queued/Researching... and
stay disabled until the job ends.
**Alternatives:** Saving and restoring scroll in each tab (duplicated, easy to miss one);
diffing the DOM (a library or a lot of code for a page this size); keeping the old
"Loading..." rebuild but restoring scroll afterwards (still flashes, and scroll restores
before charts are drawn land short); a server push channel for job state (more moving parts
than a 3-second poll of a small endpoint).

## 2026-09-24 - Backtest engine: in memory, technicals-only leans, a capped lookback
**Context:** Ticket backtest/01 replays the strategy's fixed rules over stored history
(`oracle backtest`), with no look-ahead and nothing written to the paper accounts. Chosen
overnight while the user slept.
**Decision:** A pure day loop in `oracle/broker/backtest.py` over each listing's stored bars
and that day's rate; `backtest_service.run_backtest` only reads, so no migration and no run
record (ticket 02 decides what to keep). Each day, after the close, a listing's lean is the
baseline over the technicals signal alone (`price_signals`, now shared with `analyse`), sized
by the risk analyst from its last year of base-currency closes, and the strategy's `Rules`
(`strategy_rules`, shared with the paper accounts) and pure `decide` place orders. Orders
fill at that listing's next bar with an open and a rate, reusing `buy_fill`/`sell_fill` in
whole shares: slippage in bps, a flat commission per trade in the base currency (a new
`Costs.commission`), no FX spread, stamp duty or exchange fees. No orders on the last day.
Signals see the last `YEAR + MIN_BARS` bars, not the whole history: RSI and MACD averages
forget their start well inside that (a test pins the same lean as the full history) and a
long replay stays linear (10 listings over 5 years, about 30 s). The report states the
survivorship bias, that fundamentals are left out and a technicals-only lean tops out at
62.5% (and says so when the threshold is above that, as nothing can then be bought), and the
listings left out. Buy and hold is `compare.buy_and_hold` over the same listings and days.
`--list` defaults to `main` like the watch commands. The parity manifest marks the command
and its flags `Pending` on ticket 02.
**Alternatives:** Full history per day (quadratic: minutes for a few years); the IBKR fee table
and FX spread the paper accounts use (the spec asks for commission and slippage only, default
free, so a run is comparable across markets); keeping runs in a new table now (left to 02).

## 2026-09-24 - Discovery, related source: news first, then universe peers, bounded per run
**Context:** Discovery ticket 01: find companies related to the watched ones and add them to a
`discovered` watchlist unattended, with a small local model and shared rate gates.
**Decision:** Each run takes watched listings (outside `discovered`) as seeds in turn, at most
3 a run, starting after the last run's seed. Per seed: one Google News and one GDELT search,
then one bounded-JSON model call over up to 20 headlines naming companies with a relation and
the headline numbers; then the universe peers (same industry group, else sector, largest
first, up to 10). News candidates come first, as they are what is happening now. A name is
kept only if a headline it cites holds it (legal forms aside), so invented names add nothing.
Names resolve in code: a universe search for the name without legal forms, equities only,
exact name on any of the company's listings first, else one starting with it, largest first;
the listing added is the company's primary line (home or most liquid), else its best live
sibling. Then prices are fetched (the configured sources, Yahoo first) and must be recent. A
run price-checks at most 6 candidates. A failed search or model call is noted and the run goes
on. Runs are single-flight across processes.
**Alternatives:** Asking the model for tickers (small models invent them); peers only (never
finds suppliers or customers); searching every watched company each run (slow through the
gates, and one failing source would stall the lot).

## 2026-09-24 - Discovery: Tesla is never added whatever the setting says
**Context:** The user's rule is never to watch or trade any Tesla listing. `[discovery] never`
defaults to ["Tesla"], but a settings edit could empty it.
**Decision:** "Tesla" is always on the never list in code, beside whatever `never` holds.
Names match as whole words, case and punctuation aside, against the model's name, the
company's name and every one of its listings' names.
**Alternatives:** Trusting the setting alone (one edit away from breaking the rule); matching
substrings (would refuse unrelated names containing the letters).

## 2026-09-24 - Discovery: removals remembered by symbol and company; "Discover now" ignores enabled
**Context:** A company the user removes must never come back, and the Watchlist tab can remove
items from the `discovered` list as well as the Discovery section can.
**Decision:** Any removal from the `discovered` list (Discovery's Remove, the Watchlist row's
Remove, `oracle watch remove --list discovered`) is recorded in `discovery_removed` with the
symbol and company key; a candidate matching either, or any listing symbol of its company, is
skipped. "Move to my watchlist" puts it on `main` and takes it off `discovered` without
counting as a removal; its reasons are kept. `[discovery] enabled` governs automatic runs
(the tick stage, ticket 03); a button press or `oracle discover` always runs. The discovered
list counts as watched everywhere today (prices, analysis, research, and both paper
accounts) until ticket 03 decides what the strategy account trades. Migration m0033 (m0032 is
on the deep-research branch).
**Alternatives:** Flagging removals on the provenance row (misses listings added by hand);
refusing manual runs when disabled (a button that does nothing is confusing).

## 2026-09-24 - Discovery in the tick: a stage before research, capped in time
**Context:** Ticket 03 runs discovery unattended. It uses the network and the model, and it
delays research, which has its own budget.
**Decision:** A `discover` stage between `analyse` and `research`, run only with
`[discovery] enabled` and `[automation] enabled`, and held like research while the PC is in
use (`research_when_idle`). It may run for at most 10 minutes and never more than a third of
`research_budget_min` (fixed, not a setting); at the limit it is cancelled and the summary
lists what it had already added. A failed source makes the stage partial, a crash fails only
the stage, and "Discover now" already running is left to finish. The stage summary is plain
words ("added 6902.T DENSO Corporation (same industry as 7203.T)", or why nothing was added),
and the Ledgers AI panel shows a one-line account of discovery with the last tick's result.
`stale_research` puts the user's own picks before discovered listings, so the tick and
"Research all stale" both research them first.
**Alternatives:** A separate `[discovery] budget_min` setting (one more knob for little gain);
deducting discovery time from the research budget (would couple the two stages).

## 2026-09-24 - Discovered listings: the AI account trades them, the strategy account and buy-and-hold do not
**Context:** The comparison between the strategy and AI accounts should stay fair, and
buy-and-hold is there to benchmark the user's hand-picked list.
**Decision:** Prices, FX, analysis and research cover every watched listing, discovered ones
included. The strategy account decides only over the user's own picks (a listing on any
list besides `discovered`); a discovered listing it already holds stays in so it can still be
sold. The AI account trades discovered listings while `[discovery] ai_trades` (default true,
in the Settings form) is on; off, each is logged as a skip with the reason and the setting,
and a held one can still be sold. Buy-and-hold holds only the user's own picks and names the
discovered ones it leaves out in a note.
**Alternatives:** The strategy account trading discovered listings too (the comparison would
then partly measure discovery, not research); buy-and-hold of everything watched (its
benchmark would move whenever discovery adds a company).

## 2026-09-24 - Discovery sources: related, themes and movers take turns, switched one by one
**Context:** Ticket 02 adds themes the user names and market-mover news beside the related
source, each with its own switch, and a failed source must not waste the run.
**Decision:** `[discovery] themes` is a list of short topics (at most 60 characters each,
trimmed, repeats dropped), edited in the Settings form like `never` (comma separated; a blank
box leaves none, which skips the source). Switches are three booleans, `search_related`,
`search_themes`, `search_movers` (default true), so the form shows checkboxes. The sources
take turns in that order, the turn kept in meta: a run starts with the source whose turn it
is, and when that source fails or adds nothing the next one gets the run, so one run may try
all three. Themes need nothing watched; related and movers do. Movers search three kinds of
news (biggest risers and fallers, unusual trading volume, results surprises) in each country
the user's own watched listings are in, most watched first, two searches a run, rotating;
themes likewise two a run. Themes and movers use one bounded-JSON call per search that names
companies without a relation (`TOPIC_SCHEMA`), then the same resolution, never list, skips and
price check as related. Provenance stores source `theme` or `mover`, the theme or the search
("the biggest risers and fallers (Japan)") as seed, and the kind as relation; no migration.
**Alternatives:** A `sources` list setting (a typed list is easier to get wrong than three
checkboxes); a switch for themes beside a blank list (kept: a user can keep their themes and
pause them); strict turns where a failed source ends the run (a holiday run would add nothing
whenever a feed is down); movers from price data instead of news (needs prices for listings
not yet watched, many requests per run).

## 2026-09-24 - The Discovery section says what it searches and whether the AI account trades it
**Context:** Ticket 03 left the Watchlist tab silent on `[discovery] ai_trades`.
**Decision:** Above the discovered list a line names the sources on (with the themes) and
says "The AI account trades these after research." or "The AI account researches these but
does not trade them." from the setting. The tick's empty summary now reads "no companies
found", since not every source is about related companies.
**Alternatives:** A switch in the section itself (Settings already holds it, one place to
change it).

## 2026-09-24 - Retry for cancelled, interrupted and failed jobs
**Context:** A restart cancelled 8 queued research jobs at once with no way to redo them
short of re-entering each request by hand. The Activity tab needed a way to retry a single
finished job and to retry everything left in a bad state after an outage.
**Decision:** One mapping in `oracle/services/activity_service.py` (`_RETRY`) from job kind
to a resubmit function that rebuilds the original call from the job's stored kind, subject
and label; `retry(job_id)` looks a job up (live or from history via `JobRunner.get`) and
refuses anything not cancelled, interrupted or failed, or a kind not in the mapping.
`retryable(ctx, runner)` reads `JobHistoryRepo.latest_per_subject()` (the newest finished
row per kind+subject) and keeps only rows in a bad state, not already queued or running, and
of a retryable kind; `retry_all` resubmits each one. A subject's older bad runs are never
retried individually: only the latest matters, since anything before it either already
reran or is superseded by the one that matters now. A research replay's subject
(`SYMBOL@day`) carries its day; a tick always retries as manual, regardless of what
triggered the original; prices, analyse and fx tell "every listing" apart from one listing
by the same `*`/name subject `submit_*` already stores. Routes: `POST
/api/activity/{job_id}/retry`, `POST /api/activity/retry-all`, and `GET
/api/activity/retryable/count` for the button's badge. `activity.js` adds a Retry button
next to Cancel (mutually exclusive by state) and a "Retry all (n)" button by the Finished
heading, both refreshing the finished list and the count in place, no page reload.
**Alternatives:** Storing the original submit arguments verbatim (a new column, more to keep
in sync with every `submit_*` signature) instead of rebuilding from kind+subject, which
already uniquely determines the call for every kind in this app; retrying every past bad run
for a subject instead of only the latest (redundant, and confusing when an older failure was
already fixed by a later success); a kind allow-list hard-coded in the route instead of the
resubmit mapping doubling as the "can this be rebuilt" check (one source of truth instead of
two lists to keep in sync).

## 2026-09-24 - Backtest in the GUI: a job, the last 10 results kept in meta, days inside the stored prices
**Context:** Ticket backtest/02 puts the backtest on the Ledgers tab. A long run takes about
30 s, so it cannot answer a request directly, and the user wants to compare runs later.
Chosen overnight while the user slept.
**Decision:** `POST /api/jobs/backtest` queues a `backtest` job (`submit_backtest`); the same
request pressed twice joins the first, and a retry rebuilds it from the subject
(`list|start|end|slippage|commission`, split from the right). The request is checked before
queueing (`check_backtest`, shared with the CLI): the first day before the last, costs zero
or more, and both days inside the first and last stored price of the list's listings
(`stored_span`); the form starts on that span a year in, since each lean needs a year of
earlier prices. The job keeps its result (`keep_backtest`), and only the newest 10 are kept,
as JSON rows in the `meta` table under `backtest_run:<id>`, with the base currency of the run.
The job's result is a summary; the page fetches the whole run from `/api/backtests/{id}`, so
Activity never carries chart points. The section is its own module (`lib/backtest.js`): one
node per tab render kept across the tab's repaints, so the form keeps what was typed; results
repaint in place. The comparison chart and table moved to `lib/compare.js`, shared with the
accounts' comparison. The replay never yields, so a Cancel lands after it, before the result
is kept.
**Alternatives:** A `backtest_runs` table in a new migration (m0034 was suggested to leave
m0033 to discovery). Not taken: a migration's version is its place in `MIGRATIONS`, not its
file number, so a database migrated with this branch first would skip discovery's m0033 and
fail on re-running this one; `meta` needs no schema change. A proper table can follow once
discovery has merged. Keeping results only in the in-memory job list (lost on restart);
clamping out-of-range days instead of refusing them (hides the mistake).

## 2026-09-24 - Market index lines in the Ledgers comparison
**Context:** The comparison only showed the three accounts and buy-and-hold of the
watchlist; there was no reading of how the accounts do against a plain market index like the
world or the S&P 500, and no way to add or change which indexes without a code change.
**Decision:** A new `[compare] benchmarks` setting (default `["VWRL.L", "VUSA.L"]`,
Vanguard FTSE All-World and Vanguard S&P 500, both London-listed and GBP-priced so a GBP
base currency reads them with no FX noise), edited through the existing generic settings
form (`oracle/config.py`'s `CompareConfig`, walked by `setting_list` and validated in
`_apply` like every other section). Benchmarks are priced alongside the watchlist:
`prices_service.update_watched` prices watched listings then any benchmark not already among
them, but a benchmark unresolvable in the universe is recorded with `ListingOutcome.skipped`
("not in the universe: benchmark skipped") rather than `.error`, so it is never a "failed"
listing and never turns a tick "partial" just because an index has not been added to the
universe yet. Benchmarks are never added to any watchlist, so `strategy_step`, `ai_step` and
research (all driven off `WatchlistRepo.all_symbols()`) never see them: nothing extra needed
to keep them untraded. `oracle/broker/compare.py` gains `benchmark_return` (a single symbol's
own close series as returns since a start day, no split across listings, unlike
`buy_and_hold`); `paper_service.comparison()` appends one `Line` per configured benchmark,
its bars read by `prices_service.benchmark_history_in_base` (a new function sharing the FX
conversion `price_history_in_base` already did, factored into `_bars_in_base`, but reading
`BarRepo` by symbol directly instead of through `resolve_symbol`, since a benchmark need not
resolve in the universe to have bars). `Line` gains an optional `label` field (universe
company name, else a nice fallback for the two defaults, else the symbol) that the GUI and
CLI already carry through generically (`asdict`). `ledgers.js`'s `LINES` map stays fixed for
the four known lines; a benchmark's label and one of two new `--bench1`/`--bench2` CSS
colours are assigned per line at render time in the new `lineInfos()` helper, dashed like
buy-and-hold (another line held, not traded).
**Alternatives:** Requiring a benchmark to already be a valid, resolvable listing (simpler,
but would silently drop the shipped defaults for anyone whose universe has not been refreshed
yet, with no visible reason); carrying the FX/company logic in `oracle/gui` instead of the
label living on `Line` (would break the cli/gui-only-imports-services layering, and duplicate
the label logic between the CLI and the GUI); a static per-symbol colour map in `ledgers.js`
instead of assigning by position (would silently run out of colours, or collide, once a user
configures a third benchmark).

## 2026-09-24 - Held positions are never stranded; one never-list check everywhere
**Context:** Review of discovery found a listing removed from every list (discovered, or the
user's own `main`) while an account held it dropped out of both pricing and the accounts'
decisions, so a stop could never fire on fresh prices. Main already had this for removed
watched listings. The never list was only enforced inside discovery runs.
**Decision:** `traded_by` is the listed picks plus whatever the account holds; the rules
only buy what is not held, so a held listing can be sold but never added to.
`update_watched` also prices every listing any paper account holds (held but gone from the
universe is skipped with a note, like a benchmark). A new `oracle.services.never_list`
holds `ALWAYS_NEVER` (Tesla) and one company-wide check over every listing's name:
`watch()` refuses Tesla on every list and `[discovery] never` names on the discovered list;
`place_buy` refuses Tesla on every account; both trading accounts skip a buy of Tesla
anywhere, or of a `[discovery] never` company that only the discovered list holds. A company
moved from discovered to the user's list and later removed is remembered as removed.
Discovery's name lookup takes a leading-name match only when exactly one company matches,
never for names under 4 characters, and falls back to an exact symbol ("Meta" is META).
**Alternatives:** Pricing held listings through a hidden watchlist (would leak into every
list view); refusing the whole never check in `decide` (the broker layer has no universe).

## 2026-09-24 - Discovery follow-ups left open
**Context:** Items from the discovery review not fixed now.
**Decision:** Recorded as follow-ups: (B4) headline injection, where a crafted headline
names a company to get it discovered, is bounded by `per_tick`, by the rule that a name must
appear in a headline it cites, and by the code-side universe and price checks, but not
removed; (B6) the price checks store bars for candidates later rejected, which costs a
little database space and nothing else. B5 (CDR, BDR and CEDEAR receipts not filtered) was a
one-line regex widening and is fixed.
**Alternatives:** Fixing B4 with a second model pass (costly, and still model-judged);
checking prices without storing (would need a separate fetch path).

## 2026-09-24 - Held-but-unwatched listings are analysed too; AI-held unlisted ones are
stop-only
**Context:** `update_watched` already prices every listing a paper account holds, off every
list. `analyse_watched` did not: it only ran over the watchlist, so an unwatched-but-held
listing's lean went stale the moment a new bar arrived, and `_usable_leans`/`_usable_verdicts`
refuse a stale lean, leaving the stop as the only exit.
**Decision:** `analyse_watched` now covers watched listings plus `held_unwatched` (the same
helper `update_watched` uses, moved into `prices_service` for both to share). Separately, for
a listing a paper account holds that has left the universe entirely (not just its lists):
the strategy account trades on baseline leans and would still sell it on a sell lean once
`analyse_watched` reaches it, same as any held-but-listed symbol. The AI account is
different: `ai_step` decides from the newest live research verdict (`_usable_verdicts`), not
from a lean, and `_usable_verdicts` skips a symbol outright when it fails to resolve in the
universe (reason `GONE`), so no verdict-based sell decision is ever produced for it. An
AI-held listing gone from the universe therefore only ever exits on the stop; a sell verdict
can never apply to it, because none is looked up. This is left as is (a listing gone from the
universe has nothing left to research a verdict against).
**Alternatives:** Having `ai_step` fall back to the baseline lean for a delisted symbol
(would trade the AI account on baseline in a rule it otherwise never uses).

## 2026-09-24 - The midday pass: a second schedule slot for discovery and research only
**Context:** Discovery 06 wants a daily slot (`[schedule] midday_time`, "12:30", "" off,
`midday_budget_min` 60) that runs discovery, then research on stale listings, and never
prices, steps or trades, without a migration.
**Decision:** The pass reuses the tick's `discover` and `research` stage functions with a
shared budget: discovery gets `discover_cap_min` over the midday budget (at most 10 min, a
third of it), research gets what is left from the pass's start. It runs under the tick's
lock and job queue (kind `tick`, subject `midday`, label "Midday pass (...)"), so it never
overlaps a tick and a tick due during it waits behind it. Runs are stored in `tick_runs`
with the trigger written as `midday/<trigger>`; `TickRun.kind` reads it back, and "last
tick", catch-up and "ticked since" count ticks only, so a midday pass never hides a missed
tick. The slot needs scheduling (`[schedule] enabled`) and automation on, shares the
warning, postpone and skip (on whichever run is soonest), and never catches up: a pass not
started within 30 minutes of its time (or its postponed time) is missed, not fired late.
The grace is checked again as the queued job starts, and a restart inside the grace still
runs it. A pass missed while the app was running is stored as skipped ("missed: ...") so the
Ticks panel says why; one missed while the app was closed leaves no record. Each slot keeps
its own floor, so changing one never drops the other's postponed run. A midday time equal
to the tick's is refused when saved, but on load it only turns the midday pass off (with a
logged warning), so a file already set to time = "12:30" still starts. A pass already queued
or running is not offered for postpone or skip; the banner moves on to the next run.
Research still needs the AI account on, as in a tick; research_when_idle holds both stages.
**Alternatives:** A `kind` column (needs a migration, which the ticket rules out); a separate
job kind and lock (a midday pass and a tick could then run at once and fight over research);
firing a missed pass whenever the PC wakes (the user wants midday work at midday, not in
the evening).

## 2026-09-24 - Discovery chat: a Discover tab, follow-ups as linked questions, whole answers kept
**Context:** Ticket 04 asks for a question box that answers with a shortlist, follow-ups that
refine it, and reruns that show what changed.
**Decision:** A Discover tab of its own (the Watchlist tab's Discovery section is already long).
m0034 keeps `discovery_questions` (a follow-up is a question with `refines`, so each keeps its
own answers), `discovery_answers` (status, stage for progress, searches, notes) and
`discovery_answer_items` (the whole shortlist, skipped names with why included, evidence ids as
JSON). The model reads the whole chain ("X Follow-up: Y"). "New" and "gone" compare an answer
with the question's previous done answer, or for a follow-up's first, its question's newest.
Watched and discovered marks are the live state when the answer is shown, not stored. Asking is
an Activity job of kind `ask` (subject = question id, so Retry reruns it); the question is saved
at submit, so a bad one is refused before a job starts.
**Alternatives:** Follow-ups as runs of one question (loses which text each answered); storing
only the shortlist (a rerun could not say why a company dropped off).

## 2026-09-24 - Discovery chat pipeline: plan, research tools, reading, one naming call, code screen
**Context:** The ticket asks for model-planned searches, the deep-research reading stage, and
code-side resolution with the never and removed lists.
**Decision:** One bounded-JSON plan call gives up to 4 English queries (each run on Google News
and GDELT through the research tools and their gates), up to 2 local-edition queries with a
country from `EDITION`, the first 2 queries on the web, and filters: market-cap bands and
excluded sectors (matched in code against sector, industry group and industry, singular or
plural). A plan with no usable query searches the question itself. Items are taken a search at a
time (at most 24); the reading stage reads 4 of them, keeping notes under `<model>#question` so a
question's "not about" never marks an article for a company's research. One naming call returns
companies with a why line and item numbers; a name no cited item holds is dropped (as in 01).
The screen is 01's, moved into `candidate_screen.Screen` and shared: never list and removed
companies are left off with the reason, filters next, watched and discovered companies stay on
(marked, not re-priced), others are price-checked (at most 8 an answer). Rank: most items behind
a name first, the model's order among equals. The model failing to plan or name fails the
answer (saved with the error): unlike a discovery run, a question has nothing to fall back on.
**Alternatives:** Running the full ReAct loop per question (slow on a 4B model, and its findings
are prose, not a list); ranking in the model's order alone (small models list in headline order).

## 2026-09-24 - Add to discovered from a shortlist keeps the discovered list's rules
**Context:** A shortlist button puts a company on the list the AI account trades.
**Decision:** `discovery_service.add_by_hand` refuses a never-listed company, one the user
removed, one already watched or discovered, and a full list (`max_total`), with the reason. It
records source `question`, relation `answer`, the question text as seed and the item's evidence,
so the Discovery section says 'an answer to your question "..."'. Watch and Research reuse
`/api/watchlists/main/items` and `/api/jobs/research`.
**Alternatives:** Letting a click go past `max_total` (the cap bounds what the AI trades, whoever
adds); refusing watched companies from the shortlist (they are still answers; they are marked).

## 2026-09-24 - Discovery chat follow-ups left open
**Context:** Found while building ticket 04.
**Decision:** Recorded, not fixed: the name lookup misses hyphenated names written without the
leading "The" ("Coca-Cola" does not find "The Coca-Cola Company"), shared with 01; the plan's
region is only searched, never enforced in code (a "European" question can list a US company if
the news names it); questions cannot be deleted; the naming call sees at most 24 items.
**Alternatives:** A country filter from the plan (needs a region-to-country map).

## 2026-09-24 - Discovery chat gate round 1 fixes
**Context:** The ticket 04 gate found sector excludes that missed ("oil and gas") or overreached
("tech" in Biotechnology), notes shared across questions, outages saved as answers, and a
discovered-list add that could race a tick.
**Decision:** An exclude now matches whole words of the sector, industry group or industry, with
filler words ("and", "sector", "companies") dropped and plurals folded only on words over 4
letters. Notes are keyed per root question (`<model>#q<root id>`), so a follow-up reuses its
root's notes and no other question does. An answer where every search failed fails with "no
search could run" (`SearchFailed`, 502) and is never compared against. Adding to the discovered
list (a button or a tick) re-reads watched, listed, removed and room inside the insert's write
transaction; a button add also re-checks prices first and quotes the whole question, follow-ups
included. A discovered company on a shortlist offers Move instead of Watch. Names are checked
against an item's title and facts, never its outlet. Only bracketed numbers that are item
numbers are stripped from a why line. The shortlist is numbered 1..n with left-off names after it.
**Alternatives:** An enum of the universe's sectors in the plan schema (exact, but a question
naming an industry below the group level, "gas", would have nothing to pick); notes under a
question-neutral prompt (more reuse, but "not about" would lose its meaning).

## 2026-09-24 - Question answers and tick research share the local model
**Context:** Gate item 4: a question answered while a tick researches runs two Ollama loads at once.
**Decision:** Left as a follow-up: they hold different job locks, so both proceed, only slower.
**Alternatives:** Making questions take the research lock (a question would then wait up to the
research budget, or defer the tick's research as manual research does).

## 2026-09-24 - Discovery chat excludes need every word, with everyday aliases
**Context:** Gate round 2: one shared word excluded unrelated fields ("consumer staples" dropped
Consumer Discretionary), and everyday words ("tech", "chipmakers") matched nothing while the
note still claimed to leave them out.
**Decision:** Every remaining word of an exclude must be among a field's words (after filler
words and plural folding), with a small alias map first: tech, pharma, biotech, insurer,
chip/chipmaker to the universe's words. The answer says "leaving out X" only for an exclude that
matches some sector, industry group or industry the universe holds, and says an unmatched one was
not recognised.
**Alternatives:** Any shared word (too loose); a model-chosen enum of the universe's names (loses
industries below the group level).

## 2026-09-24 - Stored company links (discovery 07): what is kept, and deletes are per company pair
**Context:** Ticket 07 keeps the company links discovery finds and feeds research. Only one
candidate per run is added, but the extraction call names up to 8 related companies.
**Decision:** m0035 adds `company_links` (symbol, other, relation, source, first/last seen,
deleted_at; "other is relation of symbol") with `company_link_evidence`, plus
`research_queries.via`. Discovery's related source records every news-named company that
resolves (not only the one added), and a peer only when it is the one added (the universe
already lists every peer; storing ten per seed would bury the evidence-backed links). Themes
and movers have no seed company, so they record nothing. `links_service.record_links(ctx,
[LinkFound(symbol, other, relation, evidence)], source)` is the one writer, for the chat (04)
too: it skips self-links, unknown listings and the never list (Tesla), and any pair the user
deleted. A delete marks every link between the two companies (any listing, either way round,
any relation) and none is recorded again, whatever evidence finds it later; a wrong link is
usually a wrong name resolution, so relation-level deletes would let it back in. So reading
matches deleting, `company_links` gathers every stored link between the two companies into
one entry (its relations listed, evidence merged), and drops any company on the never list
as it stands at reading time, not only at recording.
**Alternatives:** Deleting one relation only; letting new evidence revive a deleted link
(the same story reappears daily, so a wrong link would keep coming back); company keys in
place of symbols (keys change on a universe refresh; symbols are matched across all the
company's listings instead).

## 2026-09-24 - Research reads linked companies through two planned searches
**Context:** Research should read its strongest linked companies within the existing plan cap.
**Decision:** `research_links` picks up to `MAX_LINKED = 2` linked companies with evidence
(most articles, then newest; one link per company; in a replay only links first seen by its
day). Each becomes a seed search "<linked short name> <company short name>" (both names, so
the articles bear on the company researched and the reading stage's "about the company" note
holds). Seeds are always kept and suggestions fill up to `MAX_PLANNED = 8`, so links take
suggestion slots, never add loop steps. The search carries `via` (e.g. "DENSO Corporation
(6902.T), a supplier"), stored with the planned query. `research_evidence.query` (m0035) keeps
the search that showed each article to that run (an article's own `query` is the first ever,
often discovery's or another run's), and an evidence item shows the link when that search is
one of the run's planned link queries. A replay ranks and shows links by evidence published
by its day only, with last seen capped at its day. `related_companies` lists
stored links first (why, article count, last seen), then universe peers minus those companies.
**Alternatives:** A separate reading slot for linked articles (the reader already reads what
the loop was shown); tagging evidence by the tool call (a model that rewords the query loses
the tag either way; matching the planned query needs no new plumbing in the tools).

## 2026-09-24 - The discovery chat keeps a link only when an item states one
**Context:** Ticket 07 wants links from the chat too, but a chat answer names companies that fit
a question, not companies related to a seed: its naming call returned no relation at all.
**Decision:** The naming schema gets two optional fields per company, `related_to` (another
company) and `relation` (supplier, customer, competitor or partner; never "other"). The prompt
asks for them only when a cited item says so. Code keeps the pair only when one line of a cited
item (its title, or one fact bullet) names both companies, and not the company itself; only those
items are the link's evidence, so an item that never names the other company cannot inflate its
rank. Both names resolve through the screen's plain lookup (no shortlist rules, but only a
company's own shares: no funds or depositary receipts), stored as primary listings, and
`record_links(..., source="chat")` applies the never list and deleted pairs. Links are kept whatever the question's filters leave off: the item states a fact
about the two companies, not an answer to the question. The answer's notes say how many were kept.
**Alternatives:** Linking every shortlisted company to the others (invents relations); linking
them to the question's subject (a question has no company to link to); a second model call per
answer to extract relations (more model time for something most answers do not state).

## 2026-09-24 - Company chart gets a 1y/2y/5y/All range picker
**Context:** The Company tab's price chart always fetched `days=365`, ignoring the `/prices`
route's 5000-day cap.
**Decision:** A small `chart-range.js` lib holds the pure range table (`1y`/`2y`/`5y`/`All`,
`All` resolved to the route's cap) so it is unit-testable without the DOM. `company.js` keeps
the picked range in a module-level variable (default 1y, kept while browsing) and a `<select>`
next to the chart; changing it re-runs only `drawChart`, not the whole company reload.
**Alternatives:** A row of toggle buttons (no existing button-group pattern in the codebase to
match; a `select` matches how other tabs already offer a days/range choice, e.g. `fx.js`).

## 2026-09-24 - Backtest default window caps at 5 years instead of growing unbounded
**Context:** The Ledgers tab's Backtest form defaulted `from` to a year after the first stored
day and `to` to the last, so a watchlist with many years of prices got a default window that
grew without bound instead of "as long as stored bars allow, up to 5 years."
**Decision:** `loadSpan` now picks `from` as the later of (first stored day + 1 year warmup) and
(last stored day - 5 years), still falling back to the first day when there is under a year of
data. The date inputs were already free-text, so a shorter period is still one edit away. The
CLI's `backtest` command takes `--from`/`--to` as required arguments with no default, so it is
left alone.
**Alternatives:** Capping at 5 years from the first day instead of the last (would show a stale
window on a long-lived list instead of the most recent data); adding a separate period selector
(the date inputs already do this job).

## 2026-09-24 - A discovery exclude leaves out its most specific match only
**Context:** An exclude matching a combined industry group dropped the whole group: "biotech"
left out Pharmaceuticals (group "Pharmaceuticals, Biotechnology & Life Sciences"), "media" left
out Interactive Media & Services, "tech" matched Health Care Technology.
**Decision:** `UniverseRepo.classifications()` returns the universe's industries, groups and
sectors by level. An exclude becomes one `Exclusion` (level + values): the most specific level
with a classification named exactly the exclude's words, else the most specific level with one
holding all of them, else nothing. Only that level of a company is tested. "tech" alone means
Information Technology; aliases healthcare -> health care, automaker/car(s) -> automobile.
**Alternatives:** Industry level first with plain word containment (the brief's rule: "media"
would still leave out Interactive Media, since its name holds the word; "utilities" would
match the Multi-Utilities industry and keep power producers); a model-chosen enum.

## 2026-09-24 - Auto questions add only what passes a validation layer (discovery 05)
**Context:** Ticket 05: a per-question auto switch that adds shortlisted companies to the
discovered list unattended, but only when they pass every check; the rest stay suggestions.
**Decision:** m0036 adds `discovery_questions.auto`, `discovery_answer_items.validation` and
`discoveries.question_id` + `validation` (JSON: checks with name, passed, detail; added; note).
`auto_validation.Validator` runs, cheapest first and stopping at the first failure: resolved
(symbol still resolves to the shortlisted company key), line (the primary or the most liquid
live line, as the screen picks), prices (stored bars not stale), never list, removed, receipt
(the line or company is a depositary receipt, or a watched company of another key has the same
legal-form-blind name), evidence (items naming the company, dated within 90 days, from at least
2 outlets; outlet = the feed's outlet, else the URL's domain), relevance (a separate call,
"Role: relevance_check", given only the question, the company and the cited items; yes/no + a
reason; "no", garbled, blank reason or a model error fail it). Filings count: the company's SEC
filings already stored (any listing's symbol, last 90 days) join the evidence check with outlet
"SEC"; relevance still reads only the cited items. The counted items become the discovery's
evidence. Adds go through `add_checked` (rules re-read in one transaction) with source
"question", relation "auto", seed = the whole question.
Caps: `[discovery] per_question` (default 2, 1 to 20) is how many of the question's companies
are on the discovered list now (moved or removed ones free a place), plus `max_total`. Once a cap
is reached the rest are not validated (no model calls) and say so. Watched and discovered
companies are left alone. Turning auto on does not act on answers already given: the next Rerun
or midday pass does.
Midday pass: a "questions" stage between discovery and research answers up to 3 auto questions
again, longest since answered first, under discovery's switches (discovery on, automation on,
PC idle) and discovery's time cap (min(10 min, budget/3)); a full list or a question already
being answered asks nothing. The nightly tick does not rerun them.
GUI: an Auto checkbox per question (`PUT /api/questions/{id}/auto`), an "auto" badge in the
list, each shortlisted row's validation line (added, or the failed check with its detail), and
the discovered list's folded "Passed N checks". CLI: `oracle discover auto ID on|off`, and the
answer printout shows each company's validation.
**Alternatives:** A per-run cap (unattended daily reruns would fill the list from one question);
counting every company a question ever added (a removal would never free the place); running
auto questions on the nightly tick too (it already has a discovery stage and a longer research
queue); fetching filings live during validation (network per candidate; stored filings cover
companies research already read); an auto flag given at Ask time (one more switch; Rerun after
switching does the same).

## 2026-09-24 - Auto validation hardened (discovery 05 gate round 1)
**Context:** The gate found one outlet passing the 2-outlet rule under two spellings, a relevance
call open to injected headlines, a twin check blind to discovered and removed companies, and a
cancelled answer losing its validations.
**Decision:** Outlets are keyed by the site's registered name (`research.web.site_name`: the feed's
own site, else the link's host, subdomains and suffix dropped; a Google News item with no site
falls back to its outlet name, filler words dropped), so "Reuters" and uk.reuters.com are one.
A normalised title counts once. Newswire hosts are one outlet, "company releases"; SEC filings
are "SEC filings"; at least one counted outlet must be neither. Relevance: items are quoted
between `<item N>` tags (tags inside an item are stripped), the prompt says they are untrusted
and never obeyed, the schema asks reason, items, then answer, and only the cited items the
evidence check counted are shown, so a yes must name at least one of them by a valid number or
it fails. The twin check covers every list (discovered included) and the removed list, by
symbol and blind name. An answer saves its shortlist and validations even when it fails or is
cancelled, and notes what it added. The midday questions stage has its own cap (min(15 min,
budget/3)): no company is validated after it, no question starts with under 5 minutes (a typical
answer) left, and a hard stop one typical answer later reports what was added from the list
itself. Auto is re-read before each validation and each add; `add_checked` re-reads
`per_question` in its transaction. Exclude fallback: after exact names, a sector or group whose
name starts with the words (so "consumer" is both Consumer sectors), then any name holding them.
**Alternatives:** A public-suffix list (a dependency for the few country suffixes seen); counting
releases as independent when on two wires (every wire copy is the same company speaking).

## 2026-09-24 - Quant basics as a pre-v1 stream

**Context:** The rule backtest made +2.6% against buy-and-hold's +20.2%, and there is no simple well-known baseline to show whether LLM research adds anything. Phase 4 already has volatility sizing, drawdown, pairwise correlation and peer value/quality/growth scores.
**Decision:** Add `quant-basics` at the start of phase 9. It covers 12-1 momentum and low-volatility ranks across the watched and discovered set, portfolio beta, concentration and correlation per account, one factor/risk line in the AI verdict input, volatility-targeted AI sizing, and a monthly-rebalanced momentum baseline account (also in `oracle backtest`). Fundamentals factors enter backtests only once stored daily snapshots are deep enough.
**Alternatives:** Trained return models, regime detection and optimisers now (overfit at this data depth; moved to v2). Skipping it (phase 12 would lack a factor baseline).

## 2026-09-24 - Replay study before quant basics

**Context:** Evidence of an edge is the top priority, and months of paper trading are slow. Research replays at past dates already exist, and the model's claimed training cutoff is 2025-05-20.
**Decision:** Add `replay-study` ahead of `quant-basics`: a cutoff probe of the model in use, ~300 paced replays (about 20 listings, monthly dates after the measured cutoff), each verdict scored against 1- and 3-month forward returns, plus a simulated AI account against buy-and-hold and later the momentum baseline. The report states its limits (filings and headlines only, survivorship, sample size).
**Alternatives:** Paper trading only (months to a first read). Replays before the cutoff (look-ahead). Every listing monthly (too slow on the local model).

## 2026-09-24 - Earnings 8-Ks read their press exhibit

**Context:** An earnings 8-K's main document only points at Exhibit 99.1, and reports without "Item N." headings (Citigroup's) were read from their cover page.
**Decision:** When an 8-K has Item 2.02, 7.01 or 8.01, the reading stage fetches its EDGAR filing index (`sec_edgar` gate, contact User-Agent), takes EX-99.1 (EX-99.01 counts) or else the first EX-99.x from the filing's own folder, and stores the exhibit labelled with its type, followed by the 8-K's other items and any press item with 500 characters of its own past its heading, the pointer at the exhibit and the "not deemed filed" legend, within the usual cap. SEC trouble on the index or exhibit (open breaker, 429, transport error) is a passing failure, so a later run reads the exhibit; a permanent miss (no EX-99, 404, not a page, a thin page) falls back to the 8-K's own items. A 10-Q/10-K without item sections is read from its first MD&A, executive summary or results heading that has prose within five lines (not the contents entry), else from the first paragraph past the last cover-page "check mark" line.
**Alternatives:** The exhibit alone (drops officer changes and other items filed alongside). Finding exhibits in the full submission `.txt` (several MB per 8-K). Keeping the start-of-text fallback (the note model saw only the cover and contents).

## 2026-09-24 - Model profile shape

**Context:** Ticket 01 of the model-profiles work: named profiles now, slots and more providers later.
**Decision:** `[models.<name>]` tables with provider, model, base_url, context, timeout_s and one of `key_env` (an environment variable name, shown) or `key` (the key itself, never shown or logged). Two fields rather than one ambiguous `key`, so a literal key can never be mistaken for a variable name. Unset keys take the provider's defaults (`PROVIDER_DEFAULTS` in config: Ollama's URL and 16384 tokens; Claude's own endpoint and 200000). `[llm]` is the implicit `default` profile (backend `claude` maps to `claude_api` with `claude_model`); a `[models.default]` table replaces it wholesale, and `max_steps` stays in `[llm]`. Profile names are letters, digits, `_` and `-`, unique ignoring case. Profiles are kept as set (None where unset) and resolved against the provider only when used, so a provider switch never carries the old provider's URL or context over; the service also drops carried-over values on a switch, and keys on a provider that takes none (`ollama`). A `key_env` that looks like a key (known prefixes, long mixed case) is refused without echoing it. A run resolves its profile once (`resolve_model` gives the model and its context together), so a live edit mid-run cannot resize later stages; ticket 04 extends that one point per slot. Every model is built from a provider-to-builder table, so a new provider is one `PROVIDERS` entry plus one builder. Test is refused (409) while research or discovery holds the model. Profiles apply live (a `models` live section); editing the default in the GUI writes `[models.default]`. A profile whose `key_env` is unset fails at build with a plain message rather than falling back to another credential. An invalid TOML file now loads as a ConfigError (duplicate tables included), not a traceback.
**Alternatives:** One `key` field read as an env name when it looks like one (ambiguous). Editing the default back into `[llm]` keys (no room for a key, and two shapes to map). Profiles in the generic Settings form key by key (no add, delete or test).

## 2026-09-24 - openai_compatible provider

**Context:** Ticket 05: one provider for any OpenAI-style chat completions server (LM Studio, vLLM, llama.cpp, OpenRouter, OpenAI).
**Decision:** Plain HTTP on the app's transport, no SDK. `base_url` is required (no default): defaulting to OpenAI's paid endpoint could send research and a key somewhere the user never chose, and local servers are the point. It is the API root (`.../v1`); a bare host gets `/v1`, a URL already ending in `/chat/completions` is used as is. The key is optional and has no fallback: only the profile's `key` or `key_env`, never `OPENAI_API_KEY`, so a key goes only to the host its profile names (redirects are not followed). `Provider` gains `keyed` (takes a key at all) and `base_url_required`; `fallback_key` now only describes a lookup (None: goes without), and a keyed profile with no key shows as "no key". Replies ask for a strict `json_schema` response_format (the schema closed as for Claude, `closed_schema` now shared in `llm.py`) at temperature 0. A 400/422 naming temperature drops it (reasoning models take only the default); any other 400/422 retries once with `json_object` and the schema appended to the first system message. Both are remembered on the model instance; if json_object is refused too, the error says so and the next reply tries the schema again. Answers are read from `content` (list parts joined, `<think>` blocks and code fences stripped, else the widest `{...}`), falling back to `reasoning_content` or `reasoning` when content is empty; a `refusal` is no answer. Context defaults to 16384 like Ollama; it only sizes prompts, the server's own window must be at least that.
**Alternatives:** The `openai` SDK (a dependency for one POST). Defaulting base_url to api.openai.com, or reading OPENAI_API_KEY only for that host (hidden credential lookup). Checking the error text for "response_format" before falling back (servers word it differently).

## 2026-09-24 - openai_compatible hardening (gate round 1)

**Context:** Review of the openai_compatible provider: key leaks across providers and hosts, unbounded replies, loose error matching.
**Decision:** A saved literal key is kept on edit only while the provider stays the same; the GUI also clears key_env and key on a provider switch. A key (any provider) is refused over plain http:// except to this machine (localhost or a loopback IP); base_url may carry no user, password, query or fragment, and errors show it without them. Replies are bounded twice: `asyncio.wait_for` makes timeout_s wall-clock (httpx times each read), and an output cap is sent, `max_tokens` 8192 (8x Ollama's 1024, room for a local model thinking in its content) then `max_completion_tokens` 16000 (Claude's thinking budget) when the model refuses the first, then none; each swap is remembered like the temperature drop. A parameter counts as refused only when the error's `param` names it or its message has it within 60 characters of "unsupported / not supported / does not support / only the default", never from the rest of the body. A malformed 200 reply (wrong types anywhere) is a ModelError, never a crash that loses the run.
**Alternatives:** Guessing reasoning models by name to pick the cap (breaks on every new model). Warning instead of refusing a key over http (a LAN server with a key would still leak it).

## 2026-09-24 - openai_compatible output cap sized to the prompt; lenient load of old profiles (gate round 2)

**Context:** A fixed 8192-token cap overflowed the context the prompt budget fills (it reserves only 1024 for the reply), and the new base_url rules could stop an unattended app from starting over one old profile.
**Decision:** The cap is sized per request: the profile's context less the prompt estimated as the budget cuts it (3 characters a token, schema included) and a 256-token margin, at most 8192 and never below the budget's 1024. A 400 about the context (maximum context length, too large, "N in the completion", exceeds the context) is a cap problem, never a format one: that reply is resent uncapped, and if it still does not fit the error says to set the profile's context to the server's window. The cap is never raised; `max_completion_tokens` is only a rename, with the same value, for a model that calls `max_tokens` unsupported. At load, a profile breaking the base_url rules (user, password, query or fragment; a key over plain http beyond this machine) is kept, marked `unusable` with a warning, shown in Settings, and refused when a model is built; a save from the form or CLI still refuses it outright. In the form, switching back to a profile's own provider restores its values and key_env.
**Alternatives:** A smaller fixed cap (starves reasoning models on short prompts). Failing the whole config at load (one old profile stops the app). Dropping the offending profile silently (a slot naming it would fail without saying why).

## 2026-09-24 - Reply room per model; cut-off replies named (gate round 3)

**Context:** With prompts filled to leave 1024 tokens, openai_compatible's sized cap fell to 1024 on full prompts, where a reasoning model can spend it all thinking and the run ended "unusable" with no reason.
**Decision:** Each ChatModel names `reply_tokens`, the room every prompt leaves for its reply; the budget (`budget`, `note_room`, `digest_room`, the verdict) reads it through `reply_room(model)`, which gives scripted models the default. Ollama and Claude keep 1024, so their budgets are unchanged; openai_compatible keeps 4096. A reply that stops at the cap (`finish_reason` "length") without parsing is a ModelError naming the cap and saying to raise the profile's context. The key-over-http rule also covers a provider with a fallback key (claude_api with neither key nor key_env still sends ANTHROPIC_API_KEY or its login). "Too large" counts as a context error only within 60 characters of "tokens", so a grammar or schema too large still gets the json_object fallback.
**Alternatives:** Shrinking the context handed to a run instead (the run records and slots read that number as the model's). A larger fixed reserve for every provider (cuts Ollama and Claude prompts for no gain).

## 2026-09-24 - Model task slots and a per-run model

**Context:** Ticket 04 of the model-profiles work: each model task names a profile, and one run can pick its own.
**Decision:** `[models.slots]` maps the slots research, notes, roles, queries, verdict, discovery and ai_account to profile names; an unknown slot fails the load. A slot naming a missing profile is refused by the Settings form (one select per slot, "unset" meaning default), where a user is present; loading the file (at start, and the live reload from disk) drops it with a warning instead, so neither an unattended restart nor the other live settings (the automation switch included) are held up by it. A dropped slot runs on `default`, except ai_account, whose tick research then uses the other slots. "slots" is reserved as a profile name (any case) and parsed into `Config.model_slots`, never a profile. A research run resolves all five stages once at its start (`resolve_stages`, one model per distinct profile), and each stage sizes its prompts by its own profile's context (notes by the notes model, the digest by the roles model, the verdict material by the verdict model). The run stores each stage's profile, backend, model and fallbacks as JSON (`research_runs.stages`, migration 37); `backend`/`model` stay the research stage's, and the label lists models per stage only when stages differ. Fallbacks are counted per stage: stages run in turn, so a shared model's fallbacks belong to the stage then running. Notes are read back under the notes stage's model, so a slot change reads again rather than reusing another model's notes. Discovery chat runs wholly (plan, notes, naming, validation) on the discovery slot; its notes stay keyed per question. The per-run picker (Research, Replay, Ask, Refine, Rerun; `--model` on `research`, `discover ask` and `discover rerun`) overrides every stage; the picker preselects "per slot", not "default", on purpose: picking the default profile would override every slot the user set, while "per slot" runs each stage on its slot and so equals "default" when no slot is set. A picked profile is checked at submit, kept on the job (`jobs.profile`) so a queued job and its Retry run on it, and part of the job's identity (the same listing on another profile queues separately). Retry-all still finds the newest job by kind and subject alone, not profile: a listing researched since on any profile needs no retry, and one already queued on any profile is being handled. It skips a job whose picked profile was deleted (a single Retry of it says so) rather than aborting the batch. ai_account, when set, is the picked profile for every stage of research a tick starts; unset, that research uses the slots like any run (not `default`, which would silently ignore the other slots). Deleting a profile a slot names is refused, naming the slots. A non-research stage whose profile cannot be built (a key_env unset) runs on the research stage's profile, so one bad slot never stops unattended research; the research stage itself failing to build fails the run as before. An ai_account profile that cannot be built gives way to the slots for that tick research. Either is kept as the stage's `unavailable` (which profile, and why), apart from `fell_back_to` (models that answered after a refusal), and the label says "<stages> on <profile> (<profile> unavailable: <why>)". A claude_code profile that is off (no `allow_claude_code`) counts as unavailable the same way and is never built, not even in place of a given model; the Models list warns with that reason. The key warning says per slot what happens: research and discovery fail, ai_account uses the other slots, the rest run on the research slot's profile. `oracle models list` and the Models section show the slots each profile serves (unset ones under default, marked "(unset)") and warn when such a profile's key is unset. "Discover now" (tick-style discovery) takes the discovery slot and has no picker.
**Alternatives:** ai_account unset meaning `default` (would bypass the notes and verdict slots on unattended runs). Notes in discovery chat on the notes slot (splits one short answer across models for little gain). A picked profile in the job subject (breaks the stale-research and dedupe checks keyed by symbol). Refusing a slot's profile deletion silently via the loader error (message would not say which slot).

## 2026-09-24 - Google News stubs decoded before the headline search

**Context:** Half of the Google News links the reading stage picked went unresolved: the headline search found nothing at the outlet's own site. Every live stub now carries an opaque id ("AU_yqL"), so the address is only in Google's own decoder.
**Decision:** Decode first: an older id's address offline, else Google's decoder (the stub's page for a signature, then a form POST to its `batchexecute` endpoint), both through the `google_news` gate with a new gated `RateLimitedClient.post`. Both decoder calls are tried once and kept off the breaker (a 429 still holds the source back), so a throttled decoder never shuts news search; a consent redirect or an error answer is passing, and a decoded address answering 404 or 410 falls back to the headline search. The offline path is unreachable on today's feeds (every live stub is the newer kind) but is kept: it is cheap, tested, and covers stubs stored earlier or Google reverting. A consent-declining cookie (`SOCS=CAI`) and the English edition's params avoid the European consent redirect. A decoded address must pass `unsafe_url` and the outlet check, and `read_page` still checks its host; otherwise, or when decoding fails, the headline search runs as before and the failure names both reasons. Outlet matching also accepts any host of the feed site's registered domain when that domain is registered under the outlet's name (morningstar.com for global.morningstar.com), and a short table of outlets registered under another name, mapped to their full registered domains (The Motley Fool to fool.com, fool.co.uk, fool.ca, fool.com.au). Country registries are the common ones listed (co.uk, com.au), else a generic label under a two-letter code (com.pe, go.jp), minus reseller zones (com.de), so reuters.com.de or fool.co.com is never taken for the outlet.
**Alternatives:** A decoder library such as `googlenewsdecoder` (a dependency with its own unguarded HTTP calls). Following the stub's redirect (it answers with a JavaScript page, not a redirect). Matching outlet names by any word of the name (Financial Times would match times.co.uk).

## 2026-09-24 - Agent follow-up research waits for the replay study
**Context:** User idea: let the agent ask for more research on a company when it needs it, not only on schedule.
**Decision:** Stream `research-followups` after `replay-study`, before v1: the verdict may ask up to 3 questions when confidence is low, cases conflict or evidence is mostly unread; one bounded follow-up run per listing per week inside the tick budget, never chained. Ships only if replays show it improves verdicts.
**Alternatives:** Build now (no baseline to tell whether it helps; a 4B model asks for more every time); unbounded self-directed research (eats the tick budget).

## 2026-09-24 - claude_code provider: the user's own `claude -p`, behind a file-only flag

**Context:** Ticket 06: a profile that runs the user's logged-in Claude Code CLI (their subscription), which a distributed build must not offer (2026-09-23).
**Decision:** `provider = "claude_code"` is usable only while `[models] allow_claude_code = true`, a key set only by hand: it is left out of the Settings form, and the Models provider list offers claude_code only while it is set. With the flag off a claude_code profile still loads (a warning is logged, so turning the flag off never breaks the file), is listed as off, and is refused when its model is built or saved. The key sits in `[models]` beside the profile tables, is reserved (no profile may take its name), is refused at the top level, and applies live with the profiles. `model` stays required and is always passed as `--model` (an alias like `sonnet` or a full id), checked to a plain token in the file: research does not follow a CLI default that can change under it, and every profile keeps one shape. No key and no `base_url` (refused in the file, dropped by the form). One process per reply, no shell: the whole conversation is flattened into one prompt on stdin (system text, then every turn in order as `<message role=...>` blocks, then the JSON Schema and an instruction to answer with one JSON object). Arguments through `claude.cmd` are reparsed by cmd.exe under an 8191-character line limit, so neither the prompt nor the schema goes on the command line (`--json-schema` exists but is not used for that reason). Flags, as `claude --help` of CLI 2.1.281 lists them: `-p --output-format json --model M --tools "" --permission-prompts none --permission-mode dontAsk --strict-mcp-config --setting-sources project --no-session-persistence --disable-slash-commands`: no tools, nothing that could prompt, no MCP servers or skills, no transcripts, and, with an empty temp folder as the working folder, no settings (so no user hooks, plugins or permission rules). `--bare` is not used: it forces API-key auth. `~/.claude/CLAUDE.md` may still load (unverified); the user's live check shows it. The reply is the envelope's `result` (the whole output, else its last line), one code fence stripped, parsed as JSON, else returned as text like the other backends; a non-JSON envelope, a non-zero exit or `is_error` is a `ModelError`, with a login hint when the CLI says to log in. Every `ANTHROPIC_*`, `CLAUDECODE` and `CLAUDE_CODE_*` variable is removed from its environment (except `CLAUDE_CODE_OAUTH_TOKEN`, subscription auth from `claude setup-token`, and `CLAUDE_CODE_GIT_BASH_PATH`), so it bills the subscription, not the API, and does not act as a parent session's child. The CLI is found in PATH's absolute folders only (never the working folder) and, on Windows, by PATHEXT only, so npm's sh launcher is passed over. On Windows the process joins a kill-on-close job object (ctypes, no dependency), so the reply ending kills everything it started. A kill while the CLI is alive runs a non-blocking `taskkill /T` first, which also reaches a child started before the job was attached (it walks the tree from the live CLI, so the job is not terminated at the same moment; closing it right after ends the rest); a CLI already gone has its job terminated at once. Elsewhere, the process group. A killed process gets 5 s to let go of the pipes, then they are closed off the event loop and the timeout error stands; a cancel never blocks the loop and waits briefly (shielded) before the temp folder goes. The subprocess is `Popen` with `communicate` in a thread, so it works on any event loop. `ORACLE_CLAUDE_BIN` names the CLI when `claude` is not on the app's PATH; tests point it at a fake.
**Alternatives:** Model optional (the CLI default). `--system-prompt` or `--json-schema` (cmd.exe quoting, length limit). A hand-kept `--disallowedTools` list (goes stale as tools are added). Refusing the whole file while the flag is off (turning it off would break the app). asyncio subprocesses (need the Proactor loop on Windows). Leaving the API key in the environment (would bill the API silently).

## 2026-09-24 - openai_compatible joined with claude_code and slots

**Context:** Merging the openai_compatible provider with claude_code (06) and task slots (04).
**Decision:** `Provider` carries `url` beside `keyed` and `base_url_required`; `URLLESS_PROVIDERS` is derived from it, so a provider is still one entry. An unusable profile behaves like an off one: `resolve_model` refuses it even when a test model is supplied, and its reason fills the row's `unavailable`, so a slot naming it falls back to the research profile under the slot rules and it is never built. Profiles load leniently even on a strict (write) load, so one old profile never blocks saving anything else; the profile being saved is still checked strictly. Each stage's budget reads the reply room of the model that stage runs on (roles for the digest, notes for reading, verdict for the verdict).
**Alternatives:** A separate unusable flag beside `unavailable` (two ways to say "cannot run").

## 2026-09-24 - The verdict's "invalidated if" must name a condition, asked again once

**Context:** Research-quality ticket 02: the 2026-08-24 replays of MSFT, AZN.L and 7203.T put restated evidence ("earnings exceeded estimates by 43.77%") where the verdict's "invalidated if" belongs.
**Decision:** A word check in `oracle/research/conditions.py`, no model call: the condition is rejected when it repeats a sentence of the reason (four words or more, either way round, citations and punctuation ignored) or has no conditional or change word (if, when, within, next, below, falls, misses, cut, loss, rejects, is delayed, raised...). The words are present-tense, noun and passive forms, so a bare noun phrase ("a guidance cut", "loss of the contract") passes while a past-tense restatement ("declined", "grew") does not; positive events (guidance raised, an upgrade) count too, since they prove a hold or sell wrong. Near-variants count as well (flat, stays, disappoints, launch, longer than, more than). A condition with no such word still passes when it carries a figure the reason does not give and no past-tense verb ("Q3 revenue of $58B"): a new figure is a threshold, while "grew to $58B" is a result. Years, "Q3" and citation ids are not figures. Run over every stored verdict and bull-case condition in the live database, it rejects only the three replay verdicts and their risk-manager copies. Only this rejection is asked again: one more call with the same material refitted to leave room for a short note (why, and the rejected condition cut to 200 characters); a second failure is today's rejected verdict. The prompt now also says "a future event and not the evidence restated".
**Alternatives:** A model call to judge the condition (a call per run for a word-level problem). Re-asking on every verdict rejection (out of scope; a reason citing what the budget left out fails the same way twice). Checking the risk manager's bull-case condition too (left as a follow-up). Rejecting past-tense verbs outright (false rejects on "the contract is lost").

## 2026-09-24 - Replay study: listing spread and pacing

**Context:** Replay study 02: a stored study of about 300 research replays that runs itself in free time, survives restarts and never blocks ticks or manual work.
**Decision:** The listings come from the universe, not the watchlist: 20 fixed (region, sector) slots (US 5, UK 5, Europe 4, Japan 3, other Asia 2, emerging 1, covering tech, financials, health, energy, consumer, industrials and materials). Each slot ranks companies based in the region by band (Mega, then Large, then Mid Cap), then how widely listed, then symbol, as peers are ranked, and takes the company's own line on its region's main board (exchange tier 1 or 2 in a region country) in the region's currency, so a New York ADR (TSM), a Berlin line (DKI.BE) or a UK company listed only in New York (LIN) never stands in. Skipped: the never list (Tesla always, `[discovery] never` too), depositary receipts and dead listings. Creating fetches each candidate's prices through the prices service and requires bars from the first replay day to within 10 days of today, so a delisted company or one with no data is passed over; up to 4 candidates are tried per slot, and every slot left unfilled is listed in the job result and the CLI. A price source that does not answer refuses the create, rather than quietly changing the spread. Creating is therefore a job (the CLI prints its progress). `--symbols` (the card's listings field) replaces the pick, and `replace OLD NEW` swaps one listing before the first replay, both held to the same checks except the slot. The pick is saved as JSON with the study. Each cell's day is its listing's first stored bar in the month, so a local holiday is never a replay day, from the first day to a calendar month before today; the first day is `--from` or the measured cutoff (`measured_cutoff`, None until ticket 01 stores one; a `--from` before it is refused). Cells queue oldest month first, so early results have their longest outcomes. A study is created paused and only `start` lets the pacer run; creating another is refused while any replay runs (older studies are kept, the newest is the one shown).
The pacer polls every 10 s in the GUI server, stores each poll's time and wait reason (so a headless check can tell it is alive), and submits one replay at a time as an automatic research job (slots model) only when: the study is running, automation is on, no back-off is in force, no job is queued or running here, no job lock is held by another process, no scheduled run is due within the longest of the last 10 replays (5 min before any) plus 10 min, counting one whose time has passed that the scheduler's next poll has yet to submit (`Scheduler.next_run`), and `research_when_idle` does not say the PC is in use. A running replay gives way when a tick or midday pass is a minute away or other model work (research, tick, ask, discover) is queued or running: the pacer cancels it and its cell goes back to pending without counting an attempt. A cell gets two attempts (the second later in the queue), then stays failed until Retry, which allows one more without rewriting the count. Three failures in a row with the same error kind (the text before the first colon, without the symbol, quoted text, hex addresses and numbers), or five in a row whatever their text, mean an outage: those attempts are given back and the pacer backs off 45 minutes, then tries one replay. While the outage lasts, a failed probe with the outage's error (any error, for one found by five) costs no attempt and backs off again, while one with a new error costs its cell the attempt but still backs off (five of those in a row are an outage of any error); a cell's attempts are given back at most twice (`refunds`), after which its failures count, so cells that are bad rather than unlucky still end failed and the study finishes. Back-offs run every 45 minutes four times and then every 2 hours, never pausing the study; `waiting` says since when, how many back-offs and the last error. A replay running over 3 times the longest recent one (at least 45 minutes) is stopped and counted as a failure, so a hung one never sits until the next tick. An Activity Retry is taken for its cell only on the slots model the study replays on. A replay cut off by a restart goes back to pending; one another live server still runs is left alone. One cancelled in Activity goes back to pending and pauses the study, so the next poll does not undo the cancel; one cancelled by turning automation off does not pause it (the pacer holds until automation is back). Activity's Retry on a replay of a failed or waiting cell runs it for that cell. `finished` (nothing left to run) and `complete` (every cell done) are reported apart.
**Alternatives:** A hand-kept ticker list (goes stale when the universe changes symbols). Taking the primary listing as it is (the real universe offered ADRs, Berlin lines and a New York line for a UK slot). Picking without fetching prices (seven of twenty real picks had no bars at all). The first weekday of each month (falls on Labor Day, May Day and other local holidays). Pausing after repeated outages (would stop the study for the rest of a trip). The mean replay time for the tick margin (about half the replays run longer). Waiting on each job inside the poll (a poll-and-settle loop keeps the store the single source, so a restart needs no special case).

## 2026-09-24 - Replay study: scoring rules

**Context:** Replay study 03: score each replay verdict against what the price did next, cheaply enough for the status poll.
**Decision:** A forward return runs from the listing's first close strictly after the as-of day (a replay reads that day's news to 24:00 UTC, after most closes) to its last close on or before the first weekday on or after the same day 1 or 3 calendar months later, in the listing's currency, using adjusted closes only when both ends have one (else plain closes). Either close more than 7 days from the day it stands for is a gap, so unpriced (exactly 7 is a price); so is a zero or missing close between them, and any day-over-day close move outside 0.5x to 2x, taken for a split the stored bars were not re-based for (real days reach about 0.77). A horizon is pending while its end is today or later; unpriced once it has passed without covering bars. The basket is the equal-weight mean of every study listing's return from the same day over the same horizon, with its count; under half the study's listings priced, it and the relative results are unpriced. A verdict's relative return is its return minus the basket's. Hits are buys that rose and sells that fell (flat is a miss); holds count in the mix and mean returns but call no direction. Every hit rate carries a Wilson 95% interval from 10 calls (None below), and each horizon has base rates over the same (listing, day) windows as the scored buys and sells (the queue runs oldest first, so all windows would compare different months): the share that rose and that beat the basket, plus the hit rate that call mix would expect by chance (buy share times the rise rate plus sell share times its complement). Confidence bands are under 0.60, 0.60 to 0.80 and 0.80 and up, over buys and sells only, with returns as called (a sell's sign flipped). Scoring reads stored bars and verdicts only (the verdict via `json_extract` on the report body) and is recomputed on every call, about 60 ms for a full 300-cell study, with no cache to invalidate. Fetching is `update_study_prices`: `oracle replay report` (before it scores), the card's Update prices (a `replay_prices` job) and every tick's prices stage (so the interim stays current unattended; any error there, even an unexpected one, only adds a note to the stage summary). It refetches, over one client and skipping dead listings, the whole study window (the first as-of day less a week, via `update_listings(since=)`) of every study listing, watched or not, with a window closed on or after its last whole-window fetch day (kept per study in meta), so each window reads closes on one split and dividend basis: incremental updates can add bars on a newer basis, and a 3:2 split passes the ratio guard. A suspect split or missing bars therefore heal on the next check; once a whole fetch after the window's close still cannot price it, it stays unpriced without refetching daily. Rather than hide failures behind an "updated" time, the score carries `prices_checked_at` (every check, fetches or not) and `price_failures` (the listings the last check could not fetch). The status JSON carries a small `score` block: verdicts, no-verdict count, buy share, mean confidence, `prices_checked_at`, `price_failures`, a note pointing at the limits, and per horizon scored, pending, unpriced, hit rates with intervals, base rates, expected hit rates and per verdict n, raw mean return and mean as called. The stated limits are one constant printed with every report.
**Alternatives:** The as-of day's own close (look-ahead for Asian listings and after-hours news). Trading-day offsets (21 and 63 bars; calendar months match how the study picks its dates). A cached score keyed on cells and bars (invalidation for 60 ms saved). Incremental fetches (mix split bases across fetches). Pricing study listings in the pacer (it is being reworked; the tick already runs unattended daily). A benchmark index for the basket (another currency and universe; the study's own listings are what the verdicts chose among). Counting holds as hits when the move is small (needs an arbitrary threshold).

## 2026-09-24 - Replay study: the simulated account

**Context:** Replay study 04: what the replay verdicts would have earned as an account, against buy-and-hold, reusing the backtest engine and the AI account's rules.
**Decision:** The backtest engine (`broker/backtest.replay`) gains a lean source (`lean=`, default the technicals baseline), `fill_at="close"` and `venues=` (per listing country and exchange; given, every fill pays the paper accounts' `fills.COSTS`: 0.1% slippage, the broker's fee table, a 0.2% FX spread each way when the rate is not 1, 0.5% stamp duty on LSE buys; `Trade.costs` now counts stamp and spread). Without `venues`, `oracle backtest` charges exactly what it did. Only a buy lean is now sized, which leaves every decision the same; it speeds up the verdict account (few buy leans) and not the technicals backtest (131.8 s against 130.0 s on the live list). The study account passes each listing's newest verdict with an as-of day on or before the decision day, used for `max_verdict_age_days` like the live AI account's, and fills at the next close, so a verdict enters at the first close strictly after its as-of day as the score does. Rules, risk sizes (the year before each day), the stop and whole shares are the engine's, with each listing's venue from the universe. Prices stay in each listing's major currency unit (pence to pounds; adjusted closes when every bar has one) at one fixed rate per listing, the stored base rate nearest the first day, so returns stay in the listing's currency like the score's while fees and share prices are real. Every line starts from a notional 1,000,000 in the base currency (`NOTIONAL`), not the AI account's 10,000: at that size a 500 to 1,000 slot buys no ASML, LLY or CAT share and half an AAPL one, and the study picks the biggest company per slot; at the notional, whole shares are negligible, proportional costs stay, and fixed fee minimums are negligible, as a note says; a listing with no stored rate is priced as if a share cost 1 base unit on the first day, and named. Three lines are drawn: the verdicts (`ai`); every verdict taken as a buy at its confidence under the same rules (`all_buy`), because the caps (20% cash floor, at most 10 positions of 10%, the 8% stop, a short verdict age) keep both traded lines partly in cash, so the verdicts' own effect is AI against that line and a note names the caps; and buy and hold, equal amounts bought in fractional units at the first close after each listing's first as-of day, paying the same costs on that one entry (counted as a trade only when it buys something). Rejected orders are collapsed to one note per listing and reason. Replays on or before the current cutoff are left out of every line, as the score leaves them out, so the account and buy and hold start at the first replay day after it. Each line shows return (against the starting capital, so entry costs count), largest fall, trades and costs, in `oracle replay report` and on the card. A listing is left out of every line, and named, when the score's pricing rules fail from its first as-of day on (no close within 7 days after it, a zero close, a day-over-day move outside 0.5x to 2x) or when a whole-window fetch more than 7 days after its last close found nothing newer (stopped trading). A listing whose bars merely lag (unwatched ones are refetched about monthly) is held flat after its last close and named (as failing to fetch when the last price check could not fetch it), rather than dropped or dragging the end back. Risk sizes read only the bars after the last zero close or suspect split up to the first close after the first as-of day, and study price refetches now start 400 days before the first as-of day so the sizing year shares the window's basis. `replay_report` adds the account to `Score` and the report route runs on a worker thread and connection; `score_study` (the status poll) never runs it (about 600 ms for a 300-cell study with three lines, against 45 ms for the score), so the interim block has no account line. `BASELINES` in `replay_account` takes more rule accounts by name (caption and lean source), run through the same engine, listings, days and costs: quant-basics adds the momentum baseline there.
**Alternatives:** A second simulator for verdicts (two sets of fill and sizing rules to keep in step). Filling at the next open (one day later than the score's entry). Daily FX conversion (the study's KRW, SGD and BRL have no stored rates, and the score's returns leave FX out). Indexed prices with flat costs (fee minimums and per-share fees need real prices and share counts). The AI account's own 10,000 (whole shares left buy and hold 15 to 25% in cash and the biggest names unbought). Ending the account at the earliest last close (one monthly-refetched listing would cut weeks off every line). Dropping lagging listings (most unwatched study listings would drop). Comparing the verdicts to buy and hold alone (measures the rules' cash drag, not the calls). An interim account line (a far slower status poll every 15 s).

## 2026-09-24 - A name matched by its start must fit the question; one GDELT search per answer
**Context:** Discovery 03. "Olix" (a UK chip startup) matched OliX Pharmaceuticals (Korea) by the
start of its name, and 3 of an answer's 4 GDELT searches failed on 429 and the open circuit. Gate
round 1: judging the sector by headline words refused many true matches ("Micron stock rises after
bank upgrade", "Toyota picks Rockwell for car plant automation"), and any nationality word was
taken as a home ("Micron wins UK subsidy").
**Decision:** `candidate_screen` records how a name matched. Exact names and symbols are kept as
they are; a match by the start of the name (one company, or the clearly biggest) goes through
`business_fit`, given the question (or theme, or mover search) and the items citing the name.
Home: a nationality counts only when it qualifies the name itself ("German startup Olix", "UK-based
Olix", "Europe's Olix", "Olix, the German ..."), with only describing words between; bare "UK" and
"US" never count. A home that does not hold the company's country refuses the match, except for a
US line on an OTC venue or carrying a foreign legal form (the universe often calls those US).
Sector: only the question's own words count, never the headlines' (they name partners' and
customers' trades); a watched company's related news asks nothing, so it gets no sector check.
Words of the universe's classifications give sectors (plurals folded, aliases: chip, chipmaker,
drug, defence, "semiconducter"); words news uses for anything ("financial", "capital", "energy",
"it") are left out. A sector the question does not ask about never refuses alone: it also takes
an industry word in the rest of the listing's name that the question and news never use
("Pharmaceuticals" against chips, or a trade the classifications do not spell: medical,
therapeutics, furniture; "Technology", "Electronic" and "Media" say nothing). Size is no sign:
small caps serve the end markets questions name (car battery makers, bank payments). Reason: "a
different business: OliX Pharmaceuticals,Inc is in Pharmaceuticals, Biotechnology & Life Sciences,
but the question is about "semiconducter"". After a comma, a home counts only past a determiner
("Micron, Korean rival SK Hynix" is another company's home).
Refused names show under "left off" (GUI, `oracle discover show`) and in a run's skipped list;
links go through the same check. The chat plan searches GDELT for its first query only; every
query goes to Google News and the web.
**Alternatives:** Headline words as a sector signal (refused cross-industry partners, worst in
related discovery); the sector alone (the universe's sectors are sometimes wrong: Rheinmetall as
Consumer Discretionary, Arista as Financials); a model call per fuzzy match (slow on a 4B model);
moving the extra GDELT searches to Google News (each query is there already).

## 2026-09-24 - Cutoff probe: scoring rule, margin and question set

**Context:** Replay-study ticket 01. Replays must start after what the research model actually knows, not what it claims (2025-05-20 for Qwen3), or a replay could remember what happened next.
**Decision:** A versioned question set (`oracle/research/cutoff_questions.py`, version 2026-09-24.1) of 2 or 3 dated public events a month from 2025-01 (the claim minus four months) to 2026-09, each with its known answer, keywords and a source URL; events after September 2025 were confirmed by web search. Events were picked to be hard to guess from earlier knowledge: no scheduled rate holds or foregone results. Each event is asked alone (system prompt: answer in a few words, "unknown" if unsure) on the JSON-schema chat path, on the research slot's profile or `--model`. A reply is right when every expected keyword, or one of its "a|b" alternatives, is in it as a whole word or number, after lower-casing, dropping accents, writing fractions and trailing zeros one way and ranges as "a-b" ("4.00% to 4-1/4 percent" matches "4-4.25"); no second model call. A month is known at half right or more. The measured cutoff is the last day of the month after the last known month (one month margin), never before the claimed cutoff. The last known month anywhere counts, not the first miss: a lucky late month only moves the cutoff later, the safe direction. Only events on or before today are asked. A model error fails the probe and keeps nothing. One probe per model (provider and model id from the profile, so a slot change reads the right one), replacing the earlier probe, with every question, reply and mark (migration 39, numbered beside replay 02's 38). `measured_cutoff(ctx, profile=None)` is what replays read; `check_replay_from` refuses a day on or before it, and when the cutoff leaves no day at least a month old (a model that knows everything) it says so.
**Alternatives:** A second model grading replies (costly, and the grader has its own cutoff). The first month under half right as the cutoff (earlier; a model that half forgets a month would let replays look ahead). Asking all of a month's questions in one call (answers bleed into each other). Storing probes in `meta` like backtests (the answers are rows worth querying).

## 2026-09-24 - Cutoff probe covers every stage's model; unguessable questions, two right a month (gate round 1)

**Context:** A replay runs five stages (queries, research, notes, roles, verdict), each on its own slot, but only the research slot was probed, so a later-cutoff verdict model could leak outcomes. Several questions could be answered from earlier knowledge (repeat winners, values already true, favourites, answers the question implied), and with two questions a month one guess made a month known.
**Decision:** The question set (version 2026-09-24.2) has three questions a month, each asking what only the event revealed (a score, a vote count, a runner-up, a name nobody predicted), each checked on the web with its source recorded; a month is known at two right. `measured_cutoff(ctx, profile=None)` resolves the distinct profiles of the replay's stages (the slots, or `profile` for every stage) and returns the latest of their cutoffs, or an `Unprobed` value (falsy) naming those not probed; `check_replay_from` then refuses every day, naming the profiles to probe, rather than falling back to a claimed cutoff, and otherwise needs a day strictly after the cutoff. Probe with no profile probes each unprobed stage model in one job (all again once every one is probed); with one, just that one. Each profile may set `claimed_cutoff` (default 2025-05-20), stored with its probe and shown in Settings > Models and the readout. Keyword checks no longer turn "to" and "and" into dashes: a range or vote keyword "a-b" is searched as "a (-|to|and) b" and never inside a longer chain; punctuation other than `. - /` is a space; `<think>` blocks are dropped; a failed reply is asked once more before the probe fails.
**Alternatives:** Probing only the research slot (a verdict model with a later cutoff leaks). Enforcing the claimed floor while unprobed (the claim is what the probe exists to check). Half right with two questions (one guess passes a month).

## 2026-09-24 - Cutoff questions one of each kind a month; claim applied when read (gate round 2)

**Context:** Two late months still had two answers a model could infer (a question that narrows to one famous person, a repeat of an older result), so one month could end the study; and a claimed cutoff raised in Settings after a probe never took effect.
**Decision:** Question set 2026-09-24.3: every question has a kind (number: a score, vote, share, price, toll; name: a person, team, company or country; other: a place, title or code name), and from the default claimed cutoff on each month asks one of each, so no single kind of guess (a favourite, a repeat, an inferred name) passes a month; numbers are preferred. A keyword "58.x" takes 58 with any decimals. A reply that hedges ("unknown", "not sure", "I don't know", "no idea") is wrong whatever else it names. A profile's cutoff is read as max(measured, its claimed cutoff now), and the model that sets the replay cutoff is picked on that value.
**Alternatives:** A free-text audit note per question (nothing checks it). Re-probing when the claim changes (costs a full probe for a settings edit).

## 2026-09-24 - Replay study creation reads the cutoff probe

**Context:** Merging the cutoff probe (01) with the study queue (02), whose stub cutoff was always None and whose `--from` was the only way to start.
**Decision:** Creating a study calls `check_replay_from`: while any model a replay's stages run on is unprobed, or its cutoff leaves no day with a month of outcome, creating is refused with that reason, `--from` included; otherwise the first day defaults to `replay_from`, the day after the cutoff, and a given day must be after it. The status JSON's `cutoff` is the replay cutoff (claims included), beside `replay_from` and `cutoff_note`. The card's create form fills and floors its day with `replay_from`, and the cutoff section sits in the same Replay study card, reloading it after a probe. Migrations run 38 (replay study) then 39 (cutoff probes).
**Alternatives:** Letting `--from` bypass an unprobed model (a replay could then remember what came next).

## 2026-09-24 - A replay study checks its cutoff and models before every replay

**Context:** The cutoff was checked only when a study was created, but each replay job resolves its stage models from the slots when it runs, so a slot moved after creation, a re-probe or a raised claim could let the pacer replay days the model knows.
**Decision:** A study stores the profile each research stage ran on at creation (`replay_studies.stages`, added in migration 39) and shows it in status, the CLI and the card. Before every submit the pacer reads the slots, probes and claims again: stage profiles that differ from the stored ones pause the study ("the replay models changed since the study was created: verdict default to big"), as does an unprobed model or a cutoff that leaves no day (its note); pending cells on or before the current cutoff fail for good ("on or before the cutoff X of profile", no attempt left). Start refuses on the same grounds. Replace builds cells from the later of the study's first day and the day after the cutoff. Retry, in the study or in Activity, never requeues a cell on or before the cutoff (Retry all skips them). The score leaves out done replays on or before the current cutoff and says how many.
**Alternatives:** Pinning each replay job to the stored profiles (a run would then ignore a slot the user moved on purpose, silently). Letting a study run on mixed models (its verdicts would no longer measure one set of models).

## 2026-09-24 - The universe screen answers "which companies" questions (discovery 01)
**Context:** Three live questions named 11 companies and matched 1: headlines favour famous names
and startups, so real listed mid caps that fit a question's industry, region and size never came up.
**Decision:** The chat plan also returns `industries` (up to 3, offered the universe's industry names
in the prompt) and `regions` (up to 5). Industries map onto the universe's classifications with the
exclude matcher, now `industry_words.classes` (the most specific level); regions and countries
through one table, `oracle/universe/regions.py` (regions, nationality words, each country's
currency), which also replaces 03's Europe and nationality tables in `business_fit`. A word naming
nothing is dropped with a note. A screen needs an industry: a region or a size alone would list any
big company there. `universe_screen` takes active primary equity companies in any of the
classifications, based in the countries asked (or with a US OTC primary, where the universe's
country is often wrong), within the plan's bands, biggest band first then most widely listed; it
drops never-listed companies (any line's name), depositary receipts and companies with no live home
line. The home line is the replay picker's rule, moved to `regions.home_line` and used by both: the
best-ranked line on a main board (tier 1 or 2) in the country's own currency, inside the countries
asked (STMicro is STM.PA for Europe, not its New York primary). Replay's per-region currency sets
became per-country currencies; its slot regions stay its own (`STUDY_REGIONS`: Europe there leaves
out the UK). In the shortlist, companies both named and screened rank first and carry both kinds of
evidence, then those only named, then those only screened, up to 15, with their own budget of 20
price checks; one without recent prices is passed over for the next and only counted. The evidence
line ("universe: Semiconductors & Semiconductor Equipment, Netherlands, Mega Cap") is stored per
item (m0040) and shown on the Discover tab and in `oracle discover show`; a note says how many
matched and how many are shown. The recognised industry words join the question in `NamedIn.asked`,
so 03's sector check holds a name matched by its start to them. Auto: a company only the screen found
is never validated or added ("a universe match alone is not evidence"); a named one on its screened
home line passes the line check. When nothing screens, answers work as before, and the screen runs
even when the news finds nothing.
**Alternatives:** Screening lines rather than companies (each line carries the company's same wrong
sector, so nothing gained); trusting the universe's country alone (US OTC lines hide Rheinmetall);
the primary line as it is (New York ADR-like lines and OTC lines of foreign companies); listing
screened companies without prices as left off (no one named them: noise); a screen on region or size
alone; letting auto add on a universe match (DECISIONS 05 wants outlet evidence).

## 2026-09-24 - Universe screen follow-ups left open
**Context:** Found while building discovery 01 on the live universe.
**Decision:** Recorded, not fixed: wrong sectors hide companies from the screen (Rheinmetall is
Consumer Discretionary / Specialty Retail on every line, so a defence question misses it); the
universe keeps acquired companies (Dialog Semiconductor, Meggitt), which the price check passes over;
the plan's region is still not enforced on news-named companies; words below the universe's
industries ("robotics", "batteries", "solar") screen nothing unless the model picks a listed name;
NXP's Europe line is Xetra (VNX.DE), as it has none in Amsterdam; a company split in two by the
universe with its home line carrying no currency (Prysmian: PRY.MI apart from AEU.F) has no home line.
**Alternatives:** A sector override table (hand upkeep); a company-level country vote across lines
(every Rheinmetall line says United States).

## 2026-09-24 - Universe screen gate round 1 fixes
**Context:** The gate found that a screen with no region picked ADRs, BDRs and foreign secondaries
(NIO, TSM, TMCO34.SA), that an unrecognised region ("EU") widened the screen to the world, and that
named companies and screened ones had separate price-check budgets and screened checks ignored the
auto deadline.
**Decision:** With no region asked, a company's home line must be in its own country, unless its
primary is a US OTC line (the universe's country is then not trusted and any home line counts); a
company with no country, or no such line, is dropped. Receipt lines are never chosen, and "DRN" (Sao
Paulo's BDR suffix) counts as a receipt. Regions asked of which none is recognised skip the screen with
a note. `countries_of` ignores dots and hyphens and knows the EU, Eurozone, Western Europe, Southeast
Asia, Asia-Pacific/APAC, Africa, emerging markets (roughly MSCI's), England/Scotland/Wales and more
nationalities. One price-check budget per answer (8, plus 20 when the screen runs): named companies
take from it first. Screened companies are not price-checked past the auto deadline, and the note says
so. A plan industry phrase naming nothing whole is tried a word at a time ("defence contractors",
"oil majors"); aliases ev, homebuilder and miner.
**Alternatives:** The best main-board line anywhere (brought back NIO and TSM); screening the world
for an unknown region (answers a different question); a separate screen budget (named companies could
go unchecked while the screen spent its own).

## 2026-09-24 - Universe screen gate round 2 fixes
**Context:** The word-by-word retry matched phrases by their first known word ("electric vehicles" as
Electric Utilities, "private equity" as Equity REITs, "car parts" as Automobiles), and Brazilian BDRs
kept as companies of their own passed a region screen (C1HI34.SA for Sinopec, "ECOPETROL SADRN").
**Decision:** A plan phrase is matched whole, else once more without a trade noun at its end (makers,
manufacturers, contractors, companies, firms, producers, suppliers, majors, players, stocks, groups,
providers, operators, developers); anything else stays unrecognised with the note. A name ending in
"DRN", glued or not, is a receipt, and so is a B3 ticker of four letters or digits and 32, 33, 34, 35
or 39 (`is_receipt_symbol`); the screen checks both on the primary and on every line it may choose.
**Alternatives:** Keeping the per-word retry with a stop list (every new phrase finds a new wrong
word); dropping every Sao Paulo line of a foreign company (the ticker says which are receipts).

## 2026-09-24 - Universe screen gate round 3: foreign quotes on Latin American boards
**Context:** With a region asked, Mexico's SIC quotes and Buenos Aires' CEDEARs of foreign companies
stood as home lines (TSMN.MX for TSMC, SQ.BA for Square): they trade on a main board in the local
currency.
**Decision:** The screen never takes a line on BUE, MEX or SAO for a company whose universe country is
not that exchange's country. The replay picker's rule is left as it is.
**Alternatives:** Changing `home_line` itself (would change the replay spread); a ticker pattern per
board (SIC and CEDEAR tickers follow no one pattern).

## 2026-09-24 - Replay study size mix: a mid/small half, scores split by size, slippage by band

**Context:** Replay study 06: the user would rather test smaller companies than megacaps (an edge is more plausible where fewer people read the news); a mix is fine.
**Decision:** The picker has two halves over the same 20 (region, sector) slots and the same home-line, currency, price-history, receipt, dead and never-list rules (Tesla never). The large half is unchanged (Mega, then Large, then Mid Cap in one ranking). The mid/small half takes one Mid Cap per slot, trying Small Caps only when no Mid Cap fits (up to 4 price checks per band), never Micro or Nano. `--bands large|mid-small|mix` (card: "Study size mix") picks the halves; the default for the CLI, the route and the card is `mix`, since that is the study the user wants. Named listings (`--symbols`, the card's listings field) stand in for the large half, so the user's 21 plus TSMC with `mix` gets a picked mid/small half; `mid-small` with named listings is refused. Slots the smaller half cannot fill are named "(mid/small)" in the left-out list. No migration: each listing's band is already saved with the pick. A listing's size for the split is the band it was picked in (Mega and Large Cap are "large", Mid Cap and below "mid-small", no band "unknown"), so a Mid Cap the large half fell back to counts as mid/small. The score and the report carry `by_size` (only when the study holds more than one size): per size, its listings, verdict mix and each horizon's full numbers (hit rates with intervals, base rates, no-skill expected hit rates, by verdict, by confidence band, and a new `mean_as_called` over buys and sells), all against the whole study's basket; the full report also runs the account per size, each at the notional capital (about three times the account's time). The status interim gains `by_size.<size>.{label, listings, verdicts, buy_share, horizons}` and `mean_as_called` per horizon, both additive. A split report adds a limit on thinner news, data gaps and real costs for smaller companies. Slippage comes from one table, `fills.SLIPPAGE_BY_BAND` (Mega and Large 0.10%, Mid 0.25%, Small 0.50%, Micro 1%, Nano 2%; no known band keeps the old 0.10%), through `fills.costs_for(band)`, used by paper fills (the universe profile's band), the backtest engine's `venues` path (`Venue.band`) and the replay account (the band saved with the study, for fills and buy and hold alike). `oracle backtest` does not pass venues and charges its `--slippage-bps` as before, so its output is unchanged.
**Alternatives:** A size-matched basket per half (the split would then hide how each size did against the market the verdicts chose from). Splitting by which half picked a listing (a large-half Mid Cap is a mid cap). Keeping `large` as the default (the user asked for the mix; the old pick is one flag away). A per-slot mix interleaved in one list (two halves read more clearly in the card and keep the large half identical to today's pick). Band-based slippage in `oracle backtest` (would change its numbers for existing callers).

## 2026-09-24 - Size mix gate round 1: bands as ranked, named listings count as large, TSMC's Taipei line

**Context:** Gate round 1 on replay-study 06: a named listing with no band became a third "Size unknown" group; a pick saved its home line's band, not the band it was ranked by; `2330.TW` was not in the universe, so the user's pick with TSMC was refused.
**Decision:** A pick saves its company's band (the primary listing's, which `largest_in` ranks by) and its half (`Picked.half`). A named listing takes its own band, else its company's; with none, it counts in the large half it stands in for (slippage stays the default 0.10%, the large rate). FinanceDatabase has no row for TSMC's Taipei line (TSM, its European quotes and a Brazilian receipt only; TWSE itself is well covered), so the universe refresh adds `MISSING_HOME_LINES` rows (today only `2330.TW`, as TAI/TWD) as copies of a line of the same company it has, only when the source lacks the line and has the company; they group with it by ISIN. It takes effect at the next universe refresh. The default large pick for Asia IT may then take TSMC over Samsung.
**Alternatives:** Accepting named symbols missing from the universe (no sector, band or company for the checks). Counting bandless listings as unknown (a third group of one listing and its own account). The home line's own band (not what the pick ranked by, and often blank on secondary lines).

## 2026-09-24 - List pages read for a question keep the companies they list
**Context:** Discovery 02. Discovery chat answers named few companies because headlines name few. A "top 10" article or a Wikipedia "List of ..." page names many, but its note kept 3 facts and no names.
**Decision:** Reading for a question (never for company research), the note call itself also asks for up to 15 `companies` when the page lists or ranks them: no extra call, and the 4B model judges what a list is. The prompt says a page listing companies of the kind asked about is about them: without that, live qwen3 4B called a top-10 of European chipmakers "not about" a question adding "gain from the EU Chips Act". Code keeps a name only when the text the model read (after the cut) holds it, citations stripped, once each, and only 4 or more names (live, a plain story "lists" the two or three companies it names, which its facts already carry). It stores the names as one more line of the note, `Lists: A; B; C; D`. No migration: the naming call sees them as that item's own words, and the item-words rule, business fit and price checks apply unchanged. That line never counts as a line naming two linked companies. A question's reading gives undated web results whose title or address looks like a list of companies ("list of", "/wiki/List_of_", or top, biggest, largest, leading or best followed within 4 words by companies, stocks, firms, makers, manufacturers, suppliers, producers or players) the 2 first slots that report pages take in research, so a list page is read ahead of newer headlines. A dated headline never takes those slots ("Top 5 takeaways from ASML earnings", "3 best AI stocks to buy now"), and a bare count or "ranked" is no list. The naming call may name 10 companies (was 8), each with a why of at most 100 characters ("a few words"), so a full reply stays under Ollama's 1024 reply tokens. The plan's queries include a search for a page listing the companies when the question asks which fit. Each list page read adds an answer note ('9 companies from the list page "..."'), shown in the GUI and `oracle discover show`.
**Alternatives:** Detecting list pages in code alone (repeated short lines or table rows; text extraction flattens tables unevenly) or a second call for names (one more model call per page); a names column in `article_notes` (a migration that 01's m0040 would have to renumber around); a naming cap of 12 or 15 with 200-character why lines (can outrun Ollama's 1024 reply tokens and lose the whole answer); any dated headline shaped like a list taking a list slot (pick reads only 2 such, so real news would be pushed out).

## 2026-09-24 - A question's named companies all get a price check
**Context:** Merging list pages (02) with the universe screen (01): the answer's pooled price-check budget was 8, plus 20 when the screen runs, with named companies checked first. A list page lets the naming call fill its 10, so with no screen the last 2 were left off as "too many to price-check".
**Decision:** The base budget is the naming cap (`MAX_PRICE_CHECKS = MAX_ANSWERS`, 10), so every named company is checked; the screen's 20 stay on top for screened ones.
**Alternatives:** Keeping 8 (a list page's last names always dropped unchecked); a separate budget for list-page names (two budgets for one kind of check).

## 2026-09-24 - Brands, parents and private names resolved in the shared name match
**Context:** Discovery 04. The robotics question matched 1 of 10 names: groups refused as ambiguous
(Siemens, Samsung, Sony, Toyota, Ericsson) or too short (Arm, BAE, LG), brands and subsidiaries not
found (Google, Intrinsic), startups "not in the universe" like a gap, and wrong listings (CATL a SPAC,
Meta a Brazilian receipt, Besi a bond, Lovable Lingerie for Lovable).
**Decision:** In `candidate_screen` (chat, tick discovery and auto share it; now async), rules first:
receipts and lines with no sector, size or country never match; unsized regional quotes give way to
sized companies; several companies starting with a name are settled by the whole name in the news,
then the only one in the question's sectors, then the group's own name ("Siemens Aktiengesellschaft"),
then clearly biggest (one band once the sectors narrowed them); only then the model picks among at most
5 (never for a place word), else refused naming the candidates. Short names match whole words only.
A parent the news names ("Alphabet's Intrinsic", "Intrinsic, an Alphabet company") is offered with
"Intrinsic is part of Alphabet Inc."; a named parent beats a small by-start match. A name the universe
lacks, a tiny listing matched only by ticker, or a refused match gets one Yahoo search: its own name,
its ticker, or a far-first big result ("Google" is Alphabet). Yahoo listing nothing, or the news
calling it a startup, is "private, not listed"; Yahoo listing it outside the universe says so. A small
company matched by its start whose rest of name says a trade the news does not ("Lingerie") is refused
unless the news writes its whole name. Name trade words count their sectors ("bancorp" for banks).
**Alternatives:** A model confirm for small by-start matches (measured on qwen3 4B: yes to Lovable,
Aleph and Cera, no to a true Kestra Medical, reason-first prompt said no to all); a built-in brand to
parent table (goes stale, never covers startups); adding "Aktiengesellschaft" to legal forms (would
regroup the universe's company keys).

## 2026-09-24 - Brands gate round 1: bounded lookups, stakes are not ownership
**Context:** Gate round 1 on discovery 04: "Amazon's Anthropic investment" offered Amazon, "Tesla
Optimus" matched a small Japanese listing, name searches were unbounded on the price limiter and its
breaker, and a non-JSON search answer could fail a whole answer.
**Decision:** A possessive followed by stake, investment, bet, deal, backing, partnership and the like
is no parent. A small by-start match written straight after a never-list or universe company's name
is refused. Yahoo search has its own source `yahoo_search` (0.5/s, one retry, own breaker), at most 5
searches per screen pass (tick or answer), owners searched only in their shortest form; past that, or
past an answer's deadline, names are matched by rules alone. An unreadable answer is no answer. The
model is asked once per name and news, only with 5 or fewer candidates. A Yahoo lead outside the
universe says "listed as ...", a ".PVT" line says private; startup words must be said of the name,
"round" only with seed, funding, venture or a Series.
**Alternatives:** Sharing the `yahoo` source with a lower rate (prices still paused by a search
burst); a cap per name instead of per pass (a tick with many names still floods).

## 2026-09-24 - Replay study hardening for unattended runs (holiday-hardening 01)
**Context:** The study will run 5 days with nobody at the PC; session 15's gates left mediums and lows on the pacer, the cutoff pins and study prices.
**Decision:** The pacer's memory (outage start, kind, last error, back-off count, resume time, the failures in a row, jobs it preempted or stopped as hung) is one JSON column, `replay_studies.pacer` (m0041), read at the start of every poll and written at its end, so a restart resumes a back-off and a replay the pacer stopped is never read as the user's cancel. A manual Retry (cell, all, Activity adopt) resets the cell's `refunds`. The hard cap times a replay from its job's start (an adopted Activity retry can wait in the queue), a done cell records that start so queue time is not replay time, and the message says whether the 45 minute floor or 3x the longest recent replay applied. Create refuses only when no price source answered; when one answered "no data" and another was down, the candidate is passed over, naming the down source, and not counted towards dead. A study listing whose whole-window fetch fails 3 times in a row rests a day (then one try, and another day on failure); it stays in `price_failures`, and the score, status interim, CLI, card and tick summary carry `price_backoff` (symbol: next try), an additive key. The tick's FX stage already fetches every currency stored bars use, study listings' included (a test pins it; no code). A study pins `{stage: [profile, provider, model]}` at create; `cannot_run` compares provider and model (a renamed profile is no change, the same name on another model pauses), studies from before compare profile names. Score, account, cell retry and the status `cutoff` use the pinned models' cutoff (each pinned model's probe, raised to its stored claim and any current profile's claim for that model), falling back to the slots' cutoff only if a pinned model has no probe or an old study's profile is gone. Activity Retry of a study cell goes through `cannot_run` first.
**Alternatives:** A pacer state table (one row per study is what the column is); keeping the state in memory (a restart forgets a back-off and pauses on a preempted replay). Exponential price back-off (a day is simple and a study price only matters once a window closes). Replacing `price_failures` entries with text (the account and the holiday check read them as symbols). Adding study currencies to the FX stage explicitly (already covered by stored bars). Pinning only provider and model (the profile name still helps the reader).

## 2026-09-25 - Holiday watch: a stdlib-only Python package, thin PowerShell launchers
**Context:** The holiday watchdog must keep running when the app itself is broken, and the user
wants its decisions (triggers, fix-run limits, which claude processes die) tested before leaving.
**Decision:** `holiday_watch/` at the repo root, stdlib only, never importing `oracle` (a test
checks), so an app import bug or a broken dependency cannot stop the watch, and every decision is
a pure function tested with fakes. Side effects (HTTP, process list, spawn, kill, claude runs) sit
behind one `System` and one `Http` seam; `--dry-run` swaps POST, spawn, kill and run for log lines
and keeps its files in `holiday-reports/dry-run/`. Task Scheduler calls one thin `run.ps1`. Fix
runs run under a detached supervisor (`fix-run`) holding a lock, so a pass never waits; the pass
kills a run the supervisor failed to stop. Fix runs act only through `python -P -m holiday_watch`
(`status`, `api` with GET anything and POST an allowlist, `restart-app`, `start-ollama`, a
read-only `sql`), with a `dontAsk` settings file that allows edits only in the `holiday-fix`
worktree. Each pass step fails alone into the log. A server is killed only when status failed
three tries and its PID still has the start time status last reported, and either its port is
closed and it was down on the pass before, or it has been down 4 passes in a row (about 2 h) with
its port open: a hung event loop still accepts connections.
**Alternatives:** Plain PowerShell scripts (the HANDOFF plan; untestable without real processes);
reusing `oracle` code for API shapes (couples the watch to the app it guards); psutil for the
process list (a dependency; PowerShell CIM gives CPU time and command lines).

## 2026-09-25 - Holiday reaper kills headless claude only; fix-run pytest is an accepted hole
**Context:** Gate round 1 on holiday-watch: a Remote Control session opened by hand with
`/remote-control` leaves no mark in its command line, so the reaper could not tell it from any
other interactive session and would kill it when idle; a `/T` kill could take a protected child
with it; fix runs could run edited code through their own watch copy, `--config` or pytest.
**Decision (coordinator):** `reaper.reap_interactive = false` by default: only headless claude
(`-p`/`--print`) that is idle or past its limit is killed; an interactive one is logged once as
"would reap". Idle interactive sessions cost no usage. A Remote Control session (PID file, or
`--remote-control`, `--rc`, the `remote-control` subcommand) is never reaped, even with the flag on.
A process whose tree holds a protected one (Remote Control, a fix run in its window, the watch's
ancestors, the app server's tree) is only logged, never `/T`-killed. `claude.exe` is matched by
executable name, node only when running Claude Code. Fix runs call the watch with `python -P` and
PYTHONPATH at the repo, only its listed subcommands, never `--config` (refused in code and denied),
never edit `holiday_watch/` or the reports. The fix run's `pytest` on the holiday-fix worktree
stays allowed: fixing code needs it, and a test it writes can do anything the user's account can.
The prompt forbids tests that open the real DB or the live worktree; that is the only guard.
**Alternatives:** Killing idle interactive sessions (kills a hand-opened Remote Control session);
no pytest for fix runs (a code fix could not be checked); a real sandbox (none on this PC).

## 2026-09-25 - Universe backfill from index members, and discovery adds what Yahoo lists

**Context:** FinanceDatabase lacks big home lines (LG Energy Solution, LG Chem, LG Corp, LG Electronics' 066570.KS) and files Samsung C&T under a wrong code as Micro Cap. The user wants the universe as full as possible, and discovery to add the listed companies it finds (discovery-universe/05).
**Decision:** Each market's main index member lists are read from Wikipedia (the Wikimedia REST API, cached a week, CC BY-SA recorded in THIRD_PARTY.md) and each member whose line is missing is added from a Yahoo quoteSummary profile (band from Yahoo's market cap in USD, same cut-offs; the company's FinanceDatabase classification wins over Yahoo's). Only EQUITY lines that are not depositary receipts and not on the never list join. Added lines and corrections (a line under the wrong code, delisted only when Yahoo says it has no such line, or a band two or more bands off Yahoo's) live in their own tables (m0042; renumbered after holiday-hardening's m0041) and are laid over every FinanceDatabase refresh, which drops an added line once FinanceDatabase has it. The backfill is its own Activity job (kind `universe`, so it queues behind a refresh and never runs inside a tick), 150 Yahoo lookups a run, round robin over indexes, resumable (failed members rest 30 days), and a 429 stops the run keeping what was added. A GUI refresh queues one after it; `oracle universe refresh` keeps backfilled lines but leaves the minutes of lookups to `oracle universe backfill` (which the Universe tab's "Index backfill" section also runs by hand), so a scripted refresh stays quick. Discovery (brands' Yahoo lookup) adds a listing "not in the universe" through the same guards (and never an OTC line: PNK, OQX, OQB) only when the quote carries the name, or Yahoo ranks it far first and its Yahoo market cap is Mega or Large Cap, never a line already in the universe, each add spending one of the answer's MAX_LOOKUPS, reason "... added to the universe from Yahoo". That single add inserts the one row at once in the company `place_line` gives it (the first stored line sharing an ISIN or name and country) instead of a full regroup (about 11 s on 150k rows against about 2.5 s); the next refresh or backfill regroups it properly. It is skipped while a refresh holds the universe lock.
**Alternatives:** Only copying existing rows (size-mix's `MISSING_HOME_LINES`, cannot add a line FinanceDatabase lacks). Paid constituent feeds. A full regroup per discovery add (five adds could cost a minute). Leaving the discovery reason as "not in the universe" and adding nothing (the user asked for adds).

## 2026-09-25 - Holiday arm/disarm: preflight in a service, tasks from XML, pythonw direct
**Context:** holiday-watch/02: one step at 18:00 (App-section card + `oracle holiday arm|disarm|status`)
that refuses on any failed check, then starts the study and registers the watchdog and report tasks.
Ticket 01's gate asked for tasks "only when logged on" with no execution time limit, and exactly one
Remote Control session.
**Decision:** `oracle/services/holiday_service.py` holds the checks and the steps; every side effect
(HTTP, process list, disk, powercfg/GetSystemPowerStatus, SQLite backup, schtasks, the watch as a
subprocess, the Remote Control console) is on a `HolidaySystem`; only `real_holiday()` builds the real
one and the suite's conftest replaces it with one that raises. The service imports the stdlib
`holiday_watch` package (its config, process list and reaper matchers) so the checks see what the
watch will; `holiday_watch` is now packaged too. Arm settings live in the watch's config (`[arm]`:
min_free_gb 20, backups `{repo}/../oracle-backups`, pass_every_min 30, report_time 07:00,
expect_symbols). Tasks are registered from Task Scheduler XML (`schtasks /Create /XML`), since the
schtasks flags cannot set "no time limit", battery behaviour or a working folder: InteractiveToken,
ExecutionTimeLimit PT0S, IgnoreNew, runs on battery, WakeToRun, first pass 2 min after arming. The
action runs the watch config's python (pythonw beside it when present, so no console flashes every
30 min) with `-m holiday_watch pass|report --config <file>` in the folder holding the package (the
live worktree when armed from the live app), not `run.ps1`: the live worktree has no `.venv`, which
`run.ps1` falls back to. Status accepts a running study; arm needs it paused. A failure after the
study starts deletes both tasks and pauses the study again. The live check is `pass --dry-run` then
`status`. Zero Remote Control sessions is no failure: arm starts one in a new console and writes the
PID file; two or more refuse. "Model change pending" = oracle.toml's model settings differ from the
ones the running app holds (or the file no longer loads); the study's pinned models are the separate
"Replay models" check.
**Alternatives:** Arm inside the stdlib watch package (cannot read the study or cutoff without
`oracle`); `schtasks /SC MINUTE /MO 30` flags (72 h default limit ends spawned children, no working
folder); `run.ps1` via powershell (needs HOLIDAY_WATCH_PYTHON, which a task cannot set, and flashes a
window); failing arm on zero Remote Control sessions (one more manual step at 18:00).

## 2026-09-25 - Holiday arm gate round 1: updates paused, task limits, live worktree
**Context:** Gate 02 round 1: a Windows Update restart would stop every logged-on task until sign-in;
the live check could fail and still say "Armed"; a CLI arm ran the watch from the dev checkout;
IgnoreNew with no time limit lets one stuck run block every later one.
**Decision (coordinator):** Arm refuses while a restart is pending (RebootRequired / RebootPending
keys) or while updates are not paused until at least now + `[arm] away_days` (6); the fix is Pause
updates. Automatic sign-in after an update (ARSO) off or unreadable is a warning, never a refusal; no
password is ever stored. Registry reads only, on the seam. The live check's exit code counts: a
failing `pass --dry-run` fails "Live check" and undoes arming. Tasks and the live check run in
`cfg.live`, which must hold `holiday_watch/__main__.py`; `--config` is absolute. IgnoreNew stays, with
ExecutionTimeLimit PT20M (watchdog) and PT30M (report): spawned children break away, so the limit only
ends a stuck pass; status warns past 25 min Running. A battery means the lid action on mains power must
be Do nothing. A rollback closes a Remote Control console arm started; `arm.lock` keeps one arm or
disarm at a time; disarm ends a running fix run first (it could start the study again).
**Alternatives:** Parallel instances (reviewer's fallback if the live test shows a stuck instance);
storing a password for "run whether logged on or not" (never); no time limit (a stuck pass blocks
the watch for days).

## 2026-09-25 - Discovery data: sector corrections, dead companies, theme words (discovery-data 01)
**Context:** Session 15 reviews: FinanceDatabase files Rheinmetall as a US retailer, Siemens Energy as a utility, Arista as a financial and more, hiding them from discovery screens; acquired companies (Dialog Semiconductor, Meggitt) stay screened and cost a price check every answer; theme words ("robotics", "solar", "fintech", "AI chips", "EVs") screened nothing.
**Decision:** A tracked table `oracle/universe/corrections.py` (company key -> sector, industry group, industry, and country only when that is wrong too; 43 companies: the review's six plus UK housebuilders, and a scan of Small Cap and up names saying Bank, Pharma, Semiconductor, Mining or Software filed elsewhere, each checked by hand). It is applied when read, never written to `listings`: the universe screen (in SQL, a VALUES join), name matching (`Screen.profile`, search hits), `BusinessFit.misfit`, and discovery's related peers (`choose_peers(corrected=True)`). Research prompts, the research market tool's peers, the factor analysts and the GUI/CLI peers (which show the analysts' peers) keep the raw data while the research loop is frozen. A screen price check that finds no data or only old bars marks every line of the company dead at once (listing_health) when no line has recent bars stored and the best other line, if any, answers no better; with no check left for that second opinion, or sources not answering, nothing is marked. Name trade words already map to sectors (bancorp, medical, therapeutic, diagnostic, bioscience, furniture, from 03/04); a question's written words now count as said alongside their aliases. Aliases: robot(ic)s -> Machinery, solar -> Independent Power and Renewable Electricity Producers, fintech -> Consumer Finance, "AI" names no industry ("AI chips" = chips), EVs -> Automobiles, housebuilder -> Household Durables (batteries and defence were already there).
**Alternatives:** Writing corrections into `listings` at refresh (every reader, research included, would change mid-freeze, and a live DB needs a refresh to see them); a DB table with a GUI editor (no one edits it yet; a follow-up once the freeze lifts); marking dead after the usual 3 no-data days (the ticket wants the next answer to skip it); burying on one failed line alone (a transient 404 on an OTC line would bury a live company); themes mapped to several industries (solar panel makers sit in Technology Hardware and Semiconductors, which would list Apple and Nvidia).

## 2026-09-25 - Screen burials need real death evidence and expire (discovery-data gate round 1)
**Context:** Gate round 1: burying on plain staleness (3 weekdays) would kill healthy companies over Golden Week, Easter or a halt, permanently, held positions included; one all-sources "no data" answer buried a single-line company.
**Decision (coordinator):** A screen buries a company only on no data from every source on 2 separate days, or a last bar over 30 days old; a merely stale line is skipped for that answer. No line watched or held on any paper account is ever buried. Screens (universe screen home lines, the named-company primary line) skip only burials under 30 days old; an older one is checked again, cleared on prices, buried again from that day otherwise. This expiry applies to every dead listing a screen meets, the 3-day dead mark included; a held or watched line is never buried or skipped by price updates, dead flag or not, so a lost feed can never silence its stop, though replays still read all dead listings. A refresh or backfill logs and reports correction keys matching no company (job result, CLI, Universe tab).
**Alternatives:** Keeping plain staleness with an expiry only (buries a week of Chinese companies every Golden Week); a separate burial reason column (a migration, for a flag the date already carries); expiring only screen burials (needs that column).

## 2026-09-25 - Earnings event study: SEC 8-K 2.02 dates, timed by acceptance, stored in meta (event-studies 01)
**Context:** Phase 9's first slice: how a listing moved after past earnings, from stored bars, not fed to research (frozen).
**Decision:** Release dates are 8-Ks listing item 2.02 in the SEC submissions' recent list (the `items` column), one per day, looked up by the company's primary listing, so APC.DE uses AAPL's filings but its own closes. A filing accepted before 09:30 (SEC's acceptanceDateTime is New York time despite its Z) is measured from the previous close, otherwise from the release day's close; no time means the release day's close (after-hours, the ticket's default). Moves are 1, 5 and 20 trading days, adjusted closes only as a pair, and against the first configured compare benchmark with stored bars (its closes on or before the same dates). A window with a zero close or a day-over-day move outside 0.5x to 2x is "price series break, skipped" via the shared guard in `oracle/analysts/breaks.py`. Summary: median, count up and beat-the-benchmark over the latest 8 measured releases per horizon. Dates are cached as JSON in `meta` (`earnings_dates:<ticker>`), refetched when a week old; a failed fetch keeps them and says so. Non-US companies say there is no source. The fetch shares the research filings tool's ticker map and submissions call (now `research_tools.filings.submissions`).
**Alternatives:** Yahoo for non-US (quoteSummary's calendarEvents gives only the next date and earningsHistory gives fiscal quarter ends, not release days; the past-dates page is an unofficial scrape); always measuring from the release day's close (misses the whole reaction of pre-market reporters, most of the S&P); a new table (a migration number while several branches add migrations; a JSON list per ticker is enough until research reads it point in time in 02); older releases from the submissions' extra `files` pages (the recent list holds at least a year or 1000 filings, so 4 to 8 or more releases; a follow-up if too few).

## 2026-09-25 - Price factors: 12-1 momentum and 60-day volatility, point in time, for reading only
**Context:** quant-basics/01. Momentum and low volatility ranked across the watched, held and discovered set, safe to backtest (ticket 03's momentum baseline calls the same function on each rebalance day), while the research loop stays frozen until the replay study reads out.
**Decision:** `oracle/analysts/price_factors.py` is pure: `factors_at(bars, day)` drops every bar after `day` before reading any, and `ranked_at(history, day)` ranks a set, so 03 can call it with each listing's bars on a rebalance day. Momentum is the close on or before a year ago to the close on or before 30 days ago (calendar days, so gaps and holidays do not shift the window); each anchor close must be within 10 days of its target, else "less than a year of prices" and the listing is marked, not ranked. Prices that stop more than 10 days before the day give no figures ("no price since"). Volatility is the sample standard deviation of the last 60 daily log returns, times sqrt(252). Adjusted closes when every bar in the window has one, plain closes otherwise. Within one fetch a ratio of adjusted closes depends only on events between the two days, but stored bars span several fetches (each update refetches a week), so a split between fetches leaves older rows on the old basis, and the seam can sit before the split's own date. So a window with a day-over-day move outside 0.5x to 2x, or a zero close, is left unranked: "price series has a break on DAY: needs a full refetch" (gate round 1; the guard is `oracle/analysts/breaks.py`, shared with the replay score). Seams smaller than that (a dividend's) stay in. Ticket 03 needs a whole-window refetch per listing, or relies on this guard. An as-of day one day past UTC today is clamped to today (a time zone east of UTC), and an earlier day still ranks today's set. Percentiles use the peer factors' `percentile` (ties half) against the others with the figure; fewer than two gives no rank. The set is every watchlist symbol (the discovered list included) plus any paper holding; `--list` and the watchlist column show one list but rank across the whole set, so a rank means the same everywhere. A company outside the set is ranked against it on its Company tab, marked so. A later as-of day is refused. Nothing in research, analysis or the verdicts reads these.
**Alternatives:** Bar counts (252 and 21 trading days; wrong across exchanges with different holidays and across fetch gaps). Ranking within the shown list only (ranks would change with the filter). Raw closes always (splits read as crashes). Feeding the ranks into the verdict's stored-data line now (the loop is frozen; design doc "Verdict input" waits for the replay readout).

## 2026-09-25 - Momentum baseline account (quant-basics 03)
**Context:** The design's factor baseline: the top N by 12-1 momentum, rebalanced monthly, drawn beside the verdicts in the replay study and in `oracle backtest`, under the same costs, to tell whether LLM research adds anything over a simple rule.
**Decision:** Pure `oracle/broker/momentum.py` makes a `LeanSource` through the engine's existing lean hook: ranks from `price_factors.ranked_at` on the last day of the month before each day (so every lean in a month reads bars only up to that month end); the top N lean buy for the whole month, the rest of the ranked set lean sell, unranked listings (short history, a price break) lean nothing. A whole-month buy lean, not a one-day signal, because the rules count neither pending sell proceeds nor freed slots on the day of a sell, so a one-day rotation would fail; the cost is that a stop-out is bought back the next day while the listing stays in the top. Probability 1.0 (the rule always acts). N is the rules' position limit (10 by default); an optional low-volatility blend weight exists in the function but is not exposed. Replay study: `BASELINES` entries are now factories `(bars, rules) -> (label, caption, lean)`, fed the account's own adjusted bars from the study's whole-window refetch (it reaches 400 days before the first as-of day, which covers the 375-day lookback from the first month end); the verdicts', control and buy-and-hold lines are unchanged (test). Backtest: a third line on stored bars, relying on `factors_at`'s break guard (said in the run's notes); runs kept from before have two lines and still read back. No migration, no new CLI flags, so no new GUI control.
**Alternatives:** Weight rebalancing to equal weights each month (the engine has no top-up order); ranking on the first trading day of the month (needs the panel's calendar, same effect); a whole-window refetch before each backtest (network in a read-only job; the guard leaves broken windows unranked instead); disabling the stop for this line (the comparison is meant under the same rules).

## 2026-09-25 - Momentum baseline buys in rank order; its caption says how many fit
**Context:** quant-basics 03 gate L1/L2. Every buy lean had probability 1.0, and the rules buy by `(-probability, symbol)`, so when cash or slots ran out the last slots went alphabetically. At default risk (20% cash floor, 10% positions) only 8 of the top 10 fit, and a stop-out is bought back the next day while the listing stays in the top; the caption said neither.
**Decision:** A buy lean's probability is `1.0 - 0.001 * rank position` (0.991 at the 10th, well over `min_probability`), so the leaders are bought first; sell leans stay 1.0 (the rules sell on the label). The caption takes the rules and says "about N at full size under the cash floor; risk sizing can hold more" when full-size positions under the cash floor fit fewer than the top N (smaller risk sizes for volatile leaders, and a last partial buy, can hold more; gate 03 L1), "leaders bought first", and "a stop-out is bought back while it stays in the top". N stays the position limit.
**Alternatives:** Set N to what fits (8) (changes which names rank as sells, and the line would never refill a 9th slot freed by cash growth); pass the rank as a separate sort key (the rules take one probability for every lean source).

## 2026-09-25 - One cutoff rule for a replay study's pacer, Activity retry and score
**Context:** holiday-hardening low 2. `pinned_cutoff` (score, status, `retry_failed`) raised a pinned model's probe to the claim of every profile naming that model, while the pacer and the Activity retry of a study cell used the slot profiles' claims. Another profile of the same model with a later claim made the pacer run cells the score then dropped.
**Decision:** `pinned_cutoff` is the study's one cutoff and the pacer and Activity retry now use it too: each pinned model's probe, raised to the current claims of the profile the study pinned and of the stage's slot profile (the one the run uses), each only when it names the pinned model. A claim on any other profile is ignored. Moving the slots to another profile of the same model can only raise the cutoff, the safe way (gate 03 M1). When the pinned profile is the slot profile (the normal case) nothing changes. The pacer's stop message names the profile whose probe or claim set the cutoff. Model changes and unprobed models still pause through `cannot_run`.
**Alternatives:** Every profile naming the model for all three (a claim on an unused profile would stop cells); the pinned profile's claim alone (a later claim on the profile the run now uses would be ignored: look-ahead).

## 2026-09-25 - Earnings dates from acceptance time; failed refreshes wait an hour (event-studies gate round 1)
**Context:** Gate round 1: an 8-K accepted after 17:30 gets the next business day as its filingDate, so an after-close release filed late was measured from the day after its reaction; a lagging benchmark read as a zero move; a failed SEC refresh showed research-tool wording and retried on every Company tab open.
**Decision:** The release day and time come from acceptanceDateTime when it parses, filingDate only without one. A benchmark with one bar for both window ends gives no excess (and no beat). A failed refresh says "could not reach SEC: kept the dates from DAY" (or "try later" with none stored), is remembered in meta (`earnings_dates_failed:<ticker>`) and not retried within an hour; a missing contact is said without trying.
**Alternatives:** Keeping filingDate and shifting it back for late acceptance times (the same result, more rules); retrying failures on every open (paced and breaker-guarded, but pointless while SEC is down).

## 2026-09-25 - Fill passes: prices, FX and fills at 09:30 and 15:15 on weekdays
**Context:** Orders placed by the evening tick sat pending until the next evening's tick fetched the next day's bar, so the AI account traded a day late in the ledger.
**Decision:** `[schedule] fill_times` (default `["09:30", "15:15"]`, local, weekdays, `[]` off) runs a third kind, `fill` (stored `fill/<trigger>` like midday), through the tick's run, lock and Activity queue: stages `prices` (watched, held, every listing with a pending order, benchmarks; not the replay study's prices), `fx`, `fill`. Each time must be at least 30 minutes (round the clock) from the tick, the midday pass and the other fill times: refused on write, dropped with a warning on load. A fill pass never queues: if any tick-lane job is queued or running here it is recorded skipped, and one held by another process is skipped through the lock. It has the midday pass's 30-minute grace (never fired late, missed ones recorded, dated at their due time so a missed 09:30 never covers 15:15). The replay pacer sees fill passes through `Scheduler.next_run` and yields as for a tick. The warning banner (postpone, skip) covers the tick and midday pass only. The tick's prices stage now also prices listings with a pending order. Fills are idempotent already: a filled order is no longer pending. Separately, a step skips a buy smaller than one share at the last close (base currency), "one share costs more than the position size", so it holds no slot or cash (`decide(share_costs=...)`; the backtest does not pass it yet).
**Alternatives:** Moving the tick earlier (research and steps would run on a half-finished day); warning before fill passes (twice a day of banner for a minute's work); queueing a fill pass behind a running tick (could delay the next run); filling same-day orders before the exchange opens (needs exchange open times, which are not stored: follow-up).

## 2026-09-25 - Investment trusts refused like funds, and a sweep for the ones already added
**Context:** The first live backfill added FTSE 250 investment trusts (IAD.L, JMGI.L, JEDT.L, JEGI.L and others) as equities: Yahoo types closed-end funds EQUITY.
**Decision:** `refusal` also refuses a closed-end fund ("a closed-end fund (investment trust)"), for the backfill and discovery adds alike. A line is one when its name says "investment trust" (not "real estate investment trust"), its Yahoo summary says closed-end or "is a ... fund" / "is an investment trust" (what the line is, not what it sells: Hua Nan "raises investment trust funds"), or Yahoo gives it no business profile at all (no industry, no summary, which is how Yahoo lists London's trusts) and it is on LSE or named Trust, Fund or Ord. Checked against the dev DB's 431 backfill rows: it finds the 10 London trusts plus FEML.L and FGEN.L and no company. A brand-new London listing Yahoo has no profile for yet is refused too, and tried again after the usual 30 days. `oracle universe funds [--remove]` and the Universe tab's "Find investment trusts" then "Remove N" list the added lines the rule matches and, only when told, drop them from the universe (line and search entry, company primary repicked, no full regroup) and note each as a failed try so the backfill does not ask Yahoo again. Held or watched lines are listed as kept and never removed. FinanceDatabase's own trust rows are untouched.
**Alternatives:** A hard-coded FTSE 250 closed-end list (goes stale, misses other markets); Yahoo's fund category (not in the profile modules the backfill reads, one more call per line); a full rebuild after removal (a 149k-row regroup for a handful of deletes).

## 2026-09-25 - Backfill corrections listed and undone by symbol; FinanceDatabase's row returns at the next refresh
**Context:** A backfill correction (delist, rename, band) is laid over every refresh and had no undo (gate 05 M1 follow-up); stale sector correction keys were only printed after a refresh or backfill.
**Decision:** `oracle universe corrections list` (and the Universe tab's "Show corrections") lists each correction with what it lays over and why, plus the stale sector correction keys. `oracle universe corrections undo SYMBOL` (each row's Undo) drops the correction row. The symbol is the ID: `backfill_rows` is keyed by it and a line has at most one correction. The stored line keeps the corrected values until the next universe refresh lays FinanceDatabase's own row back, since the row a correction replaced is not kept; both the CLI and the page say so.
**Alternatives:** Keeping the original values beside each correction to restore at once (a data format change, and the corrections already stored have none); a rebuild on undo (restores nothing: the stored lines already carry the correction); a numeric row ID (rowid changes on every re-correction).

## 2026-09-25 - A wrong-code rename never splits a company (gate 05 L1)
**Context:** A wrong-code correction took Yahoo's name verbatim, so a share-class name ("... Class B") split the line into its own company at the next regroup.
**Decision:** The rename is skipped (no correction at all) when Yahoo's name and the line's are one company by blind name (one is the other's leading words, legal forms aside). A line Yahoo names as another company is still renamed and split off, whatever other lines its company has: those lines joined it by the very name in doubt, and keeping it made the wrong company's line the primary (gate 06 H2).
**Alternatives:** Renaming but keeping the old company key (a regroup recomputes keys, so it would not hold); comparing exact blind names only (misses "Class B" suffixes).

## 2026-09-25 - The closed-end fund rule tightened on live data (gate 06 H1, M1, M2)
**Context:** Gate 06 round 1: the first rule took REITs (SPG, SEGRO, Tritax Big Box, J-REITs), companies that only mention closed-end products (BlackRock, Canadian Tire, E-L Financial, Golub BDC), Federal Realty and PennyMac by name, and BT Group (a London line with no Yahoo profile) for funds.
**Decision:** `closed_end_fund(name, industry, summary)` returns why, or None. Never a fund: a REIT or real estate industry, or a name with REIT, real estate, realty, mortgage or property. A fund when named "investment trust", when the summary's first 200 characters say the line itself "is a ... fund / investment trust" (no property word in between) or "is a closed-end ...", or when Yahoo has no profile at all and the name carries Trust, Fund, Ord or Income. Ord and Income are kept beyond the gate's Trust/Fund because five of the 12 London trusts (ALW, JEDT, JEGI, JMGI, PINT) carry neither; BT Group carries none. The sweep shows the why on each line (CLI and page) and keeps a line with a pending order as well as held or watched ones. On the live universe (read only) it matches the same 12 added trusts, and among Mid Cap and larger live equity lines only funds on primary lines (ETFs Yahoo types EQUITY, closed-end funds, investment trusts). Gate 06 round 2 found one non-primary asset manager (PIJ.F Platinum, "is a publicly owned hedge fund sponsor"); the summary match now skips a fund followed by sponsor, manager, management, services, administration or company, and leading words with "of" or "and" ("a provider of corporate and fund services"), while "is a fund of ..." still matches. A trust named "Mortgage" (Scottish Mortgage) is missed unless Yahoo gives it no profile and "Ord".
**Alternatives:** Name "Trust" or "Fund" only for the no-profile branch (misses five of the 12); matching "closed-end" anywhere (catches operating companies).

## 2026-09-29 - Regime core: a package under broker, flat `[regime]` config keys
**Context:** Ticket 01 of the regime lines needs a pure, point-in-time book (method scores, mood, weights, leans) and the spec's preset weights table overridable in config.
**Decision:** `oracle/broker/regime/` (settings, mood, methods, book; 02 adds its playbook and health there). `[regime]` holds each weight as a flat float key `<mood>_<method>` (32 keys) plus `smoothing_span`, `sector_share`, `warning_factor`, `good_factor`, `playbook_cap`, `tilt_cap`, so the Settings form edits each as a number; `broker.regime.settings_from(asdict(config.regime), max_positions=..., min_probability=...)` builds `RegimeSettings`. A test keeps the config defaults equal to `PRESETS`. A mood whose weights are all 0 is refused. The on/off switches belong to tickets 04 and 05.
**Alternatives:** A nested `[regime.weights.<mood>]` table (the Settings form lists one level of keys only); a list per mood (the form sends lists as text).

## 2026-09-29 - Regime method scores: scales and guards
**Context:** The spec names each method but leaves its scale open.
**Decision:** Trend: (close / 200-day average - 1) / 10%, clipped to [-1, 1], halved when the average's 20-day slope points against the gap. Reversion: RSI(2) under 10 above the average +1, over 90 -0.5, else 0. Both need 219 bars since the last price break (the breaks guard), so a split the bars were not re-based for never scores. Momentum and low volatility read `ranked_at` on the momentum line's rank day (last day of the previous month). Earnings drift: the reaction bar is the release day's own for a before-open release, else the next bar (within 7 days, not a break); its move minus the composite's that day, / 5%, clipped, fading linearly to 0 over 60 bars. Value and quality: the newest dated score on or before the day, up to 366 days old. The AI verdict: the newest on or before the day within `verdict_max_age` (7 days, the AI account's rule); `LABEL_SCORES` maps labels to -1, -0.5, 0, 0.5, 1. A listing leans only with at least one price method's score (trend, momentum, reversion, low volatility): renormalised, buy-and-hold's constant +0.3 alone would read as a buy.
**Alternatives:** Distance scaled by volatility (an extra window per listing, same sign); the release's filing day as the reaction day (after-close releases would be measured a day early).

## 2026-09-29 - Regime mood: composite, correlation sample and percentiles
**Context:** The mood must be one cached reading per day, deterministic and cheap enough for a multi-year backtest in pure Python.
**Decision:** The composite is the equal-weight mean of each listing's close-to-close return in the base currency where rates are given (a step outside the breaks guard dropped), over every day any listing has one. Percentiles (volatility, correlation) are the share of the trailing 252 readings the day beats (ties half) and need 20 of them. The correlation sample is the first 60 listings in SHA-1 order of symbol that have returns on 54 of the window's 60 days (missing days count flat, so one exchange's holiday does not drop a listing); the mean pairwise correlation comes from the variance of their standardised sum (exact, linear in the sample). No mood before 252 composite days. The whole mood series is computed once at build (rolling, so causal); weights are computed day by day and cached.
**Alternatives:** Requiring every day present (drops cross-exchange listings on each holiday); all pairs directly (quadratic, far slower without numpy).

## 2026-09-29 - Regime leans: probability, sector cap as a replay hook
**Context:** The rules buy by probability above `min_probability`; the sector cap needs to know what is held, which a per-listing `LeanSource` never sees.
**Decision:** A buy lean's probability runs from `min_probability` at the buy threshold (score 0.2) to 1.0 at score 1, so every buy label clears the rules and the strongest is bought first; other labels get 0.5 + 0.5 x |score|. The sector cap is `RegimeBook.cap(leans, held)`: at most ceil(sector_share x max_positions) holdings plus new buys per sector, likeliest buys kept, the excess leans hold; a listing without a sector is not capped. `broker.backtest.replay` takes an optional `cap` that sees each day's leans with holdings and pending buys before `decide`; the live step (04) calls `book.cap` the same way.
**Alternatives:** Capping buy leans per day without holdings (holdings could pile up past the cap across days); probability 0.5 + 0.5 x score (a buy between 0.2 and the threshold would be a buy label the rules never act on).

## 2026-09-29 - Regime playbook: ten signs, each from a study
**Context:** Ticket 02 of the regime lines: named warnings (x0.5) and good signs (x1.25) per method, combined multiplier clipped to [0, 1.5], each a tested predicate with a reason and a source shown on the card. A long list would be curve-fitting by another name.
**Decision:** `oracle/broker/regime/playbook.py` `SIGNS`, pure tests of the day's mood, flags, readings and the methods' top-fifth picks. Warnings: trend on `choppy` (Hurst-Ooi-Pedersen 2017, whipsaw) and `rebound` (Garg-Goulding-Harvey-Mazzoleni 2021, turning points); momentum on `rebound` (Daniel-Moskowitz 2016, momentum crashes), `clustered`, market-wide correlation as a proxy for comomentum (Lou-Polk 2022), and `sector_crowded`, over half of at least 4 picks in one sector, a proxy: much of momentum is industry momentum (Moskowitz-Grinblatt 1999), so a one-sector basket carries that industry's reversal risk; reversion on `volatile_down` (Connors-Alvarez 2009, RSI-2 buys only above the average); low_vol on `rebound` (Frazzini-Pedersen 2014, low beta lags a snap-back). Good signs: trend on `big_year`, the year's move either way in the top 20% of the three years before (the mood's `move_pct` reading; Moskowitz-Ooi-Pedersen 2012, the smile); quality_value on `rebound` (Asness-Moskowitz-Pedersen 2013, value leads out of a bear market) and `volatile_down` (Asness-Frazzini-Pedersen 2019, flight to quality). Earnings drift, hold and the AI verdict have none. On synthetic markets (150 listings, 5 years, two seeds) big_year fires on 29% and 11% of days.
**Alternatives:** Signs for every method (no study ties earnings drift, buy-and-hold or the verdicts to these readings). Signs that restate a mood the presets already weight, dropped as the same evidence counted twice: momentum in a calm up year (Cooper-Gutierrez-Hameed 2004), momentum in volatile_down (Daniel-Moskowitz 2016; the preset is already 0 there, so it would only show on the card), reversion in volatile_up (Nagel 2012; the preset already triples it), low_vol and trend in falling moods. A fixed 20% bar for big_year (an ordinary bull year; fired on half the live days).

## 2026-09-29 - Regime health: top fifth over the composite, completed windows only
**Context:** The spec's shadow record per method, mapped to a multiplier in [0.5, 1.5], with no look-ahead.
**Decision:** Each composite day, each method's picks are its top fifth (rounded up, of the listings it scores), the tie group at the cutoff kept whole, and only scores above 0 (0 is no signal: reversion, drift and verdicts mostly score it); none under 5 scored listings or when every score is equal, so buy-and-hold (a constant) keeps 1.0. A window's excess is the picks' mean return over the next 20 composite days minus the composite's, both from the composite's own per-listing levels (base currency, breaks and thin days folded as in the composite). A day's record is the windows picked in the 60 days whose 20 days have closed by it (picked 20 to 79 days before). Health = clip(1 + mean excess / 0.05, 0.5, 1.5), 1.0 with fewer than 20 windows. The book keeps each day's scores for the leans on it (32 days), so the replay costs the same as before: 150 listings, 3 years, 12.0 s against 11.7 s.
**Alternatives:** Ties filled by symbol (reversion, trend at its +1 clip and the verdicts score in steps, so the record measured an alphabetical basket; gate 02); leaving the cutoff's tie group out (a day with every pick tied would pick nothing); picks' own close ratios (a split or an FX move inside the window would read as return); a hit rate instead of the mean excess (throws away size); the 40 windows that both start and close inside the last 60 days (a thinner record for the same span).

## 2026-09-29 - Regime core follow-ups: stale listings, thin days, zero weights, a required buy threshold
**Context:** Gate 01 found a stale listing leaning forever, holiday and partial days reading as the whole market, all-zero weights falling back to the preset silently, and a default buy threshold unrelated to the account's.
**Decision:** `scores` and `explain` give nothing when the listing's last bar is over `price_factors.MAX_GAP` (10 calendar days) before the day asked; the live step must pass the step day (the replay passes the last bar, so it is unchanged there). A composite day counts only when at least half the listings with a return in the 20 dates before it (every date any listing has, thin ones included) have one; a thin day's returns carry into each listing's next full day, so no move is lost and today's partial session changes nothing until it fills. With every raw weight 0 the day's weights are all 0, `RegimeState.note` says why, nothing leans, and the smoothing resumes from the day before. `RegimeSettings.min_probability` is keyword-only and required, and `regime_book` requires `settings`. Also `rolling_mean` moved to `analysts.indicators`, `clean_from` reuses `breaks.first_break`, and `methods.dated` bisects without rebuilding its date list.
**Alternatives:** A quarter as the thin-day share (a US holiday in a mostly US universe would still read as the market); dropping a thin day's returns (the next day's step would miss that move); falling back to the preset on all-zero weights (silently overrides the playbook and the AI).

## 2026-09-29 - The regime account's switch is its own [regime_account] table, on by default
**Context:** Ticket 04 needs a Settings on/off for the live regime account; ticket 01 owns `[regime]` (the book's weights and factors) and is built in parallel.
**Decision:** `[regime_account] enabled = true`, beside `[ai_account]`, live-applied like it, shown in the Settings form and switched on the Regime reading card (`PUT /api/ledgers/regime`). On by default: it needs no model, and its forward record should start the day it ships. Off, its step decides nothing, the tick stage says "regime account off: nothing decided", and its equity is not recorded (so its record starts when it is first on, like the AI account's).
**Alternatives:** A key inside `[regime]` (a merge clash with 01 and a mix of book settings with the account switch); off by default (loses days of forward evidence).

## 2026-09-29 - The regime step runs straight after analysis, before discovery and research
**Context:** The spec says its own tick stage after prices. It needs the day's bars, FX and the risk analyst's sizes; the research stage can take two hours and a tick stopped part way through would skip any later stage.
**Decision:** Stage order prices, fx, fill, analyse, regime_step, discover, research, step, ai_step. It reads research verdicts by the AI account's freshness rule, so it trades the verdicts of up to `max_verdict_age_days` ago rather than this tick's.
**Alternatives:** After ai_step (fresher verdicts, but an interrupted research stage costs the day's step).

## 2026-09-29 - Regime explanations and readings are stored, keyed by account (migration 43)
**Context:** The Ledgers card shows the mood, weights, warnings and each decision's contributions; rebuilding the book on every page load would read the whole stored market.
**Decision:** Migration 43 adds `strategy_decisions.explanation` (JSON: mood, flags, label, probability, score and every method's part, largest first) and `regime_states (account, day, state, at)`, one reading per account and market day (a later step replaces it). Keyed by account so regime-ai (ticket 05) reuses both. With no mood the day's row says so.
**Alternatives:** Explanation text folded into `reason` (not structured for the card); computing the card live (slow, and it would show today's reading beside yesterday's decisions).

## 2026-09-29 - What the live regime book reads
**Context:** The book needs base-currency prices for the traded set and the whole market (the mood composite), plus sectors, earnings days, dated factor scores and verdicts.
**Decision:** One book per step over every stored listing's bars from the last 1,100 calendar days up to yesterday (UTC): today's bars are never read, whatever time the tick runs (gate 02 finding 5). A bar is only final once its exchange has closed; bars carry no closing time, the app keeps no exchange hours, and the price fetch re-reads the last week precisely because mid-session bars get stored. Stepping on the last session before today is correct for every exchange at any tick time and costs one session of lag, which matters little when orders fill at the next open anyway. The core's thin-day rule alone would let a half-traded today through once more than half the listings have a bar. Bars are read and converted to the base currency in one pass (`regime_lines.market_history`: companies only, Tesla and funds left out, see below). The traded set is the AI account's (own picks, discovered while `[discovery] ai_trades`, held), with the same never-list rule. Sectors from the universe, earnings days as last fetched from SEC (never fetched by the step), value and quality analyst scores dated when recorded (the shared inputs entry below), verdicts by the AI account's freshness rule. Trailing stored days on which under 80% as many traded listings have a bar as on the busiest of the last 10 days (counting the traded set only, since a replay study's or unwatched listings stop getting bars and would make every day look partial, gate 04 H1) are an exchange holiday or a failed fetch (with today never read, no session in them can still be open): the step reads up to the last complete day, so the mood never sees a half-market day. Each listing is explained at that step day (so ticket 02's gap guard applies), and the book's sector cap runs before the rules with every holding and pending buy counted. Verdicts reach the book as `LABEL_SCORES` of every live verdict within the age limit, dated by report day (a later report without a verdict hides the older ones, as on the AI account), so the book picks the newest on or before the step day, with `verdict_max_age` from `[ai_account] max_verdict_age_days`; value and quality combine as the mean of each one's newest score. A listing whose last bar is over 7 days behind the market's is skipped as stale; one gone from the universe, one without prices, and every listing on a day with no mood are skipped saying why.
**Alternatives:** Full history (slower; nothing older than about two and a half years changes the mood, health or scores); the traded set only for the composite (a mood read from a dozen listings).

## 2026-09-29 - One regime book input rule, live and replayed
**Context:** Ticket 03 adds a `regime` line beside momentum and buy-and-hold in the backtest and the replay study account. Gate 03 round 1 found the replayed line and the live account (04) building their inputs differently (market set, which listings get sectors, earnings and factors, factor dating), so the backtest did not measure the live book, and the market read included Tesla and two ETFs.
**Decision:** `services/regime_lines.py` holds the one rule both use. `market_history(ctx, start, end, traded)`: every stored listing the universe calls an equity, plus the traded ones, never a never-listed company (`never_list.never_company`, Tesla), funds left out (they count the market twice in the mood and rank beside companies); it feeds the mood, ranks and health, and only the traded set is traded. `regime_inputs(ctx, symbols, end, verdicts)` for every listing the book reads: sectors from the universe, earnings days from stored SEC releases (never fetched), value and quality combined as the mean of each one's newest score and dated at the later of the signal's as-of day and the UTC day it was computed (`SignalRepo.recorded`), so a past day never reads a score before it existed; live, a signal computed today counts from the next step (one day of lag). Verdicts: live, every fresh live verdict (04's rule); replay study, the replay verdicts (`LABEL_SCORES`, as-of dated, the AI account's freshness); plain backtest, none. Replayed lines read from 1,100 days before the first day to the last (never after); the replay report builds one book for the whole account and every size band. The caption names each input with counts, says the mood reads today's stored listings back in time (survivorship), and warns when the start comes before the first 12-1 momentum rank.
**Alternatives:** The mood from the list's or study's listings only (a noisy composite and ranks unlike the live account's); inputs for the traded set only (health's top-fifth picks and the playbook's sector counts would read a different market than the mood); factors dated by as-of day alone (a recomputed signal would leak later fundamentals into earlier days).

## 2026-09-29 - The regime-ai line: one validated model tilt per step day, stored, past days replayed
**Context:** Ticket 05: a second live line trading the regime book with a daily model tilt of the weights within `[regime] tilt_cap`, judged forward only against the rules-only `regime` line.
**Decision:** Account `regime-ai` reuses the regime line's step, book and never-list rules, and the same rule for its traded set (own picks, discovered while on, its own holdings, so the two sets differ once their holdings do), generalised by account (`regime_step(ctx, account)`), with its own tick stage `regime_ai_step` straight after `regime_step`. `DailyTilt` is the book's tilt: a day with a stored tilt answers from it, the step day without one asks the model, any other day has no tilt, so a book never asks the model about the past and a restart or second step that day reuses the answer. The model gets the day, mood, flags, readings, and per tilted method its preset, rules weight (pre-tilt, smoothed), health (or "no health record yet") and firing signs with reason and source; it returns JSON with a multiplier per tilted method and a reason. The research verdicts (`ai_verdict`) are never tilted: they come from the research model, by default the same one, and it boosted them to the cap with nothing behind it (gate 05 #1) (schema-constrained, temperature 0 on Ollama). Validation keeps known methods' finite numbers clipped to the cap and requires a reason (cut to 300 characters); none left, no reason, not an object, or a timeout or no connection twice is no tilt, logged, stored with the error and shown on the card and as the tick stage's error, while the line trades the rules weights. Tilts live in `regime_tilts (account, day, tilt, rules, reason, error, model, at)` (migration 44), with the rules weights the model saw, so the card shows preset, rules, tilt and final side by side. The prompt text is in `services/regime_tilt.py` only. A call that got no answer at all (an httpx transport error: a timeout, no connection) is asked once more after 45 s, and a second such failure is stored as "failed twice, 45 s apart: ..."; a bad answer is never asked again (gate 05 #2).
**Alternatives:** Keeping tilts inside `regime_states` (written after the book is built and replaced each step, so the book could not read past tilts before building); asking again after a stored failure or a bad answer (lets a flaky model be retried until it says something; a transport failure carries no answer, so its one retry selects nothing); asking the model for past days on the first step (a backtest of the model, contaminated by what it has read).

## 2026-09-29 - The regime-ai tilt model is `[regime_ai_account] model`, the research slot's when empty
**Context:** Local models first; the research engine already resolves Ollama and other profiles.
**Decision:** `[regime_ai_account] enabled = true` (on by default, so its record starts beside the rules line's) and `model = ""`, a model profile's name, empty meaning the research slot's profile, resolved through `models_service.resolve_model`. Both are live settings, in the Settings form and on the Regime-AI reading card (switch and a profile picker, `PUT /api/ledgers/regime-ai`). An unknown profile is refused by the card route and, if set by hand, is that day's stored failure.
**Alternatives:** A new `[models.slots]` entry (a slot unset means `default`, not the research slot, and slots mean research-engine stages); off by default (loses forward days of the only evidence that matters).

## 2026-09-29 - Regime step buttons run as jobs on the tick queue
**Context:** Gate 04 low #1: the Regime step button ran the 4.5 s step on the server's event loop, and could race a tick's own step.
**Decision:** `POST /api/ledgers/regime/step` and `/regime-ai/step` answer 202 with a job of kind `tick` (subject the account, label "Regime step" or "Regime-AI step"), run on the worker thread after any tick queued before it, under the tick's single-flight lock so a tick in another process cannot step beside it. The page follows the job and shows its result; a retry resubmits the same line's step. A scheduled fill pass is skipped only for a real tick, midday or fill run on the queue, not for a queued step (it waits the few seconds instead).
**Alternatives:** `asyncio.to_thread` with a separate guard (a second lock beside the tick's, and no Activity entry); a job kind of its own (would run beside a tick in this process).

## 2026-09-29 - A step day over two weekdays behind yesterday is warned about
**Context:** Gate 04 low #3: a source outage for over a fifth of the traded set keeps every new day partial, and the step silently trades an old day for up to 10 sessions.
**Decision:** `lag_warning` counts weekdays after the step day up to yesterday; over 2 (so a Monday reading Friday, or one exchange holiday, stays quiet) the run carries a warning that the tick stage summary, the CLI, the card and the holiday report show. The card judges it against the day its step ran.
**Alternatives:** Refusing to step (a stale but real session still trades sensibly under pending-order and holding guards); a stage error (it is not a failure of the step).

## 2026-09-29 - A tilt that raises is no tilt inside the book
**Context:** Gate 01 low #5: an exception from `tilt` propagated out of `state()` and would crash the lean source.
**Decision:** `RegimeBook` calls the tilt inside a guard: an exception is logged and that day has no tilt; a value that is not a finite number (text, NaN, a boolean) is dropped. `DailyTilt` also stores its own failures, so the guard is the backstop for any other tilt.
**Alternatives:** Only the service wrapping its tilt (any later tilt would have to remember to).

## 2026-09-29 - A tilt moves only a method with a firing sign or a measured record
**Context:** Gate 05 #1: the smoke call gave ai_verdict x1.5 and nothing else, at temperature 0 a standing daily bias; the prompt allowed moves for "the mood and readings" (true every day, and the preset already prices the mood) and showed a method with too short a record as "health 1.00", a sound record.
**Decision:** The prompt allows a move only for a firing warning or good sign or a measured health far from 1, and says "no health record yet" for a method under the 20-window record (`RegimeState.recorded` names the measured ones; the card shows "no record yet" too). As a structural guard at any model size, `grounded` resets to 1 any move on a method with no firing sign and no measured health, and the stored reason names those kept at x1.
**Alternatives:** A smaller cap (shrinks the bias, keeps it); trusting the prompt alone (a small model ignored it).

## 2026-09-29 - The tilt's direction is enforced in code; the model chooses only how far
**Context:** With each health described in words and allowed as a reason, the smoke call's 4B model wrote "momentum's picks have lagged badly (health 0.50), so we raise its weight" and gave x1.5: it followed the licence to move but not its direction, cancelling the rules' own health penalty.
**Decision:** `grounded` keeps each move only on a side of 1 its method's evidence supports: a firing warning or a measured health below 1 allows a cut, a firing good sign or a measured health above 1 a raise, evidence both ways allows either, a neutral record neither. A move on the wrong side is set to 1 and the stored reason says "kept at x1, a move against its evidence: ...", beside the existing "no sign or record" reset.
**Alternatives:** More prompt wording (the model had the words and still inverted them); a bigger model by default (local models first, and the guard holds at any size).

## 2026-09-29 - regime-v2 is the regime book behind two settings, not a fork
**Context:** Ticket 07: a third regime line with a rotation and without the hold padding (gate 03 round 1's why-regime-trails), while `regime` and `regime-ai` stay exactly as they are.
**Decision:** `RegimeSettings` gains `spread_unscored` (False) and `rotation` (None); `v2_settings` turns both on, the rotation from `[regime_v2]` (`rotation_min_hold_days = 20`, `rotation_margin = 0.25`, `rotation_max = 2`, fixed before any backtest and never fitted to one). `[regime]`'s weights apply to both books. The account is `regime-v2` with its own `[regime_v2_account] enabled` switch (live), a tick stage after `regime_ai_step`, a Ledgers column and the shared regime card; its leans carry the source `regime-v2`. The backtest and the replay study build both books from one read of the market (`regime_lines`), as lines `regime` and `regime_v2`.
**Alternatives:** A forked book class (two copies of the blend to keep in step); a variant name inside `[regime]` (the v1 lines would read a knob they never use); sweeping the three knobs on the backtest (fits the test).

## 2026-09-29 - An unscored method's weight goes to the price methods present
**Context:** The v1 blend renormalises over the methods a listing has a score from, so with value/quality, earnings drift and the AI verdict mostly missing, the constant +0.3 hold score took a quarter to a third of every blend.
**Decision:** On regime-v2 the weight of every method without a score is shared among the price methods present (trend, momentum, reversion, low volatility) in proportion to their weights; hold and any other present method keep exactly their own weight, so the effective weights still sum to 1. A listing whose present price methods all weigh 0 leans nothing (no price evidence), as a listing with no price score never did.
**Alternatives:** Dropping the hold method (changes the preset priors, not the padding); sharing over every present method except hold (value/quality would absorb the weight on the few listings that have it).

## 2026-09-29 - regime-v2 rotates in the cap hook, pairing the k-th weakest holding with the k-th best candidate
**Context:** The v1 book sells only on a sell lean or the stop, so it held what looked steady early on (median hold 120 days). Rotation needs holdings and how long each was held, which only the replay and the live step know.
**Decision:** The cap hook (`LeanCap`) now receives each holding with the day its position was first filled and each pending buy with None (the replay tracks it; live, `paper_service.held_since` reads the fills). `RegimeBook.cap` applies the sector cap, then on v2 `rotate`: candidates are the buy leans not held or pending after the cap, best blended score first; stale holdings are those held at least 20 of the listing's own trading days and not already leaning sell, weakest first; the k-th weakest is sold for the k-th best candidate while it trails by 0.25 or more, at most 2 a step. A rotated lean becomes `sell` with a `Lean.note` that `decide` uses as the reason ("rotation: held N trading days at score ..., sold to make room for X"), so the explanation names the candidate. The freed slot is bought by the usual rules at a later step, once the sale's cash is back. Scores are the book's own `explain` on each lean's day, so a rotation reads nothing after that day (tested with wild bars after the cut). v1's cap ignores the hold days, so its leans and trades are unchanged.
**Alternatives:** Every stale holding against the single best candidate (two slots freed for one buy, the second filled by whatever is next); rotating only when every slot is taken (an extra rule the ticket did not set; recorded as a follow-up); keeping hold days inside the book across calls (the live step builds a new book each day).

## 2026-09-29 - The regime-ai prompt carries no date, live and replayed
**Context:** Ticket 06: the user wants evidence for regime-ai tonight by replaying it over history, and a dated prompt lets the model recall what came next.
**Decision:** `tilt_messages` drops the "Day:" line; live and replay share the one builder, so both see only mood, flags, readings, signs and health. The backtest caption says date-blind is not hindsight-free (a model trained on those years may still recognise a market from its readings).
**Alternatives:** A replay-only prompt without the date (live and replay would measure different prompts); shifting or masking readings (changes what the rules also read).

## 2026-09-29 - The backtest's regime_ai line replays the tilt in memory, only when asked
**Context:** A model call per replayed day is hours on a local model; the plain backtest must stay fast and only read.
**Decision:** `ReplayTilt` asks the model for every decided day from the first backtest day (earlier warm-up days, and the last day, which decides nothing, get no tilt), through the live validation, evidence and direction guards; answers are kept for the run by exact prompt text (temperature 0), a failure is no tilt that day, and three days in a row with no answer at all stop the asking. Nothing is stored. `run_backtest(regime_ai=True)`, `oracle backtest --regime-ai` and a Backtest page tick box add the `regime_ai` line; its note counts calls, cache hits, failures, kept moves per method and direction, and guard resets. The job shows the replay's day as its progress, and a Cancel (or server stop) sets a flag the tilt checks through a new `on_cancel` job hook, since the replay never yields to its loop; the rest then replays on the rules weights in seconds.
**Alternatives:** Storing replayed tilts beside the live ones (a backtest only reads, and mixing them would feed replayed answers into the live account's book); a cache keyed by day (costs the same calls, guards nothing); a regime-ai line in the replay study (it reruns with the study, hours each time: a follow-up).

## 2026-09-29 - The stored tilt reason stays within MAX_REASON with its guard notes
**Context:** Gate 05 N1: the model's reason was cut to 300 characters, then the "kept at x1" notes were appended, up to about 480.
**Decision:** The model's words are cut to leave room for the notes, which are kept whole.
**Alternatives:** Cutting the whole string (would cut the notes, the part that explains the guards).

## 2026-09-30 - A regime-AI backtest gives the model way to live work
**Context:** Gate 06 #1: the hours-long replay makes a call every few seconds on the same Ollama as ticks, research, questions and discovery, which could slow them past a timeout or cost the live regime-ai tilt its one retry.
**Decision:** Before each model call (never for a cached answer) the replay waits, in 5 s steps that also read its stop flag, while a job of a kind in `activity_service.PREEMPTED_BY` (research, tick, ask, discover; moved there from the replay study, which shares it) is running, and its progress reads "waiting for <job> to finish". Once stopped it also skips the 45 s retry of a call that got no answer, so Cancel and server stop are prompt. "Retry all" leaves regime-AI backtests out; a single retry still reruns one.
**Alternatives:** Only a warning in the tick box (the live line would still pay); refusing to start near a scheduled tick (a tick can also be pressed, and research runs outside ticks).

## 2026-09-30 - Walk-forward segments are fresh starts, six months each, inside the backtest job
**Context:** Ticket 08: judge every backtest line over consecutive out-of-sample periods, so one good stretch (or the first days' picks, gate 03 #5) cannot carry a line's whole record.
**Decision:** `oracle backtest --walk-forward` (and a Walk-forward box on the Backtest card, the same job with `walk_forward=true`) splits the run into back-to-back six-month segments from its first day (each counted from that day, so month ends never drift; a last span of three months or more is kept shorter, a smaller one left out and named in the notes). Every line, buy and hold included, is replayed afresh in each segment on the same capital with nothing held. Per segment and line: return, largest fall, return over buy and hold's, Sharpe from daily equity (zero rate, 252 days), return over largest fall; over all segments: returns compounded, Sharpe of the pooled daily returns, the worst segment's fall, and how many segments beat buy and hold (a tie is not a win). The result is kept with the run (`Backtest.walk_forward`), so the GUI table and the CLI print the same figures. The regime books are built once over the whole run and shared by the segments: their states only read the past, so a segment's replay sees what the continuous book knew on its days.
**Alternatives:** Slices of the continuous run (a segment's figures would depend on what earlier segments left held, so segments would not be comparable); a separate walk-forward command and store (a second job kind and kept-results table for the same replays).

## 2026-09-30 - regime_wf refits its mood presets from the health windows, half learned, half preset
**Context:** Ticket 08 part 2: a backtest-only line that learns the per-mood weights point in time, simply and deterministically, with its parameters fixed before any run.
**Decision:** The book keeps every completed health window (method, pick day, the mood that day, the top fifth's 20-day return over the composite). With `refits` (the walk-forward's segment starts), at the first market day on or after each, before that day's own window closes, `fit_presets` reads the windows closed before it and picked in the 730 days before; in each mood the methods with 20 or more windows (the health's own minimum) share their preset mass in proportion to max(0, mean excess), the rest keep their preset, and the result is blended 50/50 with the preset. So every weight is at least half its preset and each mood sums to 1; a mood unseen, or with no method ahead of the composite, keeps its preset. The fitted table replaces the preset only; playbook, health, smoothing, sector cap and rules are regime's (v1) unchanged, so regime_wf differs from regime by the weights alone. The line exists only with a walk-forward; it is never a live account.
**Alternatives:** Refitting inside a segment (not the ticket's test); a v2-based fit (two changes at once against one comparison line); an optimiser or a sweep of the 50/50 blend, the two years or the 20 windows (fits the backtest it is judged on); giving learned weight to hold and the AI verdict (they have no pick record, so they keep their preset share).

## 2026-09-30 - Laya and industry research are tested before they get weight (grilling)
**Context:** The user asked for Laya as a test line, industry-wide research, and core-satellite. Stored news is almost all 2025-26 and there are only 9 links.
**Decision:** Backfill news from 2022-09 (SEC 8-K/10-Q + dated Google News searches), coverage labelled and thin segments left out. Laya reads headline + snippet only, live and history alike. It gives per (article, company) pair: relevant, direction, and one of 10 event types. Spill-over features (own, competitor, supplier, customer, industry) have their signs and weights fitted walk-forward on trailing data. Pass bar fixed in advance: rank IC with 5/20-day industry-excess return > 0, t > 2 overall, positive in most segments; a fail gives weight 0 but the live evidence line still runs. Core-satellite: 75% equal-weight b&h main list (yearly rebalance), 25% regime blend satellite. Industry research runs daily at the tick, plus a Company tab button. regime-ai stops stepping (frozen).
**Alternatives:** Test only on the ~13 months of stored news (2 segments, too weak); GDELT raw files (heavy); hand-set spill-over signs (a rival's news can cut either way); keep regime-ai live (evidence says it adds noise).

## 2026-09-30 - The industry map merges static competitors, peers and links, each relation dated
**Context:** Ticket 02 (laya-industry): the Company tab shows each related company, and tickets 03, 06 and 07 need "how is X related to Y, and since when" point in time.
**Decision:** `industry_service.industry_map(symbol, as_of=None)` gathers competitors (same industry; its industry group when fewer than 5 others; plus `choose_peers`), never-list names and funds left out, sorted by market-cap band with companies that have stored prices first within a band, capped at 15, and every stored link (not capped, listed first). One `Related` per company holds its `Relation`s (kind, why, source, `first_known`); a static-field relation has `first_known = None` (known on any day, as the spec allows), a link relation the first time that relation was stored and what stored it (`CompanyLink.since` and `CompanyLink.sources`, per relation, from its earliest row). `as_of` filters links through `company_links(as_of)`; `IndustryMap.known_by(day)` filters a map already built without reading the store again (for replays over many days); `relation_of(symbol, other, as_of)` answers one pair.
**Alternatives:** Ranking by numeric market cap (only bands are stored for most listings); one relation per company (a supplier that is also a competitor would lose one); capping links (they are few and the user's own).

## 2026-09-30 - Another line of a company already on the map is left out by name
**Context:** On the live DB the Company tab showed Alphabet three times (class C, two depositary shares) because the universe lists some lines as companies of their own.
**Decision:** A competitor whose normalised name equals, or starts with, a kept company's name plus a word (or the other way round) is dropped; the mapped company's own name counts. Exchange products the universe types as equities (gate 02 #1: "Leverage Shares -1x Microsoft ETC" took 3 of MSFT's 15 slots) are dropped by name through `listings.is_exchange_product` (whole words ETC, ETP, ETN, ETRACS, "Leverage Shares", or a leverage like 2x). Lines the universe names differently (Samsung GDRs) still show; the Universe tab's merges are the real fix.
**Alternatives:** Fuzzy name matching (drops real rivals with similar names); leaving them (15 slots spent on three Alphabets).

## 2026-09-30 - What a related company is doing now is read from stored data only
**Context:** Ticket 02: opening the panel must never fetch.
**Decision:** `company_activity` gives the last stored close, the 5 and 20 trading-day moves (adjusted closes as a pair, else closes; None with too few bars or a split-sized jump), the last and next earnings date from the stored SEC dates (next is rarely known: SEC gives past releases only), and the three newest headlines research on the company found by its own searches (not a linked company's search) plus its stored filings, dated ones first, Market Oracle's own stored-data summaries left out. Missing data shows as "n/a", "not known", "no stored prices", "no stored headlines". Laya's scored events (ticket 01) are not read yet.
**Alternatives:** Fetching prices or earnings on open (breaks the no-fetch rule); every article whose title names the company (slow full-text search, many false matches).

## 2026-09-30 - The user adds links from the Industry panel, and re-adding lifts that link's delete
**Context:** Ticket 02: add and delete a link from the GUI and the CLI, reusing links_service.
**Decision:** `links_service.add_link(symbol, other, relation)` (supplier, customer, competitor or partner; source `user`; first seen now) behind `POST /api/companies/{symbol}/links`, the panel's Add link form and `oracle links add`. Adding a link the user deleted brings that one row back as theirs, first seen at the re-add; adding one already found keeps who found it and when; a delete of any other relation between the two still mutes discovery for the pair. The Industry panel replaces the Linked companies section (its rows keep Delete); `oracle industry SYMBOL` prints the same map.
**Alternatives:** Refusing a re-add of a deleted link (the user cannot undo a wrong delete); clearing every delete of the pair (discovery would bring back the relation they deleted).

## 2026-09-30 - Laya scores live behind one service, per article, company and model tag
**Context:** Ticket 01 (laya-screen): tickets 03, 06 and 07 build on one Laya contract.
**Decision:** `oracle/services/laya_service.py` owns loading, device, batching (16), yielding and the tag. Scores go in `laya_scores` (migration 45), one row per (article, company's primary listing symbol, model tag), with relation (own, competitor, supplier, customer, industry), relevant, direction and its probabilities, event and its probability, scored_at; a stored pair is never scored again, a new checkpoint scores under its own tag. The tag is the checkpoint plus laya's reviewed commit (`laya-multilingual@e4e9ddf`), and the download is pinned to that commit. A company's articles are what research on any of its listings was shown, plus its SEC filings, never the stored-data summaries Market Oracle writes, nor what research saw only through a linked company's search (`research_queries.via`; gate 01 #1): that news belongs to the linked company, and scoring it as `own` would block its later scoring under the right relation. Contract for later tickets: `score_articles(ctx, symbol, article_ids, relation=..., scorer=..., stop=..., busy=...)`, `score_company`, `laya_scores(ctx, symbol, model=MODEL_TAG, history=False, known_by=None)` returning `ScoredArticle` with `known_at` (published_at, else fetched_at) and `dated`; `history=True` drops undated articles. The model loads only when something needs scoring and is closed after the run, and also while it waits for research, a tick, a question or discovery (`PREEMPTED_BY`), so the GPU is Ollama's meanwhile.
**Alternatives:** Keeping the model loaded for the server's life (holds about 2.5 GB beside Ollama on an 8 GB card); scores keyed by the listing asked for (AAPL and AAPL.MI would score the same news twice).

## 2026-09-30 - Search snippets are now stored, and Laya reads headline plus snippet
**Context:** Spec decision 2 says headline + snippet, but snippets were shown to research and never stored. Google News RSS (almost all stored news) carries none; only web-search results do, and those are undated.
**Decision:** `articles.snippet` (migration 45) keeps the first snippet seen; Laya reads "Company / Headline / Snippet" text, the snippet cut at 600 characters. In practice Laya reads headlines only today; a backfill source with snippets (ticket 04) gets them read without more work.
**Alternatives:** Reading the fetched full text (the spec keeps that with the research loop); fetching RSS descriptions (Google News descriptions repeat the headline).

## 2026-09-30 - Laya's device: auto takes the GPU only with 3 GB free as nvidia-smi reports it
**Context:** The RTX 5060 (8 GB) is shared with Ollama. Measured today: loading Laya took about 2.5 GB and scoring 110 headlines 3.2 GB; Ollama's 4B research model holds 5.1 GB. On Windows torch's own free-memory figure left out Ollama's share (it read 7.0 GB free with Ollama loaded).
**Decision:** `[laya] device` is auto (default), gpu or cpu, in Settings and live. Auto reads free memory from nvidia-smi and loads on the GPU only with 3000 MB free, else the CPU. Timings (load then 110 headlines): GPU 7 s + 2.1 s (19 ms a headline); CPU 7 s + 45 s (410 ms); with Ollama's model loaded auto picked the CPU, and forcing gpu still ran (Windows spills to shared memory) with Ollama answering after. On the live DB copy Toyota's 612 articles scored in 17 s on the GPU.
**Alternatives:** Always the GPU (can push Ollama's model or Laya into shared memory mid-tick); torch's free-memory figure (wrong on Windows).

## 2026-09-30 - Laya's questions name the company; sanity-set accuracy (reported, not gated)
**Context:** 110 real headlines from the live DB (sampled across companies, at most 3 each), hand-labelled by the builder (76 relevant), kept as `tests/fixtures/laya/sanity.json`. Five wordings were tried on it.
**Decision:** The questions name the company ("Is this headline about Toyota Motor Corporation (7203.T)...", good or bad news for its share price, event type from the fixed ten), over a plain-text state. Its scores on the set, each beside its baseline (optimistic, as the wording was tuned on the same 110 rows): relevance 38% right at 0.5 where always "relevant" gets 69%, rank AUC 0.57 (near chance; probabilities run low, a company's own news has median 0.12 on the live copy); direction 66% on relevant headlines where always "none" gets 55%; event type 51% where always "other" gets 37%. The fixed wording scored relevance AUC 0.47, direction 65%, event 41%. Relevance is not usable yet: the Company tab dims the column and heads it "unreliable", the help text gives every number with its baseline, and ticket 06 must not filter on relevance >= 0.5 (it would drop about 90% of a company's own news). Probabilities show an "uncalibrated" badge; `pytest -m laya` reruns the measurement.
**Alternatives:** The fixed company-free wording (worse on every measure); tuning further on the set (would overfit its 110 rows).

## 2026-09-30 - laya is an optional extra and its live tests opt in
**Context:** laya pulls torch (2.7 GB for the CUDA build), and the RTX 50 series needs a cu128 torch that PyPI's default Windows wheel is not.
**Decision:** `pip install -e .[laya]` after installing torch from the PyTorch cu128 index; without it (or with a torch whose DLLs will not load), scoring refuses with "laya or torch will not load" and the rest of the app runs. Tests never load the model (a conftest guard refuses it); `-m laya` runs the real one on the sanity set.
**Alternatives:** A core dependency (every install pays for torch).

## 2026-09-30 - Supplier and customer ties: candidates by exact universe names, typed by rule and Laya, linked only where both agree
**Context:** Ticket 03: suppliers and customers from 10-K text and stored news, typed by Laya (supplier, customer, competitor, partner, none), with the text as evidence; confident ones become links, the rest wait for the user. Ticket 01 found Laya's relevance near chance, so its typing was measured before trusting it.
**Decision:** Candidates: a stored headline of a company (what its research was shown, as Laya's scores use) that names another universe company beside it, and each sentence of a US company's 10-Ks filed since 2022-01-01 (up to 5, read once each, through the SEC gate) naming another. Names are matched as discovery's exact names (`blind_name`, plus a leading "the" and a trailing "company/group/holdings" aside), plus a big company's (mega or large cap) first word where it is clearly the biggest company starting with it ("Toyota"); only mega, large and mid caps are named; everyday words never count as a one-word name: a fixed list (places, nationalities, generic business words) plus any word written in lower case in 50 or more company descriptions in the universe ("investor", "strategy", "iron"). Each candidate is typed by a keyword rule (buy/supply verbs with word order, supplier/customer nouns, "accounted for", compete/rival, partner/joint venture) and by Laya (one `choice` question naming both companies). On 100 hand-labelled candidates (`tests/fixtures/laya/edges.json`, 44 real ties): always "none" 56% right; the rule 65% right, 50 ties named at 52% precision (optimistic, its patterns were written after seeing candidates from the same documents); Laya alone 50% right (below always "none"), 54 named at 35% precision, and no probability cut helps (0.7: 27 named at 52%); where the rule and Laya agree, 27 named at 63% precision, 39% recall. So a tie becomes a link only where both agree (source `filing` for a 10-K, `laya` for a headline); a tie only the rule names is kept unconfirmed in `link_candidates` (migration m0047, numbered after 04's m0046) and shown on the Industry panel with Confirm and Delete; one only Laya names is stored as none (a first live-copy run kept those unconfirmed too: 781 of 1,076 unconfirmed ties, mostly "customer" on analyst notes and holders, at Laya's 35% measured precision, burying the rule's 295).
**Alternatives:** Laya alone with a probability cut (worse than no model at all on the set); the rule alone (more ties, half wrong); a discovery-style model naming the companies (a model call per headline; the exact-name index reads the main list's 1.2k headlines in seconds); every universe listing in the index (small caps' names are mostly everyday words and people's names: thousands of false candidates on the live copy).

## 2026-09-30 - A tie's link is first seen on its document's date
**Context:** Point in time: history tests must not see a link before the document that shows it existed; `LinkRepo.record` stamped links with now.
**Decision:** `LinkFound.seen` carries the document's date (an article's `published_at`, else when it was fetched; a 10-K's filing day at 00:00 UTC, as SEC filings are dated elsewhere). `record` keeps the earliest first_seen and the latest last_seen, and a link seen in an older document takes that document's source (except a user's link, which stays theirs), so a link discovery found today but a 2023 10-K already showed is first known in 2023 as `filing`. A user-confirmed tie is dated by its document too (the document existed then; the user's judgement is later, noted for ticket 06's sensitivity run).
**Alternatives:** Dating by when the tie was read (every backfilled link would be "known" only from today, useless for history tests); dating a confirmed tie by the confirmation (hides a tie that was knowable).

## 2026-09-30 - Ties after gate 03 round 1: lists never auto-link, competitor wording first, one row per tie; the honest numbers
**Context:** Gate 03 r1 found 10-K competitor lists linked as suppliers (the bullets under "Our current competitors include:" read "suppliers of ... such as Broadcom, Qualcomm", and rule and Laya agreed on the same mistake, so "both agree" could not catch it: 10 of NVDA's 16 supplier links were competitors), "X vs Y: which stock" headlines linked as competitors, one table row per document rather than per tie, and quality numbers without their population.
**Decision:** A bullet is read with the heading that introduced its list; "Inc." and similar no longer end a sentence; the rule reads competitor wording ("competitors include/are/such as", "our competitors", "compete with/against") before supply words; vs/versus is no competitor keyword; a sentence naming 4 or more universe companies never links by itself (unconfirmed at most); a filing's cover page, signatures, exhibit list, performance graph and index mentions are skipped; preferred shares are never named. On the live copy NVDA's supplier links went from 16 to 7, all real (TSMC, Samsung, Advantest, Chroma, Amkor, Fabrinet, Jabil); Broadcom, Qualcomm, Arista, Marvell and the other competitors now wait unconfirmed. The panel shows one row per (other company, relation) with its earliest document and how many more show it; linked when any document linked it; Delete drops every row of the tie and is remembered, so later documents never link it; a tie whose link the user deleted on the map is not shown. Numbers on the 100-row set (in-sample for the rule, whose fixes were written looking at it): always "none" 56% right; rule 67% right, 45 named, 58% precision, 59% recall; Laya 50%, 54 named, 35%, 43%; shipped (agree, no list) 63% right, 19 named, 13 right (68%), recall 30%. The set was drawn half from rule-named candidates; live only 24% are (637 of 2,662 on the live copy after these fixes), so weighted to the live mix always "none" is right 67%, the rule's recall is about 38% and the shipped recall about 19%. Only 7 of the 44 labelled ties are supplier or customer (25 are competitors): shipped named 5 supplier/customer ties and 3 were right; the rule named 12, 6 right. So nothing here supports a supplier/customer precision claim, and ticket 06 must use `filing`/`laya` supplier and customer links in a sensitivity run only, until a supplier/customer sample is hand-checked.
**Alternatives:** Keeping lists linkable when both agree (the correlated error the gate found); dropping 10-K lists entirely (CAT's and AMT's competitor lists are right and the user can confirm them).

## 2026-09-30 - Core-satellite: two sleeves, one line, rebalance in two steps
**Context:** Ticket laya-industry 05 (spec decisions 7, 9): 75% equal-weight buy and hold of the main list rebalanced yearly, 25% the regime blend, costs charged like every line, the satellite never spending core cash, fixed a priori.
**Decision:** `broker.core_sat`: the core and the satellite are replayed apart on 75% and 25% of the capital and summed day by day, so neither can touch the other's cash. The core buys its members in equal amounts at the next open with the same costs as every line, holds no cash floor and no stop (it is buy and hold), and is set back to equal weight on the first market day on or after each yearly anniversary of its first day: that day sells trim overweights by whole shares and drop listings no longer in it; once they have filled, buys top the underweights up from the core's own cash, scaled down alike when short (the first day, with nothing to trim, buys at once). A member without a close in the last 7 days sits out that rebalance's targets. Core members are the main list's companies: never a fund (the universe type) or a never-listed company (Tesla). The satellite is the regime (v1) book in its own name, leaning only on those members, traded by the strategy's rules with its risk sizes, position cap and cash floor as shares of the satellite alone. Live, one account `core-sat` holds both sleeves: each order carries its sleeve's source (`core-sat:core`, `core-sat:satellite`) and each sleeve's cash is its starting share plus its own fills, so a satellite sell sells only its own shares even when the core holds the same company. No migration (accounts are free text; the sleeve rides on `paper_orders.source`).
**Alternatives:** Two separate accounts summed for display (every list of accounts would show three); a cash floor or stop on the core (not buy and hold); trimming and topping up in one step (the buys would spend cash the trims had not yet raised, borrowing from the satellite live); a drift band before rebalancing (a parameter the spec does not fix); v2 or regime_wf as the satellite (spec says the regime blend).

## 2026-09-30 - Extra satellite methods join through the regime book's inputs
**Context:** Ticket 08 will add Laya to the satellite only if it passes the pre-registered bar.
**Decision:** `RegimeInputs.extra` maps a method name (not one of METHODS) to dated scores per symbol, each usable for `extra_max_age` days from its day, read point in time like `ai_verdict`. The book's method list is METHODS plus the extras; an extra weighs by its name's weight in each mood of `RegimeSettings.weights` (0 when absent, so it scores but adds nothing) and keeps its own health record; no playbook signs. An extra named like a built-in method is ignored. `regime_lines(..., extra=)` feeds the core_sat book only; live, `core_sat_service.satellite_inputs` and `satellite_settings` are the seam. `fit_presets` (regime_wf) still fits METHODS only.
**Alternatives:** A Laya-specific method in `methods.py` (a special case the ticket rules out); adding extras to every regime line (would change the lines Laya is compared with).

## 2026-09-30 - regime-ai frozen: no step, no equity, card and history kept
**Context:** Spec decision 9: regime-ai stops stepping; its evidence showed the model tilt as noise.
**Decision:** `paper_service.FROZEN = {regime-ai}`: `account_on` is false whatever `[regime_ai_account] enabled` says, so its tick stage and button decide nothing and ask no model ("regime-ai account frozen: nothing decided"), and the fill pass records no more equity, so its record ends the day it froze (orders placed before still fill). Its card, decisions, readings and tilts stay; the card says frozen and shows no switch or model pick. The code it stepped with stays (tests thaw it), as does the opt-in regime_ai backtest line.
**Alternatives:** Keep marking its holdings to market daily (a frozen book's buy and hold is a different line from the one measured); deleting the account (loses the history the spec keeps); only turning its switch off (the user or Settings could turn it back on).

## 2026-09-30 - Core-sat gate 05 r1 fixes: one satellite universe, one stale rule, expiring core orders
**Context:** Gate 05 round 1: the live satellite leaned on discovered companies the backtest never traded; live and replay treated a stale holding differently on a rebalance; the replay placed top-ups under one share; an order that never filled blocked every later rebalance.
**Decision:** The live satellite leans only on the main list's companies (plus what it holds), a discovered one is logged as a skip, as the backtest's member filter does. A listing without a close in the last 7 days is left out of a rebalance entirely, live and replayed: not counted, trimmed, sold or bought. Both drop a buy worth under one share at the last close. A core order still waiting 7 days after it was placed is dropped (live: rejected with the note "expired, no fill within 7 days") and counts as never placed, so it never blocks a rebalance and an expired first buy is placed again. The 75/25 split and the yearly equal-weight rebalance are unchanged (pre-registered); the walk-forward note now says its six-month segments never reach a rebalance.
**Alternatives:** Letting the satellite trade discovered companies and widening the replay to match (the replay study has no discovered list); selling a stale holding as "no longer in the core" (it may only be a fetch gap); cancelling as a new order status (a migration for a rare case).

## 2026-09-30 - History backfill: quarter searches, month re-asks when Google's cap fills
**Context:** Ticket 04 (laya-industry): news from 2022-09 for the main list and its industry maps. Measured live: Google News RSS honours `after:`/`before:` for old windows but returns at most 100 items a search, and busy names fill that in any quarter (Apple Q4 2022: 100 items, 94 of them in Nov-Dec); months fill it too for the biggest names (MSFT, ORCL: 100 in every month of 2023 H1).
**Decision:** One search per company per calendar quarter (the first cut at 2022-09-01, the last at the first of the current month, so every window is over); a main-list company's quarter that returns 100 results is searched again month by month, a map company's is not (coordinator, gate 04 #2: the maps are context; about 8,000 requests instead of 15,000). A first window that is one month already is never asked again as its own month. The query is the company's search name (see the next entry) with the window's `after:`/`before:`. Results without a date are dropped (never dated by fetch day) and dates outside [start, end) UTC are dropped (Google's edges drift a few hours by outlet timezone; 1-7 per 100 at month edges). English edition only.
**Alternatives:** Monthly searches for everyone (3x the requests for the half of companies that never fill a quarter); weekly re-asks of full months (4x more for the biggest names; left for later if 06 finds months thin); GDELT (its DOC API reaches only about three months back).

## 2026-09-30 - History backfill: what is fetched, where it is kept, how it resumes
**Context:** Ticket 04: resumable, deduplicated, one Activity job that yields to live work; 06 needs articles per company and bars for industry-excess returns.
**Decision:** Migration `m0046_history_backfill` (numbered after 01's m0045; whichever merges second may need renumbering) adds `history_windows` (per company, source and window: found, kept, undated, outside, done_at) and `history_articles` (company, article), so one article found for two companies is stored once and linked to both. A window's row and its links are written in one transaction; a restart skips finished windows, and a window that failed is not recorded, so it is asked again. Stages run prices, then SEC filings (8-K, 10-Q, 10-K and amendments filed since the start, US names only, reading older submissions pages when the recent list does not reach back; stored like the research tool's filings, source `sec_edgar`, query = ticker), then news; main-list companies first. Price bars from 2021-09-01 are fetched once per company whose first stored bar is later than 2021-10-01 (the main list's start at 2021-09-21 counts as covered). The job (`submit_history`, kind `history`, retryable) waits in 5 s steps while research, a tick, a question, discovery or a price update runs; an open breaker is waited out once, then the run stops (a restart resumes); five failed quarter searches in a row stop the news stage. A single-flight lock keeps the CLI and the app from running two at once. History is keyed by primary listing.
**Alternatives:** Marking articles through `articles.query` only (one URL keeps its first company); refetching partial windows every run (hundreds of wasted requests per restart).

## 2026-09-30 - History backfill: each company's news is searched by a headline name, and noise is measured
**Context:** Gate 04 #1: on a real sample MAN SE's quarter was 100% noise (Man Utd, Isle of Man), and main-list names like "Petroleo Brasileiro" or "LVMH Moet Hennessy" are not what headlines say.
**Decision:** A company's search name is the user's own (stored in `history_names`; `oracle history name`, the card's Search name form), else one set by hand in `history_names.SEEDS` (all 51 main-list companies and 17 map companies: "Petrobras", "LVMH", "BMW", "IBM", "Aramco", `"MAN Truck" OR "MAN SE" OR "MAN Energy Solutions" OR "Traton"`, `"SAP" (software OR shares OR stock OR cloud)`), else the universe name cleaned (share class, ADR words, legal forms, trailing "&"/"and"/"Company"/"(The)"/"p.l.c.", words after a dash). A default name of one word that is 4 letters or less or an everyday word (a short list: Oracle, Target, Orange...) gets `(shares OR stock OR earnings)`; a listing code for a name ("SHINKO EL.IND.LTD") is not searched. Setting or clearing a name forgets that company's news windows and links (the articles stay), so the next run asks again. Each news window stores how many kept headlines name the company by an alias (a short all-capitals alias only in capitals); coverage shows the share per quarter and calls a quarter with 5 or more articles and under 50% naming it noisy, for 06. Measured on 2023-Q1: MAN.DE went from 100 results, 2 naming it, to 28, 22 naming it; 10 main-list names 35% (CSL, press pages about products) to 90%.
**Alternatives:** Context words on every one-word name (would starve unique names like Pfizer or Salesforce); Laya's relevance as the filter (near chance, gate 01 #2); skipping ambiguous names outright (loses SAP, Shell, DBS).

## 2026-09-30 - History backfill: one line per company, and it steps aside for scheduled ticks
**Context:** Gate 04 #4 and #5: maps carry GDRs, BDRs, warrants, preferreds and second lines of one company; a 6-hour run at Google News pace could leave the 22:00 tick without Google News.
**Decision:** The plan takes each company once by primary listing and leaves out receipts (`is_receipt`, `is_receipt_symbol`), exchange products, warrants and preferred shares (by symbol suffix and name), listing codes, and a map line whose default search name equals one already taken. The job waits while research, a tick, a question, discovery or a price update runs, from 30 minutes before a scheduled tick or midday pass until it has run (or 15 minutes past its time), read on the job's own connection. The plan is cached for 10 minutes and the card for 60 seconds (15 s of industry maps, 6 s of joins after a full run). A 02 follow-up: dedupe maps by company there.
**Alternatives:** Asking the coordinator to start the run only after the 22:00 tick (a run longer than the gap to the midday pass would still overlap); reading the scheduler's state across threads (its connection belongs to the event loop).

## 2026-09-30 - History backfill: a run that stops early or fails fails its job
**Context:** Gate 04 #3: four things ended a run early yet the job showed done, with nothing in Activity or on the card.
**Decision:** The run's result is kept (meta `history_last_run`) and shown on the card and by `oracle history coverage`. From the app, a run that stopped for any reason but Cancel, or had failures (a missing SEC contact, a search that failed), fails its job with a one-line summary (why it stopped, counts, the first failure), so Activity shows it and Retry resumes. The CLI exits 1 in the same case. A Yahoo lockout is waited out once like the other sources, then the run stops.
**Alternatives:** Done with a warning (Activity shows no detail for done jobs, and Retry needs failed).

## 2026-09-30 - Daily industry research: what is searched, scored and when
**Context:** Ticket 07 (laya-industry, spec decision 8): daily at the tick for held, watched and main-list companies and their industry maps, headlines only, plus a Research industry button.
**Decision:** The daily set is held companies, then the main list, the user's other lists, then the discovered list, each company once (primary listing), never a fund or a never-listed company (Tesla). For each target, it and every company on its industry map (the history backfill's filters: no receipts, exchange products, warrants, preferreds or listing codes) is searched once a run by its history search name with `after:` three days back; a company searched in the last 12 hours (1 hour from the button) is not searched again. Undated results and ones older than the window are dropped, never dated by fetch day. The found headlines are kept per company (`industry_news`, m0048). Laya then scores, for the target, its own recent news (the searches' and what its research read) as own, and each map company's recent news under the map's relation, only pairs not yet scored by the model; own goes first, so an article found for both counts as own. Features for the window's days are recomputed and stored (`laya_daily`).
**Alternatives:** Reading full text (spec 2: headlines only); a search per (target, map company) pair (the same headlines asked again for every target sharing a competitor).

## 2026-09-30 - A map relation names the feature its news counts in
**Context:** Spec 4 names five relations per company; industry maps have six kinds and a company can be both a linked supplier and a same-industry competitor.
**Decision:** News about a company counts for the target as what that company is to the target: supplier, customer, competitor (competitor or peer), industry (partner or other). A named tie wins over a competitor, a competitor over a looser tie. `laya_scores.relation` now reads "what the article's subject is to the target" (its comment said the reverse; nothing stored a supplier or customer relation before, so no data changes). Every map link counts live, 03's Laya/filing-typed ones included; 06 can recount through the aggregator's `relation_for` (sensitivity run).
**Alternatives:** The target's relation to the subject (supplier and customer swapped: "news about my suppliers" reads more naturally as the supplier feature); leaving Laya-typed links out live (the live paper line would then differ from the sensitivity run 06 may pass).

## 2026-09-30 - One daily feature aggregator for live and history
**Context:** Ticket 07 and 06 need the same features; whichever landed first builds it.
**Decision:** `laya_features.daily_features(ctx, symbol, start, end, *, model, history, relation_for)` reads the company's stored scores and returns, per UTC day (the day the article was known: published_at, else fetched_at) and relation, the article count, the sum of direction x its probability (up +p, down -p, none 0, p the chosen direction's probability) and counts per event type. `history=True` leaves undated articles out. Relevance filters nothing (near chance). A day's features are usable from the next session (`usable_on(rows, session)` keeps days before it). `store_features` stores a range (replacing it, so a day that lost its articles is dropped); the tick stores the last three days plus today each run, so a late-published article restates a past day's stored row (a live decision used what was stored then).
**Alternatives:** p(up) - p(down) (smoother, but the ticket names direction x prob; 06 can add it from the stored probabilities); storing features incrementally (a rescore or a late article would leave stale rows).

## 2026-09-30 - Industry research budget, yielding and failure
**Context:** A tick stage over roughly 50 main-list companies and some 300 map companies at Google News' 0.5 requests a second must never hold up or fail the tick.
**Decision:** The stage runs last in the tick (after the AI step), so it never delays a trade. Budgets are Settings keys: `[laya] industry_requests = 400` searches and `industry_minutes = 30` a run, searching and scoring together (searches at 2 s each fill about 13 minutes of it); `industry_daily = true` turns the stage on. Out of searches, it stops searching and still scores what is stored; out of time, it stops between searches or batches, keeping what it finished. A run searches everything first (which also gives an Ollama model left loaded by the research stage time to leave the GPU before Laya picks its device), then scores every target's own news, then the maps' news, storing each target's features; a budget that runs out still leaves every target its own features. It waits in 5 s steps while research, a question or discovery holds its lock (any process), closing its model meanwhile: `laya_service.SharedScorer` is one model across targets that `score_articles` now closes while waiting and reloads after (gate 01 #9). A failure is the stage's own ("failed" or "partial" in the tick record); a Laya that will not load stops the run with why. A Research industry job and the stage never run at once (lock `industry_research`); the stage then says so and skips.
**Alternatives:** Searching only the main list's own names (no spill-over, the point of the stage); an unbounded run (at 0.5 requests a second the full set is about 12 minutes of searches alone).

## 2026-09-30 - Industry research gate 07 r1: rotation, device, event scores, stage errors
**Context:** Gate 07 round 1: a short request budget searched the same head of the daily set each day; a run that loaded Laya on the CPU stayed there and scored the same maps last each day; 06 needs the signed feature per event type; a failed search marked the tick partial.
**Decision:** A run searches every target's own news first, then the map companies ordered by when each was last searched (never searched first), so a short budget rotates through them. Map news is scored starting with the target whose map was scored longest ago (meta `industry_map_scored`). Between targets, a shared model that `auto` put on the CPU is closed when the GPU has room again (nvidia-smi free memory at least the load threshold), so the next batch loads there; a run that scored on the CPU says so as a warning in its summary. `DailyFeature` gains `event_scores` (event type -> the same signed sum as `score`, over that type's articles; same keys as `events`, whose counts are unchanged), stored in `laya_daily.event_scores` (m0048, never deployed, so edited in place). The stage's error is only a source that stopped answering (breaker, five failures in a row) or a Laya that will not load; single failed searches, the request budget and the time budget stay in the summary. The lock's lease is the time budget plus an hour and is refreshed before each batch. The Industry panel gains "Research the daily set".
**Alternatives:** Randomising the order (not reproducible); a per-run cap of pairs per company (would make live counts differ from 06's history features); a parallel `event_scores` table (one row per day and relation already holds it).

## 2026-09-30 - Laya history scoring: which (article, target) pairs
**Context:** Ticket 06 (laya-industry, spec decisions 4, 6 and Point in time): score the backfilled history per (article, target) pair, as one long job.
**Decision:** Targets are the main list's companies (`history_companies`, main). For each, its own backfill ids (`company_history`, never the live research articles) are scored as `own`; then every history company whose map it is on (`of`) has its backfill ids scored for it, stored under today's map relation (`relation_for_kinds`). The stored relation is only a label: the test recounts every pair point in time. Own goes first for every target, so a pair in both counts as own. The job (Activity kind `laya_history`) shares one model (`SharedScorer`), waits while research, a tick, a question or discovery runs (closing the model), stops on Cancel between batches, and resumes by skipping scored pairs. It measures pairs and seconds and estimates what is left.
**Alternatives:** Scoring only own news (no spill-over, spec 4); scoring through 07's `industry_pairs` (mixes live and history subjects).

## 2026-09-30 - Laya predictive test: pre-registered before any real-data run
**Context:** Spec decision 6 fixes the bar; ticket 06 and the coordinator comments (never filter on relevance, relevance its own feature, 03's links only in a sensitivity run, SEC filings act next session) fix the rest. Every choice below was written before the test read any real data and is not to be changed after seeing results.
**Decision:**
- Articles: only backfill ids of the target and of its map companies, dated only (`published_at`; undated never dated by fetch day). An article counts on the UTC day it was known (SEC filings are dated 00:00 UTC of the filing day), and a feature day d is used only by decision days after d (the next session, `usable_on`).
- Relation per (article, target), main run: own when in the target's backfill; else from the map companies whose backfill holds it: a static competitor (industry, industry group, peers) is `competitor`; a user, discovery or chat link first known by the end of the article's day counts as competitor (competitor, peer) or `industry` (partner, other); supplier and customer links and every 03 link (`filing`, `laya`) are left out. Precedence own, competitor, industry.
- Sensitivity run (named "links as known today"): every relation as the map knows it today, all sources, no dates, supplier > customer > competitor > industry.
- Features of company i on decision day t, over feature days [t - L, t - 1], L = 7 calendar days for the 5-day horizon and 28 for the 20-day: for each relation r, `r_sum` = sum of direction x its probability (the shared aggregator's score) and `r_mean` = r_sum / articles (0 with none); `own_relevance` = mean relevance of its own articles (0 with none). Main: own, competitor, industry; sensitivity adds supplier and customer. The combined score (the verdict) uses the per-article means and own_relevance only (main 4 features, sensitivity 6); the sums are volume-driven and are reported as secondary per-feature ICs, never in the combined score or the verdict (amended from both before any real-data run, on the gate-07 note). Relevance filters nothing. Event types are not features (too many for a trailing fit on ~50 names).
- Target: company i's return from its open on t to its open on its h-th bar after t (h = 5, 20), local currency, dropped with a missing open or a move outside the split guard (0.5 to 2) anywhere in its closes and opens across the window; minus its industry: the equal-weight mean of the same-window return (open of the first bar on or after t to the open of the first bar on or after i's exit day, each within 5 calendar days, same guard) of its map's static same-industry competitors (industry or industry-group source) with one, when 3 or more have one; else the composite: the equal-weight mean of the other main-list companies' same-window returns (3 or more, else dropped).
- Decision days: every day some main-list company has a bar; i is in day t's cross-section only with its own bar on t.
- IC: Spearman rank (average ranks for ties) across companies per day, with 5 or more companies and neither side constant, else no IC that day. Per segment and overall: the mean daily IC, a Newey-West (Bartlett) t-stat with h lags (5 and 20, for the overlapping windows), IC days and segments positive.
- Segments: `walk_forward.segments(2022-09-21, end, 6 months)`.
- Combined score per horizon and segment: fitted at the segment's start S on training days in [S - 730 days, S) whose every observation's exit day is before S (purged): each feature's mean daily IC (20 training IC days needed, else weight 0); weight = mean IC / sum of |mean IC| (sign fitted, so a feature that predicts backwards counts backwards); score = sum of weight x the feature's cross-sectional percentile rank. All weights 0: no combined score that segment ("no trailing fit"). Never refitted inside a segment.
- Counted segments: fitted, 20 or more IC days, and at least half of the main list's company quarters overlapping it neither thin nor noisy (history Coverage). Others are named with why and left out of the verdict.
- Verdict per horizon: PASS only if the combined score's mean IC over the counted days > 0, its t > 2, and it is positive in more than half of the counted segments, with 2 or more counted segments (fewer: INCONCLUSIVE). Laya PASSES only if both horizons pass; otherwise weight 0 and it stays a live paper line for evidence.
- Every result prints the survivorship caveat (today's list and maps taken back in time).
**Alternatives:** Close-to-close from the decision day (reads news published after that day's close into its return); a relevance gate (drops ~90% of own news); non-overlapping samples for t (drops 4 of 5 or 19 of 20 days); an expanding fit window (the regime_wf fit uses 730 days); passing on either horizon (two tries at the bar).

## 2026-09-30 - Laya predictive test: where it runs and what is kept
**Context:** Ticket 06 asks for the table on the Backtest page and `oracle laya test`, with GUI parity.
**Decision:** The Ledgers tab gets a "Laya predictive test" card under Backtest: Score history with Laya (job `laya_history`), Count pairs and estimate (`GET /api/laya/plan`), Run the test and Run sensitivity (job `laya_test`), and the newest kept main and sensitivity tables (verdict per horizon, every segment with its IC, t, days, counted or why not, and the fitted weights; every feature's own IC, secondaries dimmed; the caveat). Results are kept in `laya_tests` (m0049, 20 per run, JSON). CLI: `oracle laya history [--plan] [--own-only] [--only ...]` and `oracle laya test [--sensitivity] [--show]`; `--own-only` and `--only` are measuring runs for a DB copy, exempt from the page. The aggregator's `DailyFeature` gains `relevance` (the day's sum, not stored, not compared), so relevance is read through the same aggregator.
**Alternatives:** A section inside the backtest result (a test is not a backtest run and has its own jobs); storing each day's IC (the table is what is judged; a rerun rebuilds the days in seconds).

## 2026-09-30 - Laya predictive test: pre-registration amended (gate 06 round 1), before any full run
**Context:** Gate 06 r1 found a look-ahead for listings that open before 07:00 UTC (Google News backfill stamps are date-only, midnight US Pacific, so the real time can be up to 06:59 UTC the next day), a combined score that could pass on news attention alone (`own_relevance` is mostly "had news"), a history job that could fall to the CPU for days, and a CLI run that never yields. No full real-data run has happened (only the builder's own-pairs sample on a partial copy, every segment thin); this amendment is committed before the code that implements it.
**Decision:**
- Known time: a date-only stamp is known 24 hours after it. Date-only means exactly midnight US Pacific (07:00 or 08:00 UTC, seconds 0, midnight in America/Los_Angeles) or exactly midnight UTC (SEC filing days). Other stamps are known when stamped.
- Usable per listing: an article is usable for listing i's decision day t only when known strictly before i's open on t, in UTC, from its exchange's time zone and local open (by symbol suffix: US 09:30 New York, .TO 09:30 Toronto, .SA 10:00 Sao Paulo, .L 08:00 London, .DE/.F/.PA/.AS/.MC/.MI/.BR/.SW/.CO/.OL/.ST/.HE/.VI/.IR 09:00 in their own zone, .T 09:00 Tokyo, .AX 10:00 Sydney, .KS/.KQ 09:00 Seoul, .SI 09:00 Singapore, .HK 09:30 Hong Kong, .NS/.BO 09:15 Kolkata, .SS/.SZ 09:30 Shanghai, .TW 09:00 Taipei, .JO 09:00 Johannesburg; any other suffix is taken as 10:00 Auckland, the earliest open). Each article gets its first usable decision day for the target (the aggregator groups by it through a new `day_for` hook); decision t sums the L usable days t-L+1..t (L = 7 and 28 as before). A link counts for an article when first known by the end of the UTC day of its known time.
- `own_relevance` leaves the combined score (kept as a secondary figure). The combined score is the per-article direction means only: own, competitor, industry (main); plus supplier, customer (sensitivity).
- Attention baseline, pre-registered: `own_attention` = log(1 + own articles in the window), reported beside Laya's features per horizon with its IC and t over the same counted days. Laya only counts as useful if the combined score's IC beats the baseline's IC (both reported, `beats_baseline`). The pass bar itself stays spec decision 6 on the combined score.
- Purge: a training observation needs its exit day plus the peers' slack (5 calendar days) before the segment's start.
- History job: never silently on the CPU. With `device = auto` and a model that loads on the CPU, it closes it and waits for GPU room (re-trying every 60 s, saying so, stopping on Cancel) unless `device = cpu` is set or CPU is allowed explicitly (`--allow-cpu`, the card's checkbox). Measured ms a pair counts model time only (no waits or loads).
- History scoring takes a `laya_history` lock from the job and the CLI alike, and gives way to live model work in any process: research, discovery chat, discovery, a tick and industry research locks, plus in-app research, tick, ask, discover and industry jobs (`industry` joins `PREEMPTED_BY`).
- Deviations from the ticket, stated for the verdict: event types are not features (too many for a trailing fit on ~50 names); features are direction x its probability (the chosen direction's), not direction x relevance, with relevance a separate secondary figure (coordinator note: relevance near chance).
**Alternatives:** Shifting every listing that opens before 07:00 UTC by one extra day (simpler, but still leaks for exact stamps in ASX summer time and wastes a day in Europe inconsistently); setting a missing own_relevance to the cross-sectional median (keeps a near-chance feature in the score).

## 2026-10-01 - Laya predictive test: pre-registration amended again (gate 06 round 2), before any full run
**Context:** Gate 06 r2 passed with two pre-registration details that would be baked into the verdict after the run: date-only Google stamps reached European listings a day late (the next Pacific midnight equals the London, Xetra and Euronext opens most of the year, and "known strictly before the open" called it late), and `beats_baseline` set a sign-fitted score against a raw baseline, so a score that only tracks news attention with a flipped sign would "beat" it whenever attention predicts negatively. No full real-data run has happened; this amendment is committed before its code.
**Decision:**
- A date-only Google stamp (midnight US Pacific) is known by the next Pacific midnight and usable when that moment is at or before the listing's open (known = next Pacific midnight less one microsecond, compared strictly). European listings get it for day D+1, the US too; Tokyo still D+2.
- A date-only SEC stamp (00:00 UTC of the filing day) is known at 03:00 UTC the next day (EDGAR dates filings made up to 22:00 ET with that day). Exactly-timed stamps stay strict: usable only when known before the open.
- Opens are the opening-auction times where the open price is fixed then: Hong Kong 09:20, NSE and BSE 09:08, Shanghai and Shenzhen 09:25 (the rest unchanged).
- The attention baseline gets the combined score's treatment: its sign is fitted per segment on the same purged trailing window (the sign of its mean trailing IC, 0 with fewer than 20 days), and `beats_baseline` compares the combined score's counted-day IC with the sign-fitted baseline's over the same days. The baseline's raw IC is reported too.
- Follow-up, not changed: a user or discovery link counts for an article when first known by the end of the UTC day of its known time (the stricter "before the listing's open on its usable day" is recorded for later; live links carry 2026 first-known dates, so history is unaffected).
- The history run keeps its lock alive while it waits for GPU room, and stops with the reason when there is no CUDA GPU at all (unless the CPU is allowed). One helper names live model work from the job locks for industry research and the history run (`laya_service.live_model_work`).
**Alternatives:** Comparing against |raw baseline IC| (the gate's minimal fix; the fitted sign is the same treatment as the score); leaving the European lag (a handicap against Laya on 17 of 51 names, not a leak).

## 2026-10-01 - Laya predictive test: pre-registration amended a third time (gate 06 round 3), before any full run
**Context:** Gate 06 r3 passed, with one addition that cannot be made honestly after the run: headlines that echo a past price move ("shares rose") look like price momentum, and in the gate's simulation a correctly timed echo of each European session's move passed the 20-day bar on 4 of 5 seeds. The attention baseline does not control for that. No full real-data run has happened; this amendment is committed before its code.
**Decision:**
- A second baseline, `own_momentum`: the listing's own return over the feature window in industry-excess terms, from the close of its last bar dated before t - L to the close of its last bar dated before t (sessions strictly before t's open; each within 5 calendar days of those days, the split guard over the closes between), minus the equal-weight mean of the same-window return of its static same-industry competitors (3 or more), else of the other main-list companies (3 or more); 0 when it cannot be computed. L = 7 and 28 calendar days as for the features.
- It is treated like the attention baseline: its sign fitted per segment on the same purged trailing window, scored on the same counted days as the combined score, reported as `momentum_ic`, `momentum_t`, `beats_momentum`, with its raw IC in the feature table.
- The pass bar is unchanged (spec decision 6 on the combined score). The verdict text says Laya only counts as useful if its combined score beats both baselines.
**Alternatives:** Removing momentum from the returns (changes the pre-registered target); adding momentum to the combined score (then the score is no longer Laya's).

## 2026-10-01 - Laya in the satellite: the weight rule, written before any run
**Context:** Ticket 08 (spec decisions 6, 7): Laya's daily score joins core-sat's satellite only if it passed 06's pre-registered test; its weight must be fixed a priori, never hand-set or fitted to a backtest. This entry is committed before any backtest, walk-forward or live step that reads a Laya score.
**Decision:**
- The verdict read is the newest kept result of the `main` run for Laya's model tag (`laya_service.MODEL_TAG`); the sensitivity run and other models' results (09's reader) never give Laya weight.
- Weight: only when that verdict is PASS and the combined score also beats both baselines (attention and momentum) on both horizons ("useful only if it beats both", amendments 1 and 3). Then in each mood Laya's weight is the mean of that mood's four price methods' weights (trend, momentum, reversion, low volatility) as configured in `[regime]`: one more price-method-sized method, and the row is renormalised as every row is. From the presets: calm_up 0.125, volatile_up 0.1375, calm_down 0.1625, volatile_down 0.15. Anything else (FAIL, INCONCLUSIVE, a PASS that does not beat both baselines, no verdict stored) gives 0 in every mood. No setting changes it.
- The backtest and walk-forward read the verdict stored today, which was itself measured over the same history; the `core_sat_laya` caption says so (an in-sample choice of whether it trades at all, never of how much).
**Alternatives:** An equal share of all eight methods (the hold and verdict methods are not evidence of the same kind); the trailing IC as the weight (a second fit on top of the one the test judged); PASS alone (the amendments call a score that does not beat both baselines not useful).

## 2026-10-01 - Laya's daily score, one for the backtest and the live lines
**Context:** Ticket 08 needs a point-in-time Laya score per main-list company for any day, built as 06's combined score so the backtest and live read the same thing.
**Decision:**
- Features: 06's main run exactly (own, competitor, industry per-article direction means; the link rule by the article's day; `usable_day` per listing's open; dated articles only). The articles are each company's backfill (as in 06) plus what the daily industry research (07) found for it, so the live days have news; the backtest reads the same union, so both see the same thing.
- Score for decision day t (the listing's local day whose open it feeds): per horizon (5 and 20 days), the weights fitted as 06 fits them for the 6-month segment (from 2022-09-21) containing t, on the purged trailing 730 days; the horizon's score is the sum over features of weight x (2 x cross-sectional percentile rank - 1) over every main-list company on t (a company without news ranks with zeros; no forward return is needed, so none is read). Laya's score is the mean of the horizons that have a fit, in [-1, 1]; a day with no fit has none.
- It enters the regime book dated t - 1 (a calendar day), so a bar on or after t - 1 reads it and its order fills at a later open, never before t. It stays usable 7 days (`extra_max_age`).
**Alternatives:** The 5-day horizon alone (the test judges both); ranking only companies with a bar on t (live has no bar for t yet); only backfilled articles (the live line would go stale once the backfill stops).

## 2026-10-01 - The laya paper account
**Context:** Ticket 08 (spec decision 7): Laya runs as a live paper line for evidence whatever the verdict.
**Decision:** `laya` is a regime line on the main list's companies (never a fund or Tesla, plus anything it holds) whose book weighs Laya's score alone (weight 1 in every mood, every other method 0), traded under the strategy's rules like the regime lines. It has its own full starting capital (`[risk] account`), not a 25% sleeve, so its record reads beside the regime line's on the same scale and the cash floor and sizes are the regime line's. Tick stage `laya_step` right after `core_sat_step`; Settings `[laya_account] enabled` (on); Ledgers card labelled with the verdict and, unless it earned weight, "weight 0 in core-sat"; the core-sat card shows the verdict and the weight; the holiday/morning report has a line for it. A listing with no Laya score (no news fit yet, or none within 7 days) is not leaned on.
**Alternatives:** A 25% sleeve (its return would read as a quarter-sized line beside full ones); the satellite blend with Laya added (then it measures the blend, not Laya).

## 2026-10-01 - Laya in the satellite: gate 08 round 1 fixes
**Context:** Gate 08 r1 passed with follow-ups: any kept main run gave weight whatever list it ran on; the live laya step read news one session staler than the backtest's fill; a held listing that lost its Laya score was never sold.
**Decision:**
- Each kept test result records its watchlist (with its start, already stored). Only the newest kept main run for Laya's model on the main list from 2022-09-21 is the verdict; a run on another list or from a later start is a look, never weight. A result kept before the list was recorded never gives weight. No migration: both live in the kept JSON.
- Live steps (laya, and core-sat once Laya weighs) give the book, at the step's bar day, each listing's score for the decision day its order fills on: its next open after the tick, from only the articles stored as known by the tick (`laya_live`). Earlier days stay dated as the backtest dates them. Every article read is in the database at the tick, so nothing leaks; news known after the tick but before the fill open is not read (the backtest's fill reads it, so live can only be staler by that, never fresher).
- The laya account sells a holding that has no Laya score within 7 days (left the main list, not its company's primary listing, or no fit), with that reason: Laya alone decides it, so nothing holds it.
- Logged, not changed: (3) scores are dated the calendar day before their decision day, so a Friday bar reads Saturday's score and weekend or holiday news first reaches the Tuesday fill in the backtest (an over-lag, never a leak); keying to the listing's previous session would match 06 exactly. (5) A live step rebuilds the panel (about 45 s on a fresh live copy with no scores yet), and core-sat builds it again once Laya weighs; a per-tick shared panel or cached segment fits is the fix when it grows. Also: kept results are pruned to the newest 20 per run, so 20 later looks on other lists would push the verdict out.
**Alternatives:** A migration with list and start columns (the JSON already carries them); keying live scores by the tick day (the backtest stamps by the bar, so the book would never read it); flagging an unscored holding on the card only (it would be held on no evidence).

## 2026-10-01 - The regime market is the traded set, not every stored equity (ticket 10)
**Context:** `market_history` fed every stored equity (minus funds and Tesla) into the regime books' mood, 12-1 momentum and low-vol ranks, health and playbook. Ticket 04's backfill stored bars for 270 industry-map companies, so the market grew from 124 to 394 and every regime-book line moved (continuous 4y regime +58.4% to +26.9%) with no change to any traded listing. A data-fetching job should never change a trading line's universe, and the map companies, chosen today, add survivorship.
**Decision:** Coordinator's rule, reversible: `market_history` (the one function every regime book reads its market through: backtest, walk-forward, replay study, live regime, regime-ai, regime-v2, core-sat, and laya after ticket 08) keeps only companies on any watchlist (the discovered list included) or held by any paper account (`prices_service.held_or_watched`), plus the listings the caller trades; never a fund (unless traded) or a never-listed company (Tesla). A listing only stored is never read. Same rule replayed and live; the caption says so.
**Alternatives:** Main list only (drops other lists and the discovered companies the live accounts trade); freezing the pre-backfill 124 (an arbitrary snapshot that a new watchlist could never join); every stored equity (lets fetching change the books).

## 2026-09-30 - Stronger reader: how the local model is picked, written before the bake-off
**Context:** Ticket 09 (user: "do a stronger model, local if possible"): score the backfilled headlines with a local Ollama chat model and run 06's test on those scores. Three candidates: `qwen3:4b-instruct-2507-q8_0`, `gemma4:12b`, `qwen2.5:32b` (partly on the CPU). This rule is committed before any candidate reads a bake-off headline.
**Decision:**
- Sets: the 110-headline sanity set (`tests/fixtures/laya/sanity.json`) and a fresh held-out set of about 100 backfill headlines from a read-only copy of the live DB, none in the sanity set, labelled by the builder (relevant, direction up/down/none, event type) and committed before any model (Laya included) reads them. Held-out is the deciding set; sanity is reported beside it.
- One prompt and one batch format, fixed in code before the held-out run. They may change only to fix answers that cannot be parsed, seen on the sanity set; each candidate reads the held-out set once, with the final prompt, temperature 0, batch 25.
- Accuracy: direction and event accuracy over the rows labelled relevant; relevance accuracy over all rows as a secondary figure. Baselines on the same rows: the majority class, and Laya.
- Speed: headlines a second over the whole bake-off (both sets, batch 25, model already loaded, the live Ollama as it is). Full-run estimate = about 450,000 articles / that rate (each article read once, for its own subject company).
- Eligible: full-run estimate at most 12 hours and at most 2% of headlines unparsed after one retry.
- Pick: the eligible candidate with the highest held-out direction accuracy. Within 3 points of each other: the higher held-out event accuracy; still within 3 points: the faster. If no eligible candidate beats the majority class on held-out direction, the most accurate eligible one is still picked and the report says it fails that baseline. No eligible candidate: the fastest one, with its estimate stated, for the coordinator to decide.
**Alternatives:** Picking on the sanity set (Laya's wording was chosen on it, and its labeller saw Laya's answers); the most accurate model regardless of time (qwen2.5:32b on the CPU could take days while the live app shares the GPU).

## 2026-09-30 - Stronger reader: bake-off result and the model picked
**Context:** The picking rule above, run on the 110-headline sanity set and the 105-headline held-out set (79 labelled relevant; labels committed before any model read them). One prompt and format for all (one code a headline in a constrained one-line JSON array, batch 25, temperature 0, `think` off), fixed before the held-out run and never changed after. The live app's Ollama as it was (nothing else loaded), RTX 5060 8 GB.
**Decision:** Held-out, direction / event / relevance (rows labelled relevant for the first two): majority class 68.4 / 43.0 / 75.2; Laya 69.6 / 32.9 / 30.5; qwen3:4b-instruct-2507-q8_0 64.6 / 46.8 / 68.6 at 16.6 headlines a second (full run of about 450,000 about 7.5 h); gemma4:12b 55.7 / 62.0 / 86.7 at 3.8 a second (about 33 h, 30% on the CPU); qwen2.5:32b 74.7 / 55.7 / 83.8 at 1.0 a second (about 122 h, 70% on the CPU). Sanity set: majority 55.3 / 36.8 / 69.1; Laya 64.5 / 51.3 / 38.2; 4B 61.8 / 38.2 / 75.5; 12B 55.3 / 60.5 / 76.4; 32B 61.8 / 67.1 / 90.0. No headline went unparsed. Only the 4B model is eligible (12 h), so it is picked (`[laya] reader_model` default), and the report says it fails the majority baseline on held-out direction (and is below Laya there). When it calls a direction on a held-out headline labelled up or down, its sign is right 13 of 16 times (32B 19 of 20, 12B 14 of 16); most of its direction misses are "up" on headlines labelled none (13).
**Alternatives:** qwen2.5:32b, clearly the best reader on direction and relevance, is ineligible by the rule at about 122 h for the full run; the coordinator may choose it for a smaller run (own news of the main list only), outside this rule.

## 2026-09-30 - Stronger reader: one reading per headline, pairs reuse it
**Context:** Ticket 09: batch many headlines a call, cache by (article, subject, model), each article scored once for its own subject company, competitor and industry features reuse that score through the relation, and 06's aggregator and test read the scores unchanged.
**Decision:** The reader's scores go in `laya_scores` under the tag `ollama:<model>` (no migration). Each (subject, article) the history groups need is read once, for the subject, and stored under the subject as `own`; every pair whose subject is another company (competitor, industry, ...) is then stored for the target by copying the subject's row with the pair's relation (INSERT OR IGNORE, an earlier group's relation wins as in 06's history scoring). A target's own articles are never copied over, even when a run stops before reaching them. A reading is a choice, not a probability: the chosen direction is stored with probability 1 (so 06's direction x probability is +1, -1 or 0 an article) and relevance is 1, or 0 when the model says the headline is not about the company. A headline left without an answer after one retry on its own call is not stored (a later run asks again). One run across processes (lock `reader`); it waits in 5 s steps while research, discovery, discovery chat, a tick or industry research holds its lock (any process), while in-app research, a tick, a question, discovery or industry runs, and while a scheduled tick or midday pass is due within 30 minutes or due and not yet run; Cancel stops it between batches. Activity job `reader` (retry resumes), CLI `oracle reader run|plan`, the Ledgers tab's Stronger reader card (start, count and estimate, last run).
**Alternatives:** A sibling table (06 would need a second reader path); scoring every (article, target) pair (the pairs are about 3 times the headlines, and the target's name in the prompt changes only the relation, which the walk-forward fit signs anyway); logprob probabilities (not offered through the constrained format, and a second call per headline).

## 2026-10-01 - Stronger reader: the user overrides the 12 h cap, qwen2.5:32b on own news only
**Context:** The bake-off under the picking rule above left only qwen3:4b-instruct-2507-q8_0 eligible, and it reads worse than the majority class on held-out direction (64.6 against 68.4; Laya 69.6). qwen2.5:32b read best (held-out direction 74.7, event 55.7, relevance 83.8; sanity 61.8 / 67.1 / 90.0) but at 1.0 headline a second, about 122 h for the full backfill. The user chose on 2026-10-01, after the bake-off. Before that choice the 4B read 27,091 history headlines of 6 main-list companies on a live-DB copy (23:45 to 00:12) as a throughput sample only: no test ran on those scores (06's test was not yet on the branch), and the 32B had read no history. This amendment is committed before the code that implements it.
**Decision:**
- The user overrides the rule's 12 h eligibility cap: the stronger reader is `qwen2.5:32b` (`[laya] reader_model` default). The 4B is not run on the history.
- Scope: the main list's own backfilled articles only (about 100,000 headlines, about 27 h at the bake-off rate). No map company's news is read, so this reader has no competitor, supplier, customer or industry pairs.
- Its predictive test therefore runs the own feature only (the per-article own direction mean), plus the pre-registered news-count baseline, under the same pre-registered bar (spec decision 6 and the 06 pre-registration with its gate-06 amendment: date-only stamps known 24 h later, usability per listing's open, walk-forward fit, counted segments). No parameter is chosen after seeing results. Laya's test is reported beside it as pre-registered (all its relations), and Laya's own-only figure is shown too so the two readers compare on the same feature.
- Bake-off numbers stand as recorded in the entry above; the choice is the user's, not the rule's.
**Alternatives:** The rule's pick, the 4B (below the majority baseline on direction, so a fail would say little about news); the 32B over the full backfill (about 122 h); gemma4:12b (worse on direction than both baselines).

## 2026-10-01 - Predictive test: an own-news-only run and a model choice
**Context:** The user's amendment above: the stronger reader (qwen2.5:32b) reads only the main list's own news, so its test runs the own feature alone, and Laya's own-only figure is shown beside it. 06's test read one model (Laya's tag) and two runs (main, sensitivity).
**Decision:** `run_laya_test(..., own_only=True)` is a third run, `own` ("own news only"): only each company's own backfilled articles count (every other pair is left out before the features are built), and the combined score is the own direction mean alone, fitted and judged exactly as the main run (same windows, lag, usability per listing, purge, counted segments, bar, attention and momentum baselines). It cannot be combined with the sensitivity run. The test reads any model's scores (`model`, Laya's tag by default). Kept results are now per run and model (20 each), and the card and `--show` list the newest of each, main then own then sensitivity, Laya first. The Laya predictive test card gains "Scores of" (Laya or the configured reader's tag) and an "Own news only" box; the CLI gains `oracle laya test --model TAG --own-only`. The wording "Laya beats it" becomes "the reading beats it", as the reading may be the stronger reader's.
**Alternatives:** A separate test entry point for the reader (two copies of the pre-registered math); keeping one kept result per run (the reader's would replace Laya's).

## 2026-10-01 - Stronger reader gate 09 r1: mutual waits, coverage shown, own-only by default
**Context:** Gate 09 round 1 (PASS WITH FOLLOW-UPS) before the 33 to 35 h run: the reader and Laya's history scoring could run at once (the 32B holds most of the GPU and CPU), neither saw a regime-AI backtest, a head-to-head could hide unequal coverage, the CLI read the full history by default, a timed-out batch blocked twice its timeout, a failed run read "finished", and the test took any model tag.
**Decision:** The reader waits on Laya history scoring's lock and history scoring on the reader's (`laya_history_service.READER_JOB`, `history_waits_anywhere`; neither waits on its own), and both jobs wait for a running regime-AI backtest (`_laya_history_waits`). Every test result carries `own_coverage` (the main list's own backfill pairs and how many the model scored), shown on the card and in the CLI, in red with "not like for like" when they differ; the pre-registered test itself is unchanged. `oracle reader run|plan` reads own news only unless `--all`, as the card's box. A call times out after 300 s (a batch takes about 26 s) and a timed-out batch is not asked again, so a Cancel answers within one timeout and the lease (1800 s) never lapses. A run that fails or is taken over records why (`failed: ...`, `taken over by another process`). A reader's result adds a caveat: it scores off-topic headlines 0 and reads headlines only. The test refuses a model other than Laya's tag or the configured reader's.
**Alternatives:** Restricting both readers to the articles both scored (changes the pre-registered test; would need an amendment before any result); one shared "heavy model work" lock (a larger change to 06's merged job).
**Logged, not fixed:** a full-scope run can let a competitor copy stand in for a target's own reading when the backfill later adds that article to the target's own set (own-only runs, the planned run, are not affected); the reader card has no list picker (`--list` maps to the default list).

## 2026-10-01 - Stronger reader gate 09 r2: Laya history scoring goes first, the reader waits
**Context:** Gate 09 round 2: after r1 each of the two jobs held its own lock and waited on the other's, so two runs started together waited on each other for ever.
**Decision:** The wait is one-way. Laya's history scoring never waits for the stronger reader (`history_waits_anywhere` is HISTORY_WAITS_FOR again); the reader waits while the `laya_history` lock is held, unloading its model so the GPU is Laya's, and resumes once it is released. Tested with both locks held at once and with the reader resuming after the release. This replaces the r1 entry's "history scoring waits on the reader's" lock.
**Alternatives:** One shared lock for both (a change to 06's merged job and its tests); ordering by start time (two clocks, harder to reason about across processes).

## 2026-10-01 - After Laya: fix the testbed, then test signals with published evidence (grilling)
**Context:** Buy-and-hold beats every active line; Laya failed the pre-registered test. On 51 names over 4 years a 20-day IC needs about 0.04 to reach t 2, larger than most published effects.
**Decision:** Point-in-time S&P 500 from 2010 in a separate research DB, missing share and power printed on every result. Pre-registered signals: 12-1 momentum, 1-month reversal, SUE (XBRL EPS vs a year before), gross profitability, earnings yield, Form 4 buys; filing-typed event studies only. Choose on 2010-2019, confirm on 2020+ (t > 2, majority of segments). Model-read signals count only after the model's training cutoff (2025-05-31 when unknown). Real-money plan is index buy-and-hold; index paper line added; strategy, regime and regime-v2 frozen; core-sat without the yearly rebalance, satellite 0 until a confirm pass, then 6 months on paper, satellite capped at 20%. Laya/32B: judged on the same bar, wound down if it fails.
**Alternatives:** More signals on the 51-name testbed (underpowered); paid survivorship-free data now (only if a signal passes); headline-typed events (text reading just failed); t > 3 on the whole period instead of a held-out confirm.

## 2026-10-01 - Model training cutoffs: the table, and how the predictive test applies it (ticket 03)
**Context:** Spec decision 7: a model-read signal counts only on days after the model's training cutoff (2025-05-31 when unknown; the later date when sources differ, since a later cutoff throws away more days). The cutoffs had to be looked up and cited.
**Decision:** Table in `oracle/research/training_cutoffs.py`, by model tag:
- `laya-multilingual@e4e9ddf` (base `convaiinnovations/laya-multilingual` on `jhu-clsp/mmBERT-base`): **2025-05-31** (unknown). The Laya card (huggingface.co/convaiinnovations/laya-multilingual) gives no training data or dates for its decision head. mmBERT (huggingface.co/jhu-clsp/mmBERT-base, paper 2025-09-08) names its corpora but no cutoff; its FineWeb2 web data spans Common Crawl to April 2024 (arxiv.org/abs/2506.20920), but the fine-tune data is undated, so the default applies.
- `ollama:qwen2.5:32b`: **2025-05-31** (unclear). Alibaba publishes no Qwen2.5 cutoff (model card huggingface.co/Qwen/Qwen2.5-32B-Instruct, release 2024-09; Qwen2.5 technical report arxiv.org/abs/2412.15115); third parties give October 2023, end of 2023, or 2024-06-30 (OpenRouter; github.com/HaoooWang/llm-knowledge-cutoff-dates; metehan.ai/articles/llm-knowledge-cutoff-dates). Sources differ, so the default.
- Any other tag: 2025-05-31.
How the test applies it: the verdict (and `horizons`) counts only decision days after the cutoff (the return window then lies wholly after it). A segment ending on or before the cutoff is labelled "inside the model's training" and never counts. A segment across the cutoff is **trimmed** to its later days and keeps its pre-registered trailing fit (fitted at the segment's original start), so post-cutoff numbers are exactly the full run's daily ICs restricted to later days; the usual 20 IC day minimum still applies. The full period is computed from the same ICs and kept beside (`full_horizons`, `full_verdict`), shown on the CLI and the Ledgers card but never the verdict. No schema change: both are in the kept JSON. Laya's FAIL stands (judged before the rule): a Laya result shows "Laya's FAIL stands" and gives the satellite verdict FAIL whatever its post-cutoff label. A result kept before the rule (no cutoff) is shown as full period and gives no weight until run again.
**Alternatives:** Leave a straddling segment out (wastes up to 6 of about 16 post-cutoff months); refit at the trimmed start (changes the pre-registered fit); the release date as the cutoff for Qwen2.5 (2024-09, a hard bound, but the rule says the default when unclear); third-party Qwen dates (disagree).

## 2026-10-01 - Stronger reader reads newest first; a part-read segment is left out
**Context:** Job 719 (qwen2.5:32b) will be cut long before it ends. The reader built its to-do company by company, so a cut run left most companies unread, useless for a cross-sectional IC.
**Decision:** The to-do is sorted by each article's published (else fetched) time, newest first across every company; ties keep group order; a rerun still skips read pairs. The test now records, per main company, each dated own headline's first usable day and whether the model read it. A segment counts only with at least 95% of its own headlines read (`READ_SHARE`), else "N% of own headlines read" (the same per-segment rule as thin coverage); newest-first means at most one segment is part read. Each result reports the own headlines after the cutoff and how many were read (`post_read`).
**Alternatives:** Leaving out each day whose own headlines are not all read (exact, but more code and an unread headline still shifts neighbouring days' lookbacks); 50% like thin coverage (an unread headline is a silent zero in the features, so too lax); company-by-company order (fails the cross-section when cut).

## 2026-10-01 - Signal testbed data (next-signals 01): source, research DB and what counts as missing
**Context:** Spec decisions 2 and 3: a point-in-time S&P 500 from 2010 in its own database, from a free public membership history, with the missing share printed.
**Decision:**
- Membership: fja05680/sp500 (MIT), "S&P 500 Historical Components & Changes (Updated).csv", read at the commit the GitHub API names and stored with its fetch time (`tb_snapshot`). Each row is the whole member list from its date to the next row's; `members_on(day)` is the last row on or before the day. Tickers are kept as the source lists them, in Yahoo's spelling (BRK.B is BRK-B). The source is not strictly the ticker of that day: its pre-2019 base carries some companies under today's ticker back to 2010 (BKNG, MDLZ, KDP; PCLN, KFT, DPS never appear) and others under the old one (FB, ANTM), where a rename is a removal plus an addition. It also lists a few companies twice on the same days (KORS with CPRI 2016-01-04 to 2018-09-18, CCE with CCEP in 2016); coverage flags these known pairs so a test keeps one (a general check belongs with the rename map, ticket 01b).
- Research DB (`research_db_path`, default `research.db` beside the config) has its own migration list (`oracle/store/testbed_migrations`) reusing the live meta, source-pacing, job-lock and bars DDL. The build paces Yahoo and holds its lock on the research DB, so it writes nothing to the live DB (tested by dumping the live DB before and after). As the two databases pace Yahoo separately, the job also waits for the other Yahoo-heavy jobs (history, replay prices, universe) besides live work and a near tick.
- Bars: Yahoo from 2010-01-01 for every member at some point since, plus SPY and ACWI (benchmarks; SPY's bar days are the trading calendar). Bars after a member's last day are kept for 120 days only (forward returns): later ones may be a company that reused the ticker. A member Yahoo has nothing for is noted "not on Yahoo" (a 400 "data doesn't exist" counts) and not asked again; 5 failed requests in a row stop the run.
- Sectors: GICS from Wikipedia's current member table; a former member Yahoo still prices gets Yahoo's sector in GICS's words. Both are today's sector, not as it was (caveat printed).
- Coverage: member-days are SPY trading days inside a member's spans from 2010; with bars counts those with a stored bar; missing share is 1 minus their ratio, per year and overall; members never priced are listed with why.
- GUI: a "Signal tests" card on the Ledgers tab (Build testbed, Refresh testbed, per-year table, members never priced); ticket 02 adds its test runs there.
**Alternatives:** Wikipedia's "Selected changes" table (incomplete by its own heading); a paid survivorship-free set (only if a signal passes, spec 2); pacing on the live gate (writes breaker state to the live DB); a hand-made rename map pricing renamed members under today's ticker (unverifiable without names; a follow-up).

## 2026-10-01 - Signal testbed gate 01 r1: reused tickers, refresh, narrowed runs, under-priced members
**Context:** Gate 01 round 1 (FAIL): the real build crashed on GR (Yahoo serves it as a null-currency MUTUALFUND) and every resume crashed there; a refresh before new bars marked current members "no bars" for good; a narrowed smoke run cut bars permanently; SUN carried Sunoco LP's bars; the never-priced list hid members priced on one day.
**Decision:**
- A member's chart counts only when Yahoo says it is EQUITY with a currency; otherwise it is noted "ticker reused by another instrument" (bars dropped). A former member whose Yahoo first trade day is more than 7 days after its first member day (from 2010) is noted the same way. A current member is never judged by its first trade day: today's holder of its ticker is the member. Any other per-ticker error is that ticker's failure (5 in a row stop the run); the last-run record is written even when the build raises.
- A member that already has bars and gets nothing newer keeps its note; only its tried time moves.
- A run with `--from` other than 2010-01-01 or with `--to` marks no member tried, so a full build fetches them whole. `--only` and `--limit` fetch the full period, so their members are marked as usual.
- Coverage lists members priced on under 50% of their member-days ("priced on X of Y member-days"), not only those with none, and flags the known duplicate pairs. Status and the card say when the snapshot's last change is before today. The card reloads its table when the build job ends.
**Alternatives:** Requiring a first trade day check for current members too (would drop FOXA-style relistings whose current bars are right); refusing narrowed runs on the default research DB (stops a valid smoke run); a 0-bars-only list (hides EA, EQR, AVB).

## 2026-10-01 - Signal test (next-signals 02): pre-registered before any real-data run
**Context:** Spec decisions 1, 5, 6: one general test for any daily per-company score on the testbed, with 12-1 momentum and 1-month reversal first. Written and committed before the harness reads a real testbed; nothing below is chosen after seeing a result. A change is a new pre-registration, committed before its run.
**Decision:**
- Universe on each trading day (SPY's bar days from 2010-01-04): `Testbed.members_on(day)`. Of a known duplicate pair (KORS/CPRI, CCE/CCEP) the later listing is dropped on days both are members.
- Decision at the open of day t. A score reads closes up to t-1 only. Prices are Yahoo's, adjusted by each bar's adj_close / close (opens too), so dividends and splits are in.
- Return: open of t to open of t+h trading days. It needs a bar on t and t+h, every close from t-1 to t+h-1, and no split-sized step (outside half to double) among those closes or between a close and the next open. Sector excess: minus the equal-weight mean return of the other members that day in the same GICS sector (today's sector) with a return, 3 or more; else of every other member with a return, 3 or more; else left out.
- IC: Spearman across members with a score and an excess return, per day, 5 names or more (`MIN_NAMES`).
- Windows: train 2010-01-01 to 2019-12-31 (chooses and reports, never a verdict), confirm 2020-01-01 to the run day (the verdict). Six-month segments from each window's start (`walk_forward.segments`, a last part of half a segment or more kept). A decision day counts in a window only when its exit day is inside it. A segment counts with 20 IC days or more. Window IC: mean daily IC over counted days; t: Newey-West (Bartlett) with h lags. PASS = confirm IC > 0, t > 2, IC positive in more than half the counted segments; under 2 counted segments is INCONCLUSIVE (`predictive.verdict`). A signal with several horizons passes only if every horizon passes.
- Model-read signals (a model tag): only days after `training_cutoff(tag)` count, in both windows; a segment ending on or before it is "inside the model's training", one across it is trimmed (ticket 03's rule).
- Printed per window: power = 2 x sqrt(h / ((N - 1) x D)), N mean names per counted IC day, D counted IC days (effective days D / h); the observed power (2 x the counted ICs' own Newey-West standard error); missing share (member-days in the window without a bar); counted segments.
- Signals, higher score = higher expected excess return:
  - `momentum_12_1`, horizon 20: adjusted close 22 trading days before t over the close 253 before t, minus 1 (the year to the last known close, leaving out its last 21 days). Every close between present, no split-sized step.
  - `reversal_1m`, horizon 5: minus (close of t-1 over the close 22 before t, minus 1). Same data rule.
- Caveats printed with every result: 2010-2014 misses 25-35% of member-days (delisted, acquired and renamed members; ticket 01b maps renames), so a train result is survivorship-weighted and a pass driven by 2010-2014 is provisional; sectors are today's; membership after the snapshot's last change repeats that day's list; a member that stops trading inside its h-day window has no return and drops out of that day.
- Laya's test is not changed: it keeps its own harness, sharing `predictive.py`'s IC, t and verdict.
**Alternatives:** The live 12-1 momentum (`price_factors`, calendar year and month, anchors within 10 days): its per-day scan is too slow for 4,000 days of 500 names, and trading-day anchors are the published convention. Raw opens like Laya's (leave out dividends). A Bartlett-weighted effective day count for power (a simulation of a held-still score with a known IC put the harness's t at 1.7 at that IC; the plain D / h put it at 2.0 to 2.3). Choosing on all years with t > 3 (spec 6 chose a held-out confirm).

## 2026-10-01 - Signal test: where it runs, what is kept, and how far its t can be trusted
**Context:** Ticket 02 builds the harness the pre-registration above describes.
**Decision:** Math in `oracle/analysts/signal_test.py` (a price panel on the trading calendar with adjusted opens and closes as arrays, the excess return, daily IC, window result and power), reusing `predictive.py`'s Spearman IC, Newey-West t and verdict; the registry and runner in `oracle/services/signal_test_service.py`, reading the testbed only through `Testbed` (`calendar`, `members_on`, `bars`, `sectors`), so ticket 01b's aliases apply without a change here. Results are kept in the research DB (`signal_tests`, migration t0002, 20 per signal) as JSON. A test is a `signal_test` job that waits like the testbed build (live work, a near tick, Yahoo-heavy jobs) between chunks of 250 days and keeps nothing when cancelled; Retry reruns it. `oracle signals list|test <name>` and the Signal tests card (Run test per signal, power, missing share, train, confirm, verdict, segments) do the same. Laya's test is left as it is: its fitted, multi-feature score does not fit a one-score registry, and leaving it untouched keeps its stored numbers identical by construction.
A simulation of a held-still score (30 panels per case) puts the harness's mean t at 2.0 to 2.3 when the true IC equals the printed power, and the t's spread under no signal at 1.09 to 1.14 rather than 1: Bartlett weights with h lags undercount overlapping windows a little, so t > 2 happens by chance about 4% of the time instead of 2.3%. The bar is kept as pre-registered; this is printed here so a t just over 2 is read with it.
**Alternatives:** One harness for Laya and the signals (a rewrite of a pre-registered, already-judged test for no gain); results in the live DB (the testbed's results belong with its data); a Hansen-Hodrick (flat-weight) t (not what spec 6 and Laya's test use; would be an amendment).

## 2026-10-01 - Signal test gate 02 r1: observed power first, train-only runs, waits for a build
**Context:** Gate 02 round 1 (PASS WITH FOLLOW-UPS). The formula power assumes a day's IC spreads 1/sqrt(N - 1); momentum's real daily IC spreads about 3.9 times that (a common factor moves every rank together), so the card's "power" read about 3 times too optimistic. Tickets 04/05 will choose rules on train and must not see confirm first.
**Decision:**
- The card's Power column shows the observed power (2 x the window's own Newey-West standard error), with the formula figure beside it, labelled. A FAIL whose IC is above 0 but under its observed power reads "FAIL (could not see an IC this small: needed X)", on the card and in the CLI. The pass rule is unchanged.
- Train-only runs: `oracle signals test <name> --train-only`, the job option `train_only` and the card's Train only button. They load no bar after 2019-12-31, compute no confirm, have the verdict "TRAIN ONLY", and are kept under their own key, so they never hide the full result.
- A signal test also waits while a testbed build or refresh runs, so it never reads a half-filled testbed.
- Duplicate pairs: the next pre-registration keeps whichever listing has a bar that day (CPRI and CCEP have bars, KORS and CCE have none). The two pre-registered tests above keep the earlier listing as written: train only, one company.
**Alternatives:** Replacing the formula with the observed figure everywhere (the formula still says what the names and days could give with independent ranks); hiding confirm in the display only (it would still be computed and kept).
## 2026-10-01 - The index paper line: ACWI bought once and held, and every table leads with ACWI and SPY
**Context:** Next-signals ticket 06 (spec 4, 11): a broad index is the honest "do nothing". The live DB had ACWI and SPY in the universe but no bars.
**Decision:** A live account `index` (first in ACCOUNTS, a trader, no switch): its step (tick stage `index_step` after analysis, the Ledgers "Index step" button, `paper step --account index`) buys ACWI with all its cash at the next open when it holds nothing and nothing waits, under the paper accounts' costs, then holds forever. With no ACWI bars stored it still orders and says so: the price update prices every ordered listing, and the order fills at the first open after it was placed. The tick's price stage also refreshes ACWI and SPY when nothing holds or orders them (for the backtest). Backtest, walk-forward and replay study lead with `index` (ACWI) and `spy` held from the first day with no costs (the replay study's in the fund's own currency, as its other lines); a fund with no stored bars from the start gives an empty line and a note, and is left out of the walk-forward. The main-list buy and hold stays, labelled "Buy and hold (today's list)", its caption saying survivorship. The walk-forward still judges every line against that buy and hold, so earlier kept runs compare. ACWI is a fund, so it never enters the regime market or core-sat's core.
**Alternatives:** Configuring ACWI and SPY as `[compare] benchmarks` (they are not traded and need no account, but the spec wants a live line with costs); judging the walk-forward against ACWI (breaks comparison with kept runs and needs ACWI bars for every run); VWRL.L (GBP, already a benchmark, but the spec names ACWI).

## 2026-10-01 - strategy, regime and regime-v2 frozen like regime-ai; the ai account kept
**Context:** Spec 11: keep index, momentum, core-sat and laya; freeze strategy, regime and regime-v2, card and history kept, no step. The brief asked whether the `ai` account is the same "strategy" line.
**Decision:** `paper_service.FROZEN` gains strategy, regime and regime-v2, each with its reason (`FROZEN_WHY`, `frozen_note`): `account_on` is false, so no tick stage, button or CLI step decides anything, the fill pass records no more equity (orders placed before still fill), and the cards, decisions and readings stay. `strategy_step` returns None while frozen; POST /api/ledgers/step returns `{off, decisions}` like the AI step. Settings marks the `enabled` key of [regime_account], [regime_ai_account] and [regime_v2_account] "ignored: frozen". The Ledgers tab drops the Regime and Regime-v2 step buttons (as Regime-AI's was) and keeps Strategy step, which says it is frozen (the CLI's `paper step` needs a GUI control). The `ai` account is not the strategy line: it trades the research verdicts, the strategy the technical leans, so it is left as is. Tests pin the code the lines stepped with: an autouse fixture keeps only regime-ai frozen unless a test is marked `frozen_lines`.
**Alternatives:** Turning their switches off (Settings could turn them back on); deleting the accounts (loses the history the spec keeps); freezing `ai` too (not what the spec names).

## 2026-10-01 - Core-sat without the yearly rebalance, satellite weight 0 until a signal earns it
**Context:** Spec 9, 10: core-sat stays on paper without the yearly rebalance; satellite weight 0 until a signal passes the confirm, and a pass first earns its own 6-month paper line, then at most 20%. No such mechanism exists yet. The live account placed 75/25 orders on 2026-09-30.
**Decision:** `broker.core_sat`: `SATELLITE_SHARE = 0.0` (cap `SATELLITE_CAP = 0.20` recorded), `REBALANCE_YEARS = None`: the core buys equal amounts once and holds; at share 0 the satellite replays nothing and the core gets all the capital. The rebalance and share code stays behind arguments, so its tests still pin it. Live: the sleeves split by the share (core all of it); the core phases are START (no orders) and TOP_UP (no order waiting and idle core cash of at least 5% of its capital, bought towards equal shares, never selling), so the live account's unspent quarter flows into the core once the satellite is out. At share 0 the satellite's step drops its waiting buys (rejected, "satellite weight 0 ...") and sells what it holds; the core's spendable cash is never more than the account's free cash. Card, caption, Ledgers text and holiday report say why the satellite is empty. No migration: orders keep their sources.
**Alternatives:** Keeping 75/25 with the quarter in cash (weight 0 would then be a cash drag, not a weight); restarting the live account (loses its record); a share read from Laya's verdict (spec 10 wants six months of its own paper line first).

## 2026-10-01 - A satellite buy never fills while the satellite's weight is 0 (gate 06 round 1)
**Context:** Gate 06 r1 M2: the tick fills before core-sat steps, so the live account's 10 waiting satellite buys (placed 2026-09-30 at 25%) would fill and then be sold the same tick (about 1,280 GBP of paper costs, 20 pointless orders, the core's top-up split in two).
**Decision:** The fill pass rejects any waiting buy from core-sat's satellite (`paper_service.SAT_SOURCE`) while `SATELLITE_SHARE` is 0, with the "satellite weight 0" note, so no deploy step is needed. Satellite sells while the weight is 0 say it "sells what it holds and buys nothing" (`SATELLITE_EXIT`). The Index step button and CLI run under the tick's lock (`run_index_step`), so a press never orders ACWI beside the tick's own step.
**Alternatives:** A recorded deploy step (press Core-sat step before the first tick; easy to forget); moving core-sat's step before the fill stage (changes every line's order of events).

## 2026-10-01 - SEC fundamentals (next-signals 04): what is fetched and when a figure is known
**Context:** Spec decision 5: SUE, gross profitability and earnings yield from SEC XBRL, known at the filing's acceptance time plus the next-session rule. Ticket 04 builds the data under the signal harness of ticket 02.
**Decision:**
- Fetched into the research DB only, for the testbed's priced members (members with bars): SEC's `company_tickers.json` for the CIK (the ticker as Yahoo spells it, else with a dot, else without the dash; SEC's map is today's tickers only, so a member renamed since without a current SEC ticker is "no CIK" and listed), `companyfacts` for the concepts below, the submissions (recent list and the older pages back to 2009) for each periodic report's `acceptanceDateTime`, and Yahoo's split events from 2009. Source `sec_edgar` (5 a second, SEC allows 10) with the existing User-Agent and contact. Share classes with one CIK are fetched once. A resumable job (`xbrl`) waits for live work, a near tick, Yahoo-heavy jobs and a testbed build; a signal test waits for it. CLI `oracle testbed fundamentals|fundamentals-status`, the Signal tests card's "Fetch SEC fundamentals" / "Refetch" and its coverage table per year (members, with a CIK, with facts, per signal members with a scored filing and how many).
- A fact's known day is its filing's acceptance day (New York time, as the event study reads it), else its filing date; a decision day uses it only when it is after that day (the next session), as ticker 02's scores read only closes before the decision day. Every filing's value is stored; readers take the first-filed value of each period (a restatement is never what was known then).
- Per-share figures are put into today's shares by dividing by the product of the Yahoo split ratios after the figure's known day, matching Yahoo's split-adjusted closes (the stored `close`, not the dividend-adjusted one). Like the prices' own adjustment this carries only later splits, a mechanical scale, not later earnings.
**Alternatives:** Filing date only (a day late for reports accepted after 17:30); latest-filed values (restatements leak); deriving splits from XBRL restated comparatives (fragile timing); market cap from cover-page shares (still needs an unadjusted price).

## 2026-10-01 - Fundamental signals (next-signals 04): pre-registered before any real-data run
**Context:** Spec decisions 5 and 6. Written and committed before any of these signals is run on real data; nothing below is chosen after seeing a result. A change is a new entry, committed before its run. SEC facts were being fetched while this was written (fetching data is not a run).
**Decision:**
- Harness, universe, timing, returns, sector excess, IC, windows, segments, t, power, missing share and the PASS rule exactly as "Signal test (next-signals 02): pre-registered before any real-data run", with one change from gate 02 L3: of a known duplicate pair both in on a day, the listing with a bar that day is kept (the later one when both or neither have one). Each signal has its own verdict; there are three, so about 3 x 4% chance that one passes by luck (no correction, as spec 6 sets the bar).
- Facts: taxonomy us-gaap; forms 10-K, 10-Q, 10-KT, 10-QT and their amendments; units USD, and USD/shares for EPS. Concepts, per period the first in the list with a value:
  - EPS: EarningsPerShareDiluted, EarningsPerShareBasicAndDiluted, EarningsPerShareBasic.
  - Gross profit: GrossProfit; else revenue minus cost of revenue for the same period, known when both are. Revenue: Revenues, RevenueFromContractWithCustomerExcludingAssessedTax, SalesRevenueNet, RevenueFromContractWithCustomerIncludingAssessedTax, SalesRevenueGoodsNet. Cost: CostOfRevenue, CostOfGoodsAndServicesSold, CostOfGoodsSold, CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization. Companies with neither (most banks and insurers) have no score.
  - Total assets: Assets (an instant).
- Periods: a quarter lasts 80-100 days, nine months 260-285, a year 350-380 (both ends counted). One quarter per end date (the earliest known). A fiscal year's fourth quarter, when not reported, is the year minus its nine-month figure (start within 3 days), else minus its three chained quarters (each starting 1-7 days after the last ended), when the year ends 80-100 days after the nine months; it is known when its last part is. A trailing year is four chained quarters, known when all four are.
- Score timeline per company: one event per quarter, known when every figure in it is; ordered by known day; an event is dropped when it is for a period no later than one already known, or known more than 180 days after its period ended (an old comparative); on a decision day the score is the last event known before the day, unless that event has no value (the newer filing could not be scored: the old score stops) or it was known more than 200 days before (the company stopped filing).
- Signals, higher score = higher expected excess return:
  - `sue`, horizon 60: the quarter's EPS minus the EPS of the quarter ending 350-380 days earlier (the nearest to 364), over the sample standard deviation (n - 1) of the same changes of the up to 8 quarters before it that end within 740 days, 4 or more needed, the deviation above 0. EPS in today's shares. Held from the session after the filing until the next quarter's filing (or 200 days).
  - `gross_profitability`, horizon 20: trailing-year gross profit over total assets at the same period end (assets above 0).
  - `earnings_yield`, horizon 20: trailing-year EPS (sum of four quarters, in today's shares) over Yahoo's split-adjusted close of the day before the decision day (no dividend adjustment).
- Order: a train-only run of each signal first (data sanity: coverage, scores per day, train IC); any rule change it prompts is an amendment committed before a full run, quoting the train numbers seen. Then full runs; confirm is read only from those.
- Caveats printed beside the harness's: train 2010-2014 misses 25-35% of member-days and XBRL starts 2009-2011 (smaller filers from mid-2011), so early train days score fewer names; a member renamed since has no CIK; share classes of one company (GOOG/GOOGL, FOX/FOXA, NWS/NWSA) carry the same fundamentals; derived fourth-quarter EPS is the year minus three quarters (share counts differ, an approximation); SUE starts once 4 earlier changes exist (about 2012 for most).
**Alternatives:** Compustat-style SUE with analyst forecasts (no free point-in-time data, spec 5); seasonal random walk with drift; latest-filed (restated) values; scores from 8-K 2.02 releases (no XBRL in most); calendar-year TTM by YTD differences (needs the same chains); a hold until the next filing without a cap (a delisted or late filer would keep an old score).

## 2026-10-01 - Fundamental signals: train-only sanity runs changed no rule; full runs all FAIL
**Context:** The pre-registration above ordered train-only runs first, a rule change after them to be an amendment.
**Decision:** No amendment. Train-only (scratch testbed, SEC facts for 608 of 609 priced members): sue IC +0.0121 t 1.12 (312 names a day), gross_profitability +0.0147 t 1.50 (169 names), earnings_yield +0.0038 t 0.41 (297 names); names per day and filings per year looked as expected. Full runs, confirm 2020+ (the verdict): sue +0.0056 t 0.37, 9 of 13 segments positive, observed power 0.0298; gross_profitability -0.0009 t -0.09, 5 of 14; earnings_yield +0.0086 t 0.87, 8 of 14, observed power 0.0197. All three FAIL; sue and earnings_yield could not have seen an IC below their observed power. Satellite weight stays 0 (spec 9).
**Alternatives:** Retuning concepts or holds on these numbers (would be tuning on results; a new idea needs a new pre-registration and only confirm days after it).

## 2026-10-01 - Fundamental signals: pre-registration correction (gate 04 r1 M1, L1), before the corrected run
**Context:** Gate 04 round 1 (PASS WITH FOLLOW-UPS). M1: the code derived a fiscal year's fourth-quarter EPS from raw first-filed figures and only then put quarters into today's shares, so a split between the third-quarter 10-Q and the 10-K (ANET FY2021, NFLX FY2025, about 15 cases) mixed old and new shares into a large false negative quarter. L1: SEC's acceptanceDateTime is real UTC (checked against filing index pages: JPM's 8-K 0001628280-26-048078 shows 06:30:38 on the page and 10:30:38Z in the JSON), not New York time as both entries above say.
**Decision:** Not a rule change: the code is brought to what the pre-registration already says. Every first-filed per-share figure is divided by the splits after its own known day before quarters are derived. Acceptance stamps are converted from UTC to New York before the known day is taken (it moved 1,855 of 57,495 stored filings one day earlier: accepted 20:00-24:00 New York; the old reading was only ever late, never early). The three signals are run again on the same scratch testbed (stored stamps converted in place, the same as a refetch would store) and both sets of numbers are recorded below; the verdicts are the corrected run's.
**Alternatives:** Keeping the first run as the verdict (it computed something other than the stated rule); refetching all of SEC for the stamps (same stored result, 45 minutes).

## 2026-10-01 - Earnings release timing: SEC's acceptance stamp is UTC (gate 04 r1 H-pre, live app)
**Context:** `edgar.parse_earnings` compared the stamp's clock with the 09:30 open as if it were New York time. A pre-market release (06:00-09:29 New York is 10:00-14:29 UTC) was marked after the open, so the event study measured from the release day's close and the regime methods' `with_reactions` took the next day's move: both missed the reaction day. Releases after 20:00 New York were dated a day late.
**Decision:** The stamp is converted from UTC to America/New_York (`edgar.new_york`, shared with the XBRL fetch) before its day and its time against the open are taken. Stored release dates (`meta` `earnings_dates:<ticker>`, kept as parsed day and before-open flag) now carry `"timing": "utc"`; a stored row without it is fetched again on the next earnings view or `oracle events <symbol>` even when under a week old. Reads that never fetch (the regime lines' `stored_releases`) keep the old rows until then. After deploy: open the earnings view (or run `oracle events`) for the watched and traded symbols to refetch their dates, then rerun any regime backtest or paper line step that should use the corrected reaction days. The live DB was not touched by this change.
**Alternatives:** Dropping old rows outright (the regime lines would lose their earnings reactions until each symbol is refetched); a migration rewriting stored rows (the clock time is not stored, so a row cannot be corrected without a fetch).

## 2026-10-01 - Fundamental signals: corrected run (gate 04 r1 M1, L1), all three still FAIL
**Context:** The correction entry above, committed before this run, same scratch testbed.
**Decision:** Confirm 2020+ (the verdict), first run then corrected: sue +0.0056 t 0.37 (9 of 13) then +0.0065 t 0.44 (9 of 13), observed power 0.0297; gross_profitability -0.0009 t -0.09 (5 of 14) both times (no per-share figure; the stamp change moved no confirm IC); earnings_yield +0.0086 t 0.87 (8 of 14) then +0.0090 t 0.91 (8 of 14), observed power 0.0197. Train (reported only), corrected: sue +0.0127 t 1.16, gross_profitability +0.0147 t 1.50, earnings_yield +0.0036 t 0.39. All three FAIL; sue and earnings_yield could not have seen an IC below their observed power. Satellite weight stays 0.
**Alternatives:** None taken; the numbers stand as run.
## 2026-10-01 - Signal testbed renames (next-signals 01b): SEC and Yahoo must agree, SEC backs the identity rule
**Context:** Gate 01 found renamed members (FB, ANTM, WLTW, SYMC...) unpriced under their old tickers, about 18% of the missing member-days, and a same-day swap rule would also map acquisitions (EA -> FERG). Its round 2 asked that the 7-day first-trade rule for reused tickers be backed by SEC, and Yahoo's firstTradeDate is sometimes plainly wrong (AVB, BBBY showed 2026-07-17).
**Decision:**
- Candidates: every former member priced on under 90% of its member-days, paired with each ticker that joined the day it left; for one Yahoo never priced or whose bars proved another company's, with no accepted swap, the companies Yahoo's search names under its old ticker (GPS finds "The Gap, Inc." via a BDR), matched to SEC's ticker map by name (at most 3).
- Accepted only when SEC and Yahoo agree, but acceptance rests on SEC: the Yahoo half passes for any long-lived listing (EA -> FERG, WBA -> IBKR and MRO -> TPL all passed it) and only rules out a new series that is missing or starts late. SEC: the new ticker's filer (CIK) has a periodic report in the 400 days before the change naming the old ticker and one after naming the new (the nearest of up to 4 each side that names any). A report names a ticker by its cover's dei:TradingSymbol (the XBRL viewer's R1.htm, a few KB) or, without one, by its document's name (fb-20211231.htm); the cover wins because names lead the change (Meta's April 2022 10-Q is meta-*.htm, its cover says FB). After an exit, the change day is the first report naming the new ticker (binary search). Yahoo: the new series has bars on 90% of the old member-days and on most of the 10 trading days each side of the swap, and the old ticker has no bars a week after it. Yahoo's firstTradeDate is not used.
- Chains resolve to the last live ticker, checked latest change first (NLOK -> GEN, then SYMC -> NLOK against GEN's filer). An old ticker's member-days its own bars lack are priced from the new ticker's bars, relabelled, up to its last member day plus the 120-day tail (as a former member's own bars), through `Testbed.bars` and `bars_between`, so the signal harness needs no change.
- Rejected one-for-one swaps and rejected name matches are kept in `tb_aliases` (accepted 0) for review on the card and in `oracle testbed aliases`; never applied. Rejected pairs from multi-change days are not kept (most are unrelated companies).
- Identity: for a former member with bars, or noted reused by the first-trade rule, SEC decides when it can: the ticker's filer today first filed over 30 days after the member joined and Yahoo's own series under the ticker starts late too (a new holding company such as Perrigo plc in 2013 keeps both, so it is judged by its reports), or its cover named another ticker while a member, means another company (bars dropped, noted reused); its report then naming the ticker means the same company (the first-trade rule is overridden and the bars fetched again). A document name alone, or no SEC filer, leaves the first-trade rule. Current members are never judged. The identity check runs before the renames, so a ticker it drops (BBT, now Beacon Financial) is looked for as renamed (BBT -> TFC).
- Same company twice: members with one SEC filer (through today's map, an accepted alias or the identity check) on the same days are flagged with the known pairs, except two share classes both listed today (GOOG, GOOGL). `Testbed.duplicates()` is the one list (known pairs, then found ones, earlier listing first) the signal harness drops duplicates by, so DISCA and DISCK, both priced from WBD's series, never stand twice in a day's cross-section.
- The check needs `[research] contact`; asked for (`--verify`, Check renames) without one, the run fails (the CLI exits 1). A former member noted reused by the first-trade rule that SEC has no filer for is noted "not on Yahoo (delisted ...)" instead. The research migration is t0004_aliases, appended after t0003_xbrl (migrations are positional).
- The check runs once after the first full build, and again with `verify` (CLI `oracle testbed aliases --verify`, card "Check renames"). It needs `[research] contact`; without one it says so and maps nothing. Coverage shows the missing share before and after the map; the under-priced list uses the mapped count.
**Alternatives:** A same-day swap rule (maps acquisitions); the old ticker's filer (SEC's map has only current tickers); XBRL instances for dei:TradingSymbol (megabytes each, the cover page has it); Wikipedia's changes table (no longer in the page the app reads; supporting only anyway); a hand-made map (unverifiable, misses new renames); judging current members by SEC (the source carries some under today's ticker, so their old covers name the old ticker).

## 2026-10-01 - Insider buys and typed-event studies (next-signals 05): pre-registered before any real-data run
**Context:** Spec decisions 5, 6, 8. Written and committed before any Form 4 or 8-K item is fetched for the testbed or anything below is run on real data; nothing below is chosen after seeing a result. A change is a new entry, committed before its run.
**Decision:**
- Data, research DB only, for the testbed's priced members with a CIK (ticket 04's map). Trades: SEC's Insider Transactions Data Sets (quarterly, 2009q1 to the latest published quarter), original Form 4s only (document type "4"; a 4/A would count a corrected trade twice), issuer CIK in the map, non-derivative rows with code P (acquired) or S (disposed), shares and price above 0; value = shares x price. The filer is the filing's first reporting owner (a joint filing counts once), an insider when its relationship names Director or Officer (a 10% owner or "other" alone is not). Filings: each issuer's submissions (recent list and older pages back to 2009): Form 4 and 8-K acceptance times and 8-K items. Known day = acceptance day in New York (UTC stamp converted, `edgar.new_york`), else the filing date; used from the next session (decision days after it), as tickets 02/04. The data sets carry no acceptance time; a Form 4 missing from the submissions uses its filing date (never early). The trade data ends with the latest published quarter: a decision day after that quarter's last day gets no insider score and no cluster event (no zeros from missing data).
- Signal `insider_buys`, horizons 20 and 60 (PASS only when both pass): on decision day d, the number of distinct insiders (owner CIKs) with at least one open-market purchase (P) in a Form 4 known on a day from d - 90 to d - 1 (calendar days). Purchases only (Lakonishok-Lee, Cohen-Malloy-Pomorski: sales are mostly diversification and pay; sales are stored and shown, never scored). Every member with a CIK scores, 0 without buys; a member without a CIK is unscored. Harness exactly as "Signal test (next-signals 02)" with ticket 04's duplicate rule (keep the listing with a bar). Ties: most names score 0 on most days; the harness's Spearman gives tied names their average rank, so a day's IC is close to the rank-biserial correlation of "has buyers" with the excess return, bounded well below 1 when few names buy, and a day where every name ties has no IC and is not counted. The t and observed power come from the same daily ICs, so the bar is unchanged; an effect concentrated in few names shows as a small IC.
- Event studies, four types: 8-K item 2.02 (results), 1.01 (material agreement), 2.01 (acquisition or disposal of assets) from original 8-Ks (not 8-K/A), one event per company, type and known day; and the insider-buy cluster: a known day on which 3 or more distinct insiders have a P purchase known in the 30 calendar days ending that day, at most one cluster per company in any 30 days (a later one needs 30 days since the last). Entry at the open of the first session after the known day (the decision day); the company must be a member that day (duplicate rule as above). CAR at h = 1, 5, 20, 60 trading days: the harness's sector-excess return from that open to the open h days later (same return data rule, same sector benchmark over that day's members). Windows: train = decision days 2010-01-01 to 2019-12-31 with the exit inside it; confirm = 2020-01-01 to the run day, same rule. Mean CAR; t = mean over its standard error clustered by decision day (G/(G-1) small-sample factor). Primary horizon 20 for every type. A type PASSES only when its confirm mean CAR at 20 d has t above 2 in the same sign as its train mean CAR at 20 d (train decides the sign only). Four types, about 4 x 5% chance one passes by luck. Clustering by day does not cover windows overlapping across nearby days (20 and 60 d), so t is optimistic there; printed with the result.
- Order: a train-only run of insider_buys and of the event study first (data sanity only); any rule change is an amendment committed before a full run, quoting the train numbers. Then full runs; confirm is read only from those.
- Caveats: renamed members have no CIK (ticket 01b); share classes with one CIK carry the same score and events; the data sets list a filing in the quarter of its filing date; officer and director from the filing's own relationship flags.
**Alternatives:** Net value (purchases minus sales) or purchase value over market cap (market cap needs point-in-time shares outstanding; value is dominated by a few large buyers); per-filing Form 4 XML (about a million requests); 8-K timing at the release time for pre-market 2.02s (the harness decides at the next session's open; a same-day open entry would need an intraday rule); clusters by trade date (not known then).

## 2026-10-01 - Insider buys and typed events: train-only sanity runs changed no rule
**Context:** The pre-registration above ordered train-only runs first; a rule change after them would be an amendment.
**Decision:** No amendment; full runs next. Data (scratch testbed): 605 issuers' submissions (every Form 4 trade's accession found there with an acceptance time), 70 quarters of data sets 2009q1-2026q2, 418,376 P and S rows (about 1,100-2,800 purchase rows a year by 180-270 issuers). Train only: insider_buys h20 IC +0.0036 t 0.97 (370 names a day, observed power 0.0073), h60 +0.0080 t 1.16 (observed power 0.0138). Event study train, mean CAR at 20 d: 2.02 -0.01% t -0.27 (15,400 events), 1.01 +0.06% t 0.75 (4,851), 2.01 +0.67% t 2.71 (568), insider cluster -0.21% t -0.66 (311). Event counts per year looked as expected.
**Alternatives:** None taken.

## 2026-10-01 - Insider buys and typed events: full runs, everything FAILS
**Context:** Full runs after the no-amendment entry above, same scratch testbed (missing share train 26.1%, confirm 6.5%; insider trades through 2026-06-30).
**Decision:** Recorded as run. insider_buys confirm (the verdict): h20 IC -0.0019 t -0.32 (5 of 13 segments positive, 470 names a day, observed power 0.0117), h60 -0.0032 t -0.45 (4 of 13, observed power 0.0145): FAIL. Event study, confirm mean CAR at 20 d (primary) against the train sign: 2.02 -0.08% t -1.23 (13,239 events; train -0.01%), 1.01 -0.36% t -2.85 (4,139; train +0.06%, opposite sign), 2.01 -0.22% t -0.42 (289; train +0.67% t 2.71), insider cluster +0.18% t 0.37 (209; train -0.21%): all four FAIL. Seen but not a verdict: 1.01 confirm is negative at every horizon (1 d t -3.31) and 2.02 at 1 and 5 d (t -2.65, -2.31) against flat train; a new idea from these would need its own pre-registration and only confirm days after it. Satellite weight stays 0 (spec 9).
**Alternatives:** None taken; the numbers stand as run.

## 2026-10-01 - Insider buys: correction before a rerun (gate 05 r1 M1, M2, L1)
**Context:** Gate 05 round 1 (PASS WITH FOLLOW-UPS). M2: every priced member has a CIK in today's ticker map, but 49 have one that misses part of their history (a holding company that reorganised under a new CIK: GOOGL before 2015, DIS before 2019, XOM's CIK 2115436 with no Form 4 at all; or a ticker now another company's: S, SE, SNDK, CEG...). They scored 0, read as "no insider buys", where the pre-registration means "unscored" for a member whose insiders the data cannot see; the caveat "renamed members have no CIK" was wrong. M1: a data-set quarter fetched for a smaller issuer set (a smoke run, a grown testbed) was never fetched again, so its missing issuers would score 0. L1: `sec_filings` keyed on accession alone kept a filing listed under two member CIKs (an owner company's Form 4 about another member) under one only, so the other fell back to the filing date. The gate's independent recompute with the M2 rule gave confirm h20 IC -0.0019 t -0.32, h60 -0.0031 t -0.44.
**Decision:** Not a new rule: the code is brought to what the pre-registration meant. A member scores on a day only when its CIK's insider data covers the whole 90-day window: the CIK's first stored Form 4 was known on or before the window's first day, and every data-set quarter the window touches was fetched with that CIK in scope; otherwise it is unscored (no 0). Each quarter stores the CIKs it was filtered to; a quarter lacking a CIK now in scope is fetched again, and a refetch keeps the stored ones (a smoke run never shrinks it). `sec_filings` is keyed on (CIK, accession). The coverage lists members whose CIK's Form 4s start over a year after they were first a member from 2010. Events are unchanged in rule (they only lose events for those names). The data are fetched again into a scratch research DB migrated in the final order, both runs repeated, and both sets of numbers recorded; the verdicts are the rerun's.
**Alternatives:** Keeping the recorded runs as the verdict (they scored names the rule meant to leave out); mapping predecessor CIKs now (ticket 01b's alias table, not merged yet).

## 2026-10-01 - Insider buys and typed events: rerun after gate 05 r1, everything still FAILS
**Context:** The correction entry above, committed before this run. A new run, not a like-for-like correction: the scratch research DB is ticket 01b's merged one in the final migration order (t0004_aliases with its 27 verified renames, then t0005_insider), with ticket 04's CIK map copied in and the Form 4s and 8-Ks fetched again (607 priced members, 603 issuers, 70 quarters, 417,735 trades; 27 members whose CIK starts late, listed by `testbed insiders-status`). Missing share train 22.4% (was 26.1%), confirm 5.0% (was 6.5%).
**Decision:** First run, then rerun. insider_buys confirm: h20 IC -0.0019 t -0.32 then -0.0011 t -0.20 (5 of 13 segments, observed power 0.0116); h60 -0.0032 t -0.45 then -0.0023 t -0.32 (5 of 13, observed power 0.0142): FAIL. Train (reported only): h20 +0.0036 t 0.97 then +0.0029 t 0.78; h60 +0.0080 t 1.16 then +0.0068 t 0.96. Event study, confirm mean CAR at 20 d (train in brackets): 2.02 -0.08% t -1.23 then -0.08% t -1.30 (-0.01%); 1.01 -0.36% t -2.85 then -0.28% t -2.21 (+0.07%, opposite sign); 2.01 -0.22% t -0.42 then +0.08% t 0.16 (+0.69% t 2.78); insider cluster +0.18% t 0.37 both times (-0.21%). All four FAIL. Satellite weight stays 0.
**Alternatives:** None taken; the numbers stand as run.

## 2026-10-01 - Next stream: optimise the boring plan before more alpha search
**Context:** Every pre-registered stock signal FAILED on confirm, and the S&P 500 testbed can only see IC ~0.03 at 20 d, above most published effects. Grilled with the user (three rounds, every recommendation accepted).
**Decision:** The real-money plan is index buy-and-hold, started once this stream finishes whatever the alpha search says (UK, Stocks and Shares ISA, flat-fee platform past ~GBP 50k, monthly contributions, 10+ years). Build: total return in every held line and dividends credited in paper ledgers; a GBP fund comparison after TER (all-world, developed world, S&P 500, UK home-bias mix; US-listed proxies with dividends converted at daily GBPUSD, real UCITS fund beside its proxy); user-entered platform fees with the flat-vs-percentage crossover; lump vs drip, rebalancing and cash drag; one pre-registered trend overlay; a Plan tab with CLI twins; resizable compare charts with per-line toggles. Alpha search resumes only after this is proven; international testbed (08) stays parked. Builds are fine; no long live jobs (reader, backfills, replay study, testbed rebuilds) until the user says. Holiday disarmed and reader job 732 cancelled (resumable) the same day.
**Alternatives:** Keep searching for stock signals first (08 international, composites of weak signals, ML on the same inputs): each stays under the detection limit and adds forking paths. A named-platform fee table (goes stale).

## 2026-10-01 - Trend overlay: the one rule, fixed before any data is read
**Context:** In an ISA a switch to cash costs no tax, so a timing rule is worth one honest test. It is a risk test, not stock picking, so the IC power limit does not apply.
**Decision:** At each month end, compare the total-return series' close with its 10-month simple moving average of month-end closes: above or equal, hold the index; below, hold cash from the next session's open. Switching cost charged on each switch (the backtest's cost model). Cash earns 0 unless a stored T-bill series exists (the result says which). Series: S&P 500 total return (^SP500TR). Choose window 1988-01-01..2007-12-31 (reported only), confirm 2008-01-01..run day. PASS on confirm only when both hold: worst fall at most two thirds of buy-and-hold's, and yearly return (CAGR) at most 1 percentage point below buy-and-hold's. No parameter is tuned; the GBP ACWI-proxy line is shown beside, never judged. Ticket 04 may commit wording clarifications before its first real run, never a rule change.
**Alternatives:** 12-month time-series momentum (similar, chose one); several windows (forking paths).

## 2026-10-01 - Forward-only trackers for the near misses
**Context:** 8-K item 1.01 showed a negative confirm drift (20 d -0.28% t -2.21) opposite to train; momentum_12_1, reversal_1m, sue and earnings_yield had positive confirm ICs under the window's observed power ("could not see").
**Decision:** All five frozen exactly as pre-registered (1.01: 20 d CAR vs sector, expected sign negative; the four signals at their registered horizons). Scored only on decision days after 2026-10-01; judged on 2027-10-01 with the same PASS rules. Nothing built now; days before 2026-10-02 never count.
**Alternatives:** Drop them (free to keep); a new test on past days (already seen, would be fishing).

## 2026-10-02 - Trend overlay: wording clarifications, before its first real run
**Context:** The 2026-10-01 pre-registration fixes the rule; building it needs a few readings it leaves open. Fixed here before any rule result was computed (only ^SP500TR's open/close quality was looked at: Yahoo's opens equal the close on most sessions up to 2021-01-06, nearly all before 2009-03).
**Decision:** A month end is a month's last stored session, known once a later month has one (the run day's unfinished month is no month end). Each window starts at its first session's close at 1.0; its first state is the call of the last month end before it, taken at no cost, and the choose window reads no session after 2007-12-31. Before the rule has 10 month ends it holds the index. Switching cost: the backtest's slippage, 0.1% of the value per switch; no dealing or platform fee (the user's own, ticket 03). A session with no real open (open missing, or equal to the close as Yahoo stores ^SP500TR on 5380 sessions up to 2021-01-06) fills the switch at that session's close: a day late, never earlier than the data allows. Yearly return is CAGR over calendar days; worst fall is the daily-close peak-to-trough. Cash earns ^IRX (13-week T-bill) only if already stored in the research database; the test never fetches it. Per-crash spans fixed now as calendar 2008-01-01..2009-12-31, 2020 and 2022, each from the close before the span (or the window's first close when the span starts with the window: 2008 in confirm), so the way back counts. Also after 281 sessions (2009-03-30 to 2015-05-21) whose open equals the previous close (a stale open), a switch fills at that previous close; the result notes count them (gate 04 r1 wording fixes, no rule change). Added after the first real run (data hygiene, no rule change, verdict unchanged): the run day's session is left out, since Yahoo serves it mid-session, and each update refetches the last stored day so a part day is replaced.
**Alternatives:** Fill a fake-open switch at the month-end close (the decision price, slightly optimistic); crash spans from each peak to trough (picked with hindsight); fetching ^IRX for cash (changes the stated default).

## 2026-10-02 - Trend overlay home: a research-DB job, `plan trend`, a card on Ledgers
**Context:** Ticket 04 needs a CLI, a card and a seam for the GBP ACWI proxy (ticket 02) and the shared total-return loader (ticket 01).
**Decision:** ^SP500TR bars go in the research database's bars table; `trend_sessions()` is the one place the series is read, so ticket 01's loader can replace it. The test is a `plan_trend` job (fetch newest days, run, keep; retryable) kept beside signal tests under key `trend_overlay`; `GET /api/plan/trend` reads it. CLI `plan trend` (run) and `plan trend --show`. The card sits on the Ledgers tab until ticket 05's Plan tab. Other series go in the result's `beside` windows, shown never judged.
**Alternatives:** Computing on every page load (needs the network for fresh days); storing the series in the live database (the plan keeps research data apart).

## 2026-10-02 - Next ideas: ai-track, stricter PASS bar, boring-plan additions

**Context:** Every pre-registered stock signal and every AI signal so far failed. A 185-method
research archive (`.scratch/next-ideas/research-archive.md`) rated each by its chance of real benefit;
only wrapper, fee, fund and behaviour items rated 7+. The user believes AI could work if tested fairly.
**Decision:** (1) New ai-track stream: ChronoBERT (yearly-cutoff models, no look-ahead) embeddings of
10-K/10-Q sections and 8-K 2.02 press releases, ridge fitted on 2010-2019, confirmed 2020+; it must add
information beyond Lazy Prices and a ~30-characteristic library, which also gets its own ML verdict
(ridge + LightGBM). (2) PASS bar for tests registered from ai-track 01 on: t >= max(3.0, two-sided
Bonferroni 5% over all registered tests); a scoreboard lists every test; deflated Sharpe on backtest
lines; Open Source Asset Pricing triage before any new signal build. Earlier tests, including
boring-plan 04, keep the rules they were judged by. (3) Data-error catcher (flags only). (4) The 32B
headline reader is dropped. (5) Boring plan gains a guardrails panel (drawdown preview, no-sell rule,
7-day cool-off) and an ISA vs Nest pension vs LISA comparator (LISA off by default).
**Alternatives:** stop searching for alpha entirely (kept as the real-money plan regardless); finish
the 32B reader (rejected: cannot see large-cap effects in 16 post-cutoff months); prompted LLM
verdicts (already failed); XGBoost on the old weak features (replaced by the characteristic library).

## 2026-10-02 - Shared compare chart: resizable, per-line toggles (ticket 06)
**Context:** Spec 7: the shared compare chart (`oracle/gui/static/lib/compare.js`, used by Ledgers, the
Backtest card and the Replay account card) goes full width above its table, resizable, with per-line
toggle chips, height and hidden set remembered per chart in localStorage.
**Decision:** Toggling a line adds or removes its series on the one chart instance the component already
owns (`chart.addLineSeries`/`removeSeries`), instead of destroying and recreating the chart; this is what
makes "rescale y to visible lines" free (the library autoscales to whatever series remain) and keeps the
chart's identity stable across clicks. `compareLines()` no longer hands its chart object out: `draw()`
mounts/updates it internally, and callers call `destroy()` (not `chart.remove()`) when the card's node is
about to be replaced next redraw. All three callers (ledgers.js, backtest.js, replay.js) pass their own
storage key (`oracle.compare.<ledgers|backtest.<id>|replay>`) so unrelated charts never share height/hidden
state.
**Alternatives:** Returning the live chart from `draw()` for the caller to `.remove()` itself, as before
(rejected: an internal toggle-driven redraw could leave that reference stale, risking a double `.remove()`
on the next generation's cleanup).


## 2026-10-02 - Data-error checks keep their newest run in meta, no migration
**Context:** ai-track 03 needs the checks' results stored for the Data health card and `oracle data show`;
m0050 is taken by an unmerged branch.
**Decision:** The newest run is one JSON value under `meta.data_checks_last_run` in the live database: counts
per check in full, flagged rows capped at 500 per check. Each run replaces the last. Market data in either
database is only read.
**Alternatives:** An m0051 table of runs and flags (rejected for now: history of flags has no reader yet and
would collide with the m0050 numbering); storing in the research database (rejected: the live checks would
then need it to exist).

## 2026-10-02 - What each data check counts as an error
**Context:** The checks must flag real errors in Yahoo-sourced bars without drowning them in normal market
behaviour; tuned on a copy of the live databases.
**Decision:** Split-sized jump: a day-to-day close ratio within 3% of 2, 3, 4, 5, 8, 10, 20, 50 or 100 (or
the inverse), bars at most 10 days apart, no `tb_splits` row within 5 days. Not 3:2: every 1.5x or 0.67x
day on the live copy was real news. Repeated bars: 5 or more in a row with identical OHLC. Stale: a watched
listing, benchmark or current testbed member whose last bar is over 7 days behind the newest. Dividends: a
step in adj_close/close over 1e-5 is an ex-dividend day (NVDA's $0.01 is under 1e-4); a series with 4 or more
steps is a payer; a gap over 400 days between steps (or to the last bar) is flagged, and a live series with
adj_close == close on 250+ bars while the research copy shows 2+ steps in its span. Research series are
checked only inside the windows the testbed reads (member spans plus the 120-day tail, a rename's new ticker
up to priced_to, benchmarks whole); former members are judged by ticker alone, since today's name on a
reused ticker is a later holder's. Duplicate share class: one SEC CIK (or a known pair) as members on the
same days; watchlists holding two listings of one company_key.
**Alternatives:** Checking every stored research bar (rejected: 2,400 flags, nearly all reused tickers and
illiquid swap candidates no test reads); a dividend data source to know payers (none stored; a suspension
reads as a gap, noted as the main false positive).

## 2026-10-02 - Data checks run nightly through a scheduler hook; explanations Ollama only
**Context:** The ticket wants the checks nightly after the tick and on demand, with an optional local-LLM
explanation per flag off by default (spec decisions 2 and 11).
**Decision:** `Scheduler(after_tick=...)` is called once each scheduled tick has run or been recorded
skipped; the GUI app passes `nightly_data_check`, which submits an automatic `data_check` job unless
`[data_checks] nightly` or automation is off. `[data_checks] explain` (default false) asks the
`explain_model` profile (default the research slot's) for one paragraph on the first 25 flags a run, a
check at a time; a profile whose provider is not `ollama` is refused with a note, never called.
**Alternatives:** Running the checks inside the tick's own stages (rejected: a slow check would hold the
tick's lock and its banner); explaining every flag (rejected: hundreds of model calls a night).

## 2026-10-02 - Total return from a stored dividend ledger, not adjusted closes (boring-plan 01)
**Context:** Spec 1 asks every buy-and-hold, index and benchmark line to be total return and the paper ledgers to credit dividends on the pay date. Stored `adj_close` cannot carry it: each price update refetches only the last 7 days, so a dividend's adjustment lands on the refetched bars and never on the older stored ones, and the step between them hides the dividend (the stored series loses every dividend after a listing's first fetch). Yahoo's chart gives each dividend's ex-date and amount but no pay date. Nasdaq's quote API gives pay dates, but only for Nasdaq-listed symbols (NYSE ones answer "not available"), and nasdaq.com/legal forbids automated capture of its content for data analysis software without written permission, so it is not used (gate 01 r1 M1, M2).
**Decision:**
- Dividends are stored per listing and ex-date (`dividends`, migration m0050) from the same Yahoo chart call as the bars (`events=div`, no extra request), with the span of days asked (`dividend_spans`). A fetch is the whole truth for its window: stored dividends with an ex-date after the window's first day that it no longer lists (moved or cancelled) are dropped. A listing whose span stops short of its first stored bar (every listing priced before this, a refetch from a later day) is asked once from that bar (a small monthly events-only call); when another source served the bars, only the days since the last ask. Pence lines are stored in pounds. Yahoo's London fund dividends are in the quoted currency (VWRL.L, VUSA.L yearly sums match justETF's sterling distributions).
- One seam, `dividends_service.reinvested` / `total_return_closes` / `payouts` over `broker.total_return` and `broker.dividends`. Held lines (ACWI and SPY in backtest, walk-forward and replay study; buy and hold of today's list everywhere; the Ledgers benchmarks) reinvest each dividend at its ex-date's close (the next close if the ex-date has none). A listing whose span does not cover the series is price only and every label says so ("total return" / "price only" / "partly price only").
- Traded lines (backtest and walk-forward: strategy, momentum, the regime lines, core-sat's core and satellite) are paid dividends as cash by `broker.dividends.credits`, the rule the paper ledgers use: for the shares held at the close before the ex-date, on the pay day, at that day's rate (less the paper FX spread where the line pays paper costs). From the ex-date to the pay day the dividend is a receivable (`DividendBook.owed`): counted in equity at the latest rate, not spendable, so a pay day after a window's end still counts in its last equity (gate 01 r2 N1: valuing it at zero until paid cut core-sat's walk-forward margin over buy and hold from +12.2% to +1.3%). The replay study's traded lines already run on adjusted closes from a whole-window refetch, which carry dividends, so they are left as they were; its buy and hold uses plain closes with the stored dividends.
- Pay day: the stored pay date when a source gives one (none does today), else an assumed lag after the ex-date by the dividend's currency, a typical upper bound so a credit is late rather than early: USD and CAD 30 days, GBP, EUR, CHF and AUD 45, JPY 95, KRW 130, others 90. Never before the ex-date.
- Paper ledgers: each acting account is credited per order source (a sleeve, or the account), as cash at the pay day's rate less the FX spread, gross of withholding tax. A credit waits while no rate is stored for its pay day; frozen or switched-off accounts are not credited. Recorded equity includes the receivable (dividends gone ex, not yet credited, at today's rate less the spread), stored with the holdings; the ledger card shows it as Dividends owed. Credits show in the ledger card (Dividends total and table, assumed pay days marked) and `oracle paper show`.
- A Ledgers benchmark is labelled with its own listing's name, not its company's (a fund's share classes share a company, and VWRL.L read as VWRP.L's "USD Accumulation").
**Alternatives:** Adjusted closes from a full refetch of every listing on each update (heavy, and still no pay dates); Nasdaq's API for pay dates (terms, and Nasdaq-listed only); EODHD dividends (paid key, not configured); one flat lag for every market (credits US dividends two months late); crediting on the ex-date (cash before it is paid); traded lines price only beside total-return held lines (biased every verdict against the traded line, gate 01 r1 H1).

## 2026-10-03 - Fund comparison: proxies modelled as the UCITS fund, in the research DB (boring-plan 02)
**Context:** Spec 2 compares all-world, developed world, S&P 500 and a UK home-bias mix as sterling accumulating funds after TER over long histories, which only US-listed proxies and ^SP500TR give; the real UCITS lines start 2009-2019.
**Decision:**
- Funds and proxies: all-world = ACWI modelled as VWRP (0.19%); developed = URTH as SWDA (0.20%); S&P 500 = ^SP500TR as VUAG (0.07%); UK = EWU (MSCI UK, standing in for the FTSE All-Share, which has no free total-return series) as VUKG (FTSE 100, 0.09%); UK home-bias mix = 75% all-world + 25% UK, rebalanced on each year's first shared day without cost. The UK part is shown, dimmed, as part of the mix.
- Each proxy: closes in GBP at that day's stored GBPUSD, dividends reinvested through the ticket 01 seam (`dividends_service.reinvested`; ^SP500TR already includes them), the proxy's own fee added back and the fund's TER taken over calendar days: value x ((1 - TER) / (1 - proxy fee)) ^ (days / 365.25). TER drag = return before any charge minus after, over the shared window.
- Table over the longest shared history (URTH's first day, 2012-01-12) plus each fund's own longest GBP history (FX from 2003-12 on Yahoo). Every line labelled proxy. Tracking: each real fund against its modelled proxy from 90 days after its first stored day (Yahoo's launch prints are placeholders and stale zero-volume closes), on the real fund's days.
- Data in the research DB only: it gains the live schema's FX and dividend tables (t0006; the paper ledger table stays live only). A symbol whose stored bars were never asked for dividends from their first day is refetched whole once. Result kept in `signal_tests` as `fund_comparison`; the Signals list now skips that key and `trend_overlay` (keeping the trend test broke `signal_tests()` on integration).
- Reusable seam for 03/09: `fund_series(ctx, key, monthly=False) -> FundSeries`.
- Home: `plan_funds` job (retryable), CLI `oracle plan funds` / `--show`, `GET /api/plan/funds`, a self-contained card (`lib/funds.js`) on the Ledgers tab until the Plan tab (05).
**Alternatives:** Real UCITS lines only (5-7 years, too short); proxies without fee adjustment (double-counts the proxy's own fee); MSCI or FTSE index levels (no free daily total-return GBP series); daily-rebalanced mix (unrealistic); keeping the launch days in tracking (VUAG read -4.2 points a year off from two placeholder prints).
## 2026-10-03 - Fees, contributions and rebalancing: one projection, month-end steps (boring-plan 03)
**Context:** Spec 3-4 and the coordinator's scope add: one reusable contribution and fee projection (schedule, fee model and growth series in, pot path out, with a hook for extra inflows) that ticket 09 calls unchanged, plus lump vs drip, monthly contributions, two-fund rebalancing and cash drag on the total-return series. The user stores no amounts; fund comparison (02) is not merged yet.
**Decision:**
- Pure `oracle/broker/projection.py`: `project(growth, contributions, fees=NO_FEES, extra=None, start=0.0) -> list[Month]` (per month: day, contributed, extra, fees, value). Each month the contribution and `extra(i, own)` arrive at the month's first close less buying costs (dealing per buy, FX share of the amount), grow to the next close, then the platform fee comes off the month-end value (a twelfth of the yearly percentage, capped at a twelfth of the yearly cap, plus the flat fee). The services re-export it (`fees_service.project`, `user_fees`).
- `[plan]` settings hold rates only: `platform` ("percentage" or "flat", which one the user pays), `percent_fee` (% a year), `percent_cap` (GBP a year, 0 none), `flat_fee` (GBP a month), `dealing_fee` (GBP per trade), `fx_fee` (% of each buy). All default 0 (nothing assumed); live, edited in the card's Platform form and the Settings form. The crossover is `12 x flat / percent`, None when the cap is at or below the flat fee; dealing and FX are shared by both platforms so they cancel.
- Fee drag over a horizon uses an assumed steady growth rate (default 5% a year, said as an assumption), not history: the past rate says nothing reliable about the next decades and fee drag scales with it.
- Contribution tests run on month-end closes of ACWI's total return in GBP from the 01 seam (`total_return_closes`), labelled proxy; the growth series is a parameter so 02's fund series plug in. Lump vs drip: from every start month with 12 after it, both valued at the drip's end (the lump's lead is fixed after that); the drip's waiting cash earns `cash_rate` and pays no platform fee. Monthly contributions: every `years` window. Rebalancing: money in split by target weights; yearly at every twelfth month end, bands when the first fund's weight is more than the points off; a rebalance pays dealing on two trades and FX on the moved amount, no tax (ISA); worst fall on the time-weighted index. Cash drag: a share kept in cash, topped back up each month end.
- The default second fund is CUKX.L (accumulating FTSE 100, GBP) for a 75/25 home-bias mix: Yahoo's dividend list for the distributing ISF.L starts in 2013, so its 2009-2012 total return would be price only.
- CLI in `oracle/cli/plan_money.py` (`oracle plan fees|contributions`) registered onto the `plan` group with one line, routes in `oracle/gui/routes/plan_money.py`, card on the Ledgers tab until the Plan tab (05) takes it.
**Alternatives:** Daily steps (no gain for monthly habits, 20x the work); the history's own CAGR for the fee horizon (ACWI-GBP 2008-2026 ran about 11% a year, which would overstate fee drag in pounds); contributions directed to the underweight fund (a real habit, left as a follow-up rule); named-platform presets (spec: they go stale).

## 2026-10-03 - Contribution tests read fund comparison's series; the mix's second fund is its UK part (boring-plan 03, gate r1)
**Context:** Gate 03 r1 H1: the tests read ACWI from the live DB, where the ordinary price fetch keeps about five years and GBP/USD starts 2021, so the app gave 49 starts, not "every start month in history". Fund comparison (02) now keeps each fund's GBP, accumulating, after-TER series from long-history proxies in the research DB (`fund_series`), fetched by its own job.
**Decision:**
- `contribution_tests` reads `fund_series(ctx, key, monthly=True)` (default `all_world`, any 02 key via `--fund`), cut to whole months (`broker.contributions.whole_months`: a first day inside its month and the running month are dropped). Fund TER is taken there, once; platform fees stay ours. One `month_ends` (02's `broker.fund_compare`); `growth_series` and `projection.month_ends` are gone. No history: NoPrices naming the job (run the fund comparison, Compare funds). CLI, API and card show the whole-month span, start months and windows.
- The mix's second fund is 02's `uk` (EWU proxy, modelled as VUKG, 0.09% TER), not CUKX.L: the same modelling and TER handling as the first fund, 02's own home-bias mix (75/25), history from the proxies' shared start, and no live-DB listing to price.
- Gate r1 M1: a buying cost above the money it is charged on takes all of it and no more, a rebalance cost at most the pot, so the pot never goes below 0 and a fee never pays back; a lump of 0 or less, a cash rate of -100% or less, and fees that take a whole drip tranche are refused (400 in the GUI).
- Gate r1 L2: the card's Platform form saves the six `[plan]` keys in one checked write (`PUT /api/plan/platform`, `settings_service.set_plan`): all or none.
**Alternatives:** A deep fetch of ACWI and GBP/USD into the live DB (duplicates 02's job and its research-DB rule); CUKX.L as the second fund (2010 on, live DB, a different TER basis than the first fund).

## 2026-10-03 - Plan tab, the "your plan" summary and its default fund rule (boring-plan 05)
**Context:** Spec 6 asks for a Plan tab with a summary (fund, wrapper, platform type, monthly, overlay yes/no); spec 2 says "default pick all-world unless the numbers say otherwise". 02's real run has the S&P 500 ahead of all-world since 2012 on both yearly return (+15.96% vs +12.68%) and worst fall (-26.1% vs -27.3%), so a plain "best numbers" rule would pick a 100% US fund on one decade.
**Decision:**
- Default fund: all-world unless a fund no more US-heavy than all-world beats it on both yearly return and worst fall over 02's shared history. A more concentrated fund's lead is shown in the reason, not followed. The user's pick (`[plan] fund`, "" the default) wins and the reason still states the numbers' default.
- `[plan]` gains `fund` and `monthly` (GBP a month, 0 not set). The spec's "no amounts stored" now means the pot: the ticket asks for a monthly setting, and only the user's own oracle.toml holds it.
- Wrapper is fixed to Stocks and Shares ISA (spec situation); its reason gives the yearly share of the £20,000 allowance. Pension and LISA are ticket 09's comparison.
- Overlay yes only when 04's kept test passed; no result is "No (not tested yet)".
- What is not known yet is a `missing` list naming the card's button (and, in the CLI, the command), never an error.
- `oracle plan set` takes every `[plan]` key; `settings_service.set_plan` now checks the given keys over the current plan, so a partial write keeps the rest (gate 03 L5). `PUT /api/plan/choice` saves fund and monthly; the platform form keeps `PUT /api/plan/platform`.
- The three 02/03/04 cards move off the Ledgers tab to the Plan tab, under the summary, which reloads when a card's job finishes or the platform is saved. Their jobs link to `#plan`. 08 and 09 add their panels to the tab's section list.
**Alternatives:** Best risk-adjusted fund as the default (picks the S&P 500 on 2012-2026); a stored wrapper setting (nothing to choose until 09); a separate settings route for fund and monthly through the generic settings form (no validation of the fund key).

## 2026-10-03 - research-lab: Claude as a junior researcher, the user steers
**Context:** The user wants an ongoing programme that finds trading methods of every kind (not only AI), tests each honestly on their system, keeps a results table, and later fuses the most promising to look for an edge. Grilled 2026-10-03 (Q1-Q32); full record in `.scratch/research-lab/spec.md`.
**Decision:**
- One programme: absorbs the 185-method research archive and ai-track (ai-track 01 and 02 become research-lab 01 and 05; ai-track 04-09 become method entries; 03 stays done).
- Scope: anything testable on free daily data; the rest is logged "not testable here". Practitioner sources allowed with a quality tag. Paywalls: legal open access (Unpaywall, Semantic Scholar), the user's inbox, else abstract-only; no shadow libraries.
- Funnel: literature/OSAP, power, build, train 2010-2019, confirm 2020+. Three test types (signal IC, strategy vs buy-and-hold after UK retail costs with deflated Sharpe, event study). PASS bar t >= max(3, Bonferroni over every registered test; each horizon counts).
- Pre-registration from a fixed template, written by the autopilot, checked by a blind gate, committed before any data run; near-duplicates of a FAIL need an accepted "different because". `autonomy: propose-only` in steer.md (user marks go/drop/dig-deeper); `self-directed` later.
- Literature index (sources, methods, searches) in research.db with exact, fuzzy and local-embedding matching; service, CLI, thin MCP, panel search.
- Fusion: up to 5 "promising" methods (right sign train and confirm, confirm t > 1.5), fixed combining menu fitted on train only, judged forward-only for >= 12 months, at most 3 live; first round at the end of month 1.
- Budget: all autopilot work <= 85% of each 5-hour window and <= 70% of the week, paced; heavy compute 01:00-08:00; the user's own tasks first. Weekly private report page with go/drop buttons; a report session on request; push on any PASS.
**Alternatives:** A separate stream beside ai-track (duplicate tables and multiple-testing counts); user approval of every pre-registration (too slow for a month-long run); fusion judged on the confirm period (selection bias, every test has seen it); a carved final holdout now (weakens every single-method test); a test-count cap (the user prefers usage caps); shadow-library papers (declined).

## 2026-10-03 - Guardrails: cool-off scope, drawdown windows and stand-in (boring-plan 08)
**Context:** Ticket 08 asks for a drawdown preview on the total-return series in GBP, a stored no-sell rule, and a 7-day cool-off on changes to "the real-money plan settings", leaving which settings count to the builder.
**Decision:**
- Real-money plan settings are every current `[plan]` key, in four parts (`config.PLAN_PARTS`): fund; monthly; the platform (type, percentage fee, cap, flat fee, dealing fee, FX fee, as one part); the no-sell rule. Keys added to `[plan]` later are not cooled unless added to a part.
- A part still at its defaults is filled at once (first set-up does not wait a week). Any change to a set part, clearing included, is pending for 7 days. One pending change at a time: a new change joins it and restarts the 7 days. A key sent at its current value changes nothing, its pending change included; only Cancel drops a pending change (gate r1: a form sending every field must never undo another field's change). The GUI cards send only edited fields. Every value is checked when asked, and again when applied.
- Pending changes live in the live DB (`m0051 plan_changes`, with history). A due change is applied by whichever reader settles first: every Plan route and plan CLI command that reads `[plan]` (summary, guardrails, drawdowns, fees, contributions, set, rule, pending); no scheduler hook. Settling and asking first reload `[plan]` from oracle.toml, so a CLI write reaches a running GUI before it decides. The Settings tab refuses cooled keys; a hand edit of oracle.toml is not guarded.
- Drawdown windows, each the deepest peak-to-low with both ends inside: 2000-01..2003-06, 2007-06..2009-12, 2020, 2021-11..2023-06. Measured on the plan's fund (02's GBP total-return series after TER); where its proxy starts too late (ACWI 2008-03), the S&P 500 in GBP stands in, labelled. A window no GBP series spans is said, not guessed (2000-02 today: GBP rates start 2003-12).
- The pot is a per-request input, never stored (example £10,000). The monthly effect buys the pot at the peak and pays the `[plan]` monthly at each month's last trading day; "even" is the pot back at the money paid in, beside the price's own recovery. Months are counted from the low.
**Alternatives:** Cool only fund and monthly (a fee or rule change in a panic is the same risk); separate pending rows with their own clocks (harder to read, two changes could land on different days); a stored pot (spec: no amounts); USD series for 2000-02 (not what a GBP investor lived).
