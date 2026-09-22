# 04: Value, quality and growth scores against peers

**What to build:** Three factor analysts emitting `AnalystSignal`s: value (P/E, forward P/E, P/B, dividend yield), quality (profit margin, ROE, debt to equity, lower being better), growth (revenue growth). Each figure becomes a percentile within the peer group; the factor is the average of the percentiles it has. The reason names the peer group and its size ("cheaper than 15 of 19 semiconductor peers on P/E"). `oracle analyse AAPL` includes the factor signals.

**Blocked by:** 01, 03

**Status:** done

- [x] Percentiles are correct on a hand-built peer set, including ties
- [x] Negative P/E (losses) is treated as expensive, not cheap
- [x] A figure missing for the company is skipped; a factor with no figures gives no signal and says why
- [x] Fewer than 5 peers with a figure lowers confidence and says so
- [x] Every factor has a plain-English meaning line
