# 01: Daily bars for a listing from Yahoo

**What to build:** `oracle prices update AAPL` fetches daily bars from Yahoo's chart endpoint through the gate (source `yahoo`): five years on first fetch, then only bars since the last stored one. Bars are stored in the listing's native currency with sub-units normalised (GBp, ZAc, ILA divided by 100) and dated in the exchange's local calendar (`tzdata`). Each bar records its source. `oracle prices show AAPL [--days N]` prints recent bars. Sources sit behind a `PriceSource` protocol and an ordered `[prices] sources` list in `oracle.toml`, Yahoo only for now.

**Blocked by:** None (can start immediately)

**Status:** done

- [x] First fetch stores ~5y of bars; a re-run fetches and stores only newer ones
- [x] London prices in pence are stored in pounds; the stored currency is GBP
- [x] A bar's date is the exchange's local trading date, across DST changes
- [x] Adjusted close stored alongside close
- [x] A symbol Yahoo does not know, or an HTTP failure, is a clear error that stores nothing
- [x] Symbols not in the universe are refused with suggestions
- [x] `tzdata` recorded in THIRD_PARTY.md
