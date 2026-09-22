# 02: Search by name or symbol

**What to build:** `oracle universe search "apple"` returns ranked matches over name and symbol via SQLite FTS5, one row per company showing its primary listing (symbol, name, country, exchange, sector, market cap band). `--all-listings` shows every listing instead. Delisted listings are hidden unless `--include-delisted`. An exact symbol match (`AAPL`, `APC.DE`) ranks first.

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] "apple" finds Apple Inc. once by default, its primary listing AAPL
- [ ] `--all-listings` shows every Apple listing
- [ ] Exact symbol match ranks above name matches
- [ ] Delisted hidden by default, shown with `--include-delisted`
- [ ] Query punctuation (quotes, `&`, `-`, `.`) never raises an FTS syntax error
- [ ] Searching before any refresh tells the user to run `oracle universe refresh`
