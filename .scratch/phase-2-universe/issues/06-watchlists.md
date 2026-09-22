# 06: Watchlists of listings

**What to build:** Named watchlists holding specific listings (the thing phase 3 prices). `oracle watch add <symbol> [--list NAME]`, `oracle watch remove`, `oracle watch show [NAME]`, `oracle watch lists`. The default list is `main`. Show prints each listing with name, exchange, currency and country, and flags any symbol no longer in the universe after a refresh.

**Blocked by:** 02

**Status:** done

- [x] Add, remove and show round-trip; adding a symbol twice is a no-op
- [x] Lists are created on first add; removing the last member keeps the list
- [x] Watching one listing does not watch the company's other listings
- [x] A symbol missing after refresh stays on the list, flagged as missing
- [x] Unknown symbol on add gives a clear error, with search suggestions
