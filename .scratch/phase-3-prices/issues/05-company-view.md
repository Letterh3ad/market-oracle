# 05: Company view with fundamentals

**What to build:** `oracle company AAPL` shows the company's profile (universe fields, listings count, tags), latest price in native currency and GBP, and key fundamentals: market cap (GBP), P/E, forward P/E, P/B, dividend yield, profit margin, ROE, revenue growth, debt/equity, beta, each with a one-line plain-English meaning. Fundamentals come from Yahoo's quoteSummary (cookie and crumb, re-fetched once when rejected), EODHD fundamentals as the keyed fallback, stored as dated snapshots and refreshed at most daily.

**Blocked by:** 01, 03

**Status:** done

- [x] First view fetches and stores a snapshot; a second view the same day makes no request
- [x] An expired crumb is refreshed once, then the call fails clearly
- [x] Missing figures show as "n/a", never as 0
- [x] With Yahoo failing and a key set, EODHD fills the snapshot
- [x] Each figure has a plain-English explanation line

## Comments

Fundamentals are fetched for the company's primary listing. EODHD has no debt/equity figure, so it shows n/a from that source. The crumb calls use a browser user agent (see DECISIONS.md).
