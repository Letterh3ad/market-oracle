# 02: RSI, MACD and volatility analysts

**What to build:** Three more technical analysts on the same `AnalystSignal`: RSI (14-day; overbought and oversold readings explained), MACD (12/26/9; crossovers and their age), and volatility (20-day annualised against the listing's own past year, high volatility lowering confidence rather than implying direction). `oracle analyse AAPL` prints all technical signals plus a combined technicals line (a confidence-weighted average, rule documented).

**Blocked by:** 01

**Status:** done

- [x] Each indicator matches an independent reference: RSI StockCharts' published worked example (to its 2 published decimals), MACD and volatility their closed forms
- [x] Oversold RSI reads as a possible bounce, not a buy instruction
- [x] A MACD crossover reports how many days ago it happened
- [x] Gaps in bars (holidays) never break the indicators
- [x] The combined line shows which analysts it combined and how
