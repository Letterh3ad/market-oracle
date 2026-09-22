# 06: Signals in the watchlist and company view

**What to build:** `oracle analyse` with no symbol runs every analyst over every watchlisted listing and reports failures per listing without stopping. `oracle watch show` gains a short signal summary column (combined technicals and factors). `oracle company AAPL` shows the analyst breakdown: each signal's direction, score, confidence and reason, with its as-of date.

**Blocked by:** 02, 04, 05

**Status:** done

- [x] One listing failing never stops the rest; the command exits non-zero and names it
- [x] Stale signals (older than the latest bar) are marked
- [x] `company` with no stored signals says how to create them
- [x] Reads make no requests; only `analyse` fetches peer fundamentals
