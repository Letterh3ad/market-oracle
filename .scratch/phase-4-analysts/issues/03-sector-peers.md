# 03: Sector peers with fundamentals

**What to build:** `oracle peers AAPL` shows the company's comparison group: companies in the same industry group (falling back to sector when too few), the biggest first by market-cap bucket, up to `[analysts] peers` (default 20, settable later from the GUI). Peer fundamentals are fetched through the existing fundamentals chain and reused for up to 7 days; the company's own snapshot keeps its daily refresh. Peers are companies, not listings, so one company never appears twice.

**Blocked by:** None (can start immediately)

**Status:** done

- [x] The peer list excludes the company itself and funds
- [x] A sparse industry group falls back to the sector, and says so
- [x] A second run within 7 days makes no fundamentals requests
- [x] A peer whose fundamentals fail is listed as missing, and the rest still load
- [x] `[analysts] peers` changes the group size; values outside 5..100 are refused
