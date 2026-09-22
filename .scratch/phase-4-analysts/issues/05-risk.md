# 05: Per-listing risk

**What to build:** A risk analyst per listing: a suggested position size in GBP scaled by volatility (a listing at the target volatility gets the full 10% cap of the £10,000 paper account; more volatile ones scale down proportionally; the 20% cash floor is respected), the listing's maximum drawdown over the stored history, and its correlation with each other watchlisted listing (daily returns in GBP). Portfolio-level risk waits for the phase 5 ledgers. `oracle analyse AAPL` shows it.

**Blocked by:** 01

**Status:** done

- [x] Size halves when volatility doubles, and never exceeds the cap
- [x] Max drawdown matches a hand-built series, with its peak and trough dates
- [x] Correlation uses only days both listings traded, in GBP
- [x] A watchlist with one listing gives no correlation and says why
- [x] Every default (target volatility, cap, cash floor) is a documented config value
