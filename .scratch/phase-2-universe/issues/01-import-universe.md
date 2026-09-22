# 01: Import the universe with `oracle universe refresh`

**What to build:** `oracle universe refresh` downloads FinanceDatabase's equities file itself (no `financedatabase`/pandas dependency), through the shared source gate as a new `financedatabase` source with a conservative rate, and loads it into SQLite in one atomic swap under a job lock. Each listing gets a company key (ISIN when present, else normalised name plus country) and a primary-listing flag, so later tickets can collapse listings to companies. The run records the source commit SHA and refresh time. `oracle universe status` shows listing and company counts, delisted count, source commit and last refresh.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Refresh loads a fixture `.bz2` through an injectable fetcher; no network in tests
- [ ] A failed or partial load leaves the previous universe intact (atomic swap)
- [ ] Two refreshes at once: the second reports the lock holder instead of running
- [ ] Company key: ISIN when present, else normalised name + country; every Apple listing shares one key
- [ ] Primary listing: the listing on an exchange in the company's home country, else the most liquid exchange by a fixed ranking; exactly one per company
- [ ] Download goes through the `financedatabase` source gate; source commit SHA and refresh time recorded
- [ ] `oracle universe status` before any refresh says so plainly
- [ ] `THIRD_PARTY.md` records the FinanceDatabase dataset (MIT)
