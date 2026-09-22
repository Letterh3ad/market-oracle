# 01: Import the universe with `oracle universe refresh`

**What to build:** `oracle universe refresh` downloads FinanceDatabase's equities file itself (no `financedatabase`/pandas dependency), through the shared source gate as a new `financedatabase` source with a conservative rate, and loads it into SQLite in one atomic swap under a job lock. Each listing gets a company key (listings sharing an ISIN or a normalised name plus country are one company) and a primary-listing flag, so later tickets can collapse listings to companies. The run records the source commit SHA and refresh time. `oracle universe status` shows listing and company counts, delisted count, source commit and last refresh.

**Blocked by:** None (can start immediately)

**Status:** done

- [x] Refresh loads a fixture `.bz2` through an injectable fetcher; no network in tests
- [x] A failed or partial load leaves the previous universe intact (atomic swap)
- [x] Two refreshes at once: the second reports the lock holder instead of running
- [x] Company key: shared ISIN or normalised name + country, transitively; every Apple listing shares one key
- [x] Primary listing: the listing on an exchange in the company's home country, else the most liquid exchange by a fixed ranking; exactly one per company
- [x] Download goes through the `financedatabase` source gate; source commit SHA and refresh time recorded
- [x] `oracle universe status` before any refresh says so plainly
- [x] `THIRD_PARTY.md` records the FinanceDatabase dataset (MIT)
