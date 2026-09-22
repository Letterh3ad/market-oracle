# 04: Update the watchlist's prices

**What to build:** `oracle prices update` with no symbol updates every watched listing and the FX they need, under a job lock. One failing symbol is reported and the rest carry on; the command exits non-zero if any failed. `oracle watch show` adds last close (native and GBP), day change and a "stale" marker when the last bar is older than the exchange's last trading day allows.

**Blocked by:** 01, 03

**Status:** ready-for-agent

- [ ] Updates every listing on every watchlist once, even if on several lists
- [ ] A failing symbol does not stop the others; the summary names it and why
- [ ] Two updates at once: the second reports the lock holder
- [ ] `watch show` prices in native currency and GBP, with day change and staleness
