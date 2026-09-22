# 02: Search by name or symbol

**What to build:** `oracle universe search "apple"` returns ranked matches over name and symbol via SQLite FTS5, one row per company showing its primary listing (symbol, name, country, exchange, sector, market cap band). `--all-listings` shows every listing instead. Delisted listings are hidden unless `--include-delisted`. An exact symbol match (`AAPL`, `APC.DE`) ranks first and is the line shown. Then larger market-cap bands rank first, then FTS score.

**Blocked by:** 01

**Status:** done

- [x] "apple" finds Apple Inc. once by default, its primary listing AAPL
- [x] `--all-listings` shows every Apple listing
- [x] Exact symbol match ranks above name matches
- [x] Delisted hidden by default, shown with `--include-delisted`
- [x] Query punctuation (quotes, `&`, `-`, `.`) never raises an FTS syntax error
- [x] Searching before any refresh tells the user to run `oracle universe refresh`
