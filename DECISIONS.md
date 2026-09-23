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
