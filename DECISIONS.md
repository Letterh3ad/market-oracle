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
